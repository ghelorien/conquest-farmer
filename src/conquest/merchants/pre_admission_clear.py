"""Explicit, no-input reconciliation for an unpublished Market handoff."""

from copy import deepcopy
import hashlib
import json
import time

from conquest.discord_notify import process_alive, read_json


PREVIEW = "delivery-stale-pre-admission-preview"
CLEAR = "delivery-stale-pre-admission-clear"
TERMINAL = "pre_admission_stale_cleared"


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _idle(ui):
    control = ui.app.control.snapshot()
    if (
        control.get("enabled")
        or control.get("paused")
        or ui.coordinator.owner
        or ui.coordinator.manual_active()
        or ui.coordinator.manual_session_blocked("Farmer")
        or ui.app.mouse_priority.active()
        or getattr(ui.app, "closing", False)
        or getattr(ui.app, "thread", None)
        and ui.app.thread.is_alive()
        or getattr(ui, "grant", None)
        or getattr(getattr(ui, "grant_fence", None), "active", None)
        or any(
            getattr(ui.runtime, name, None)
            for name in ("handoff", "delivery_window", "refill_window")
        )
        or getattr(ui, "delivery_admissions", None)
        or any(
            worker.is_alive() for worker in getattr(ui, "delivery_workers", {}).values()
        )
    ):
        raise ValueError("Stale handoff clear requires stopped, unowned native input")
    statuses = ui.runtime.status()
    if any(
        state.get("pending")
        or state.get("input_active")
        or state.get("manual_input_fence")
        or (state.get("refill") or {}).get("pending")
        or ui.coordinator.manual_session_blocked(name)
        for name, state in statuses.items()
    ):
        raise ValueError("Stale handoff clear requires idle merchants")
    return control, statuses


def _row(ui, *, permit_cleared=False):
    from conquest import merchant_loop_acceptance as acceptance
    from conquest.merchants import (
        delivery_bridge,
        delivery_journey,
        delivery_route,
        service_retry,
    )
    from conquest.merchants.delivery import exact_items
    from conquest.merchants.handoff import WorkWindows
    from conquest.merchants.service_visit import MarketVisit

    row = acceptance.state()
    cycle = row.get("active") or {}
    visit = read_json(MarketVisit().path)
    if (
        not acceptance.trial_permitted()
        or cycle.get("phase") != "town"
        or cycle.get("admissions")
        or row.get("farmer_profile_id") != visit.get("farmer_profile_id")
        or cycle.get("town_visit_id") != visit.get("town_visit_id")
        or visit.get("phase") != "active"
        or not isinstance(visit.get("deadline"), (int, float))
        or time.time() < visit["deadline"]
    ):
        raise ValueError(
            "Stale handoff clear requires the exact expired unadmitted acceptance visit"
        )
    route = read_json(delivery_route.STATE)
    if route.get("active") or route.get("cleanup_pending"):
        raise ValueError("Stale handoff clear requires no active route operation")
    journey = read_json(delivery_journey.JOURNAL)
    if (
        journey.get("phase") != "market"
        or journey.get("acceptance_scope") != acceptance.journey_scope()
        or any(
            journey.get(name)
            for name in (
                "deposit_pending",
                "receipts",
                "scroll_withdrawal",
                "scroll_withdrawal_receipt",
                "scroll_delivery_receipts",
                "loose_meteor_pending",
            )
        )
    ):
        raise ValueError(
            "Stale handoff clear requires unchanged acceptance journey ownership"
        )
    route_status = read_json(service_retry.ROUTE_STATUS)
    if (
        not service_retry.ROUTE_STOP.exists()
        or route_status.get("route") != row.get("route_id")
        or route_status.get("town_visit_id") != cycle.get("town_visit_id")
        or route_status.get("phase")
        not in ("failed", "stopped", "completed", "needs_attention")
        or process_alive(route_status.get("pid")) is not False
    ):
        raise ValueError(
            "Stale handoff clear requires the original native worker to be exited"
        )
    control, statuses = _idle(ui)
    window = WorkWindows().state()
    records = cycle.get("pre_admission_stale_clears", [])
    if not isinstance(records, list) or any(
        not isinstance(record, dict) for record in records
    ):
        raise ValueError("Stale handoff clear audit is malformed")
    if (
        not isinstance(window.get("request_id"), str)
        or not window["request_id"].startswith("route-delivery:")
        or window.get("visit_id") != visit["visit_id"]
        or window.get("town_visit_id") not in (None, visit.get("town_visit_id"))
        or window.get("farmer_profile_id") != row.get("farmer_profile_id")
        or window.get("deadline") != visit.get("deadline")
    ):
        raise ValueError(
            "Stale handoff reservation does not match the exact acceptance visit"
        )
    prior = next(
        (
            record
            for record in records
            if record.get("request_id") == window["request_id"]
        ),
        None,
    )
    terminalized = window.get("phase") == TERMINAL
    if terminalized and permit_cleared:
        if not prior or prior.get("reservation") is None:
            raise ValueError("Cleared stale handoff lacks its durable audit")
        reservation = prior["reservation"]
    elif window.get("phase") == "preparing":
        if prior and prior.get("reservation") != window:
            raise ValueError("Stale handoff reservation changed after its audit")
        reservation = deepcopy(window) if not prior else prior["reservation"]
    else:
        raise ValueError("Market input window is not an unpublished stale reservation")
    if (
        reservation.get("phase") != "preparing"
        or reservation.get("request_id") != window["request_id"]
        or not isinstance(reservation.get("deadline"), (int, float))
        or time.time() < reservation["deadline"]
    ):
        raise ValueError("Stale handoff reservation is not expired preparing work")
    service_retry._unadmitted_journals(ui, visit, visit_ids=())
    source = delivery_bridge.dispatch(ui, {"action": "delivery-source"})["farmer"]
    inventory = acceptance.source_checked(source, row)
    wanted = exact_items([cycle["item"]])
    if (
        not acceptance.deliverable_item(cycle["item"])
        or source.get("map_id") != 1036
        or any(inventory.get(uid) != value for uid, value in wanted.items())
    ):
        raise ValueError("Stale handoff clear requires the exact eligible carried item")
    if any(not state.get("snapshot") for state in statuses.values()):
        raise ValueError("Stale handoff clear requires fresh merchant memory")
    if any(
        (state.get("snapshot") or {}).get(name)
        for state in statuses.values()
        for name in ("trade", "request")
    ):
        raise ValueError("Stale handoff clear requires closed merchant trade windows")
    if ui.app.control.snapshot() != control:
        raise ValueError("Farmer control changed while checking stale handoff evidence")
    merchants = {
        name: {
            field: snapshot.get(field)
            for field in ("identity", "character", "character_uid", "trade", "request")
        }
        for name, state in statuses.items()
        for snapshot in [state["snapshot"]]
    }
    evidence = {
        "request_id": window["request_id"],
        "reservation": reservation,
        "visit": visit,
        "run_id": row["run_id"],
        "cycle_id": cycle["cycle_id"],
        "item": cycle["item"],
        "control_revision": control.get("revision"),
        "route_status": {
            field: route_status.get(field) for field in ("pid", "started_at", "phase")
        },
        "farmer": {
            "identity": source["identity"],
            "character": source["character"],
            "character_uid": source["character_uid"],
            "server": source["server"],
            "inventory": inventory,
            "silver": source.get("silver"),
        },
        "merchants": merchants,
    }
    return row, cycle, window, prior, evidence, terminalized


def preview(ui):
    row, cycle, window, prior, evidence, terminalized = _row(ui, permit_cleared=True)
    digest = _digest(evidence)
    if prior and prior.get("preview_digest") != digest:
        raise ValueError("Cleared stale handoff evidence no longer matches its audit")
    return {
        "eligible": not bool(prior),
        "cleared": bool(prior and terminalized),
        "preview_digest": digest,
        "request_id": window["request_id"],
        "visit_id": evidence["visit"]["visit_id"],
        "item": deepcopy(cycle["item"]),
        "item_label": cycle["item"].get("name"),
        "item_uid": cycle["item"].get("uid"),
        "reservation_deadline": evidence["reservation"]["deadline"],
        "expired_at": evidence["reservation"]["deadline"],
    }


def clear(ui, *, preview_digest, confirmation_reference, operator):
    if (
        not isinstance(preview_digest, str)
        or preview_digest != confirmation_reference
        or not isinstance(operator, str)
        or not operator
    ):
        raise ValueError("Exact stale handoff preview confirmation is required")
    row, cycle, window, prior, evidence, terminalized = _row(ui, permit_cleared=True)
    digest = _digest(evidence)
    if digest != preview_digest:
        raise ValueError(
            "Stale handoff evidence changed; preview again before clearing"
        )
    if prior and terminalized:
        return {
            "cleared": True,
            "idempotent": True,
            "request_id": window["request_id"],
            "visit_id": evidence["visit"]["visit_id"],
            "preview_digest": digest,
        }
    if prior:
        audit = prior
    else:
        audit = {
            "request_id": window["request_id"],
            "preview_digest": digest,
            "reservation": deepcopy(evidence["reservation"]),
            "visit_id": evidence["visit"]["visit_id"],
            "town_visit_id": evidence["visit"].get("town_visit_id"),
            "farmer_profile_id": row["farmer_profile_id"],
            "item": deepcopy(cycle["item"]),
            "operator": operator,
            "sealed_at": time.time(),
        }
        from conquest import merchant_loop_acceptance as acceptance

        def seal(current):
            if current != row:
                raise ValueError("Acceptance changed while sealing stale handoff clear")
            active = current.get("active") or {}
            records = active.setdefault("pre_admission_stale_clears", [])
            if records:
                raise ValueError("A stale handoff clear audit already exists")
            records.append(deepcopy(audit))
            return current

        # SQLite FULL-synchronous acceptance audit precedes the terminal file mark.
        acceptance.update("pre_admission_stale_handoff_clear_sealed", seal)
    from conquest.merchants.handoff import WorkWindows

    closed = WorkWindows().finish(TERMINAL, pre_admission_stale_clear=deepcopy(audit))
    if (
        closed.get("phase") != TERMINAL
        or closed.get("request_id") != audit["request_id"]
        or closed.get("pre_admission_stale_clear") != audit
    ):
        raise ValueError("Stale handoff terminal mark did not persist")
    return {
        "cleared": True,
        "idempotent": False,
        "request_id": audit["request_id"],
        "visit_id": audit["visit_id"],
        "preview_digest": digest,
    }


def dispatch(ui, body):
    action = body.get("action") if isinstance(body, dict) else None
    if action == PREVIEW and set(body) == {"action"}:
        with ui.coordinator.lock, ui.runtime.lock:
            return preview(ui)
    if action == CLEAR and set(body) == {
        "action",
        "preview_digest",
        "confirmation_reference",
        "operator",
    }:
        with ui.coordinator.lock, ui.runtime.lock:
            return clear(
                ui,
                preview_digest=body["preview_digest"],
                confirmation_reference=body["confirmation_reference"],
                operator=body["operator"],
            )
    raise ValueError("Unsupported stale pre-admission handoff command")
