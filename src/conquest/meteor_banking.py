"""Reusable ten-Meteor consolidation, using verified dialog and item receipts."""

from conquest.character_context import installation_path, state_path
import time
from pathlib import Path
from conquest.discord_notify import read_json, write_json
import json
import hashlib
import os
import threading
from contextlib import contextmanager

POLICY = Path("profiles/meteor-banking.json")
JOURNAL = Path(state_path("reports/banking/meteor-consolidation.json"))
AUDIT = Path(state_path("reports/banking/meteor-consolidation-audit.jsonl"))
METEOR = 1088001
SCROLL = 720027
DELIVERY_RETRY_SECONDS = 900


def _promoted_scroll_delivery(consolidation, uid, *, evidence=False):
    """Recognize actual staged transfer evidence without inventing route receipts."""
    from types import SimpleNamespace
    import math
    from conquest.character_context import current
    from conquest.memory_build_layout import CLIENT_SHA256_1078
    from conquest.merchants.farmer_qualification import qualification_path
    from conquest.merchants.trade_driver_1078 import (
        validate_qualification,
        receipt_digest,
    )

    context = current()
    if context is None or context.profile.role != "Farmer":
        return False
    try:
        observer = SimpleNamespace(
            character=context.profile.name,
            adapter=SimpleNamespace(expected_sha256=CLIENT_SHA256_1078),
        )
        profile = read_json(qualification_path(observer, migrate=False))
        if (
            profile.get("profile_id") != context.profile.id
            or profile.get("character_uid") != context.profile.character_uid
            or profile.get("client_sha256") != CLIENT_SHA256_1078
            or profile.get("capabilities", {}).get("farmer_delivery") is not True
        ):
            return False
        validate_qualification(profile, "farmer_delivery", context.profile.name)
        receipt = profile["trade_receipt_1078"]
        intent = receipt["intent"]
        items = intent["items"]
        if (
            receipt.get("farmer_profile_id") != context.profile.id
            or len(items) != 1
            or items[0].get("uid") != uid
            or items[0].get("type_id") != SCROLL
            or items[0].get("quantity") != 1
            or items[0].get("bound") is not False
            or any(items[0].get(key) != 0 for key in ("plus", "gem1", "gem2"))
        ):
            return False
        for snapshot in (intent["farmer"], receipt["farmer_after"]):
            if (
                snapshot.get("character") != context.profile.name
                or snapshot.get("character_uid") != context.profile.character_uid
                or snapshot.get("server") != context.profile.server
            ):
                return False
        stored_at = consolidation.get("market_verified_at")
        verified_at = receipt.get("verified_at")
        stamps = [
            stored_at,
            verified_at,
            intent["farmer"].get("timestamp"),
            intent["merchant"].get("timestamp"),
            receipt["farmer_after"].get("timestamp"),
            receipt["merchant_after"].get("timestamp"),
        ]
        if (
            any(
                type(t) not in (int, float) or not math.isfinite(t) or t <= 0
                for t in stamps
            )
            or not stored_at <= verified_at <= time.time()
            or any(not stored_at <= t <= verified_at for t in stamps[2:])
        ):
            return False
        # Promotion archives the exact canonical receipt before writing the
        # qualification. Read our profile's archive, never an arbitrary path
        # named by the document (packaged and physical roots may be aliases).
        digest = receipt_digest(receipt)
        if Path(profile.get("evidence", "")).name != digest + ".json":
            return False
        archive = Path(state_path("reports/merchants/delivery-request-probe-audit")) / (
            digest + ".json"
        )
        raw = archive.read_bytes()
        valid = hashlib.sha256(raw).hexdigest() == digest and json.loads(raw) == receipt
        return receipt if valid and evidence else valid
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False


def completed_stored_scroll():
    """Return delivery intent only; fresh Market memory remains withdrawal authority."""
    from conquest import stored_scroll_queue as queue

    current = read_json(JOURNAL)
    if current.get("phase") not in TERMINAL:
        return None
    queue.capture(JOURNAL, current)
    from conquest.merchants.delivery_route import STATE as delivery_state

    receipts = read_json(delivery_state).get("receipts", [])
    for row in queue.pending(JOURNAL):
        uid = row["uid"]
        state = row["evidence"]
        delivered = next(
            (
                r
                for r in reversed(receipts)
                if r.get("outcome") == "transferred"
                and r.get("proof_digest")
                and any(
                    i.get("uid") == uid and i.get("type_id") == SCROLL
                    for i in r.get("items", [])
                )
            ),
            None,
        )
        if delivered:
            queue.complete(JOURNAL, uid, delivered)
            continue
        promoted = _promoted_scroll_delivery(state, uid, evidence=True)
        if promoted:
            queue.complete(JOURNAL, uid, promoted)
            continue
        if (
            row["deferred_at"] is not None
            and time.time() - row["deferred_at"] < DELIVERY_RETRY_SECONDS
        ):
            continue
        return uid
    return None


def defer_stored_scroll(uid):
    """Back off one exact, freshly re-banked scroll without losing intent."""
    state = read_json(JOURNAL)
    from conquest import stored_scroll_queue as queue

    if type(uid) is not int or uid <= 0:
        raise ValueError("Deferred scroll UID is invalid")
    queue.capture(JOURNAL, state)
    now = time.time()
    queue.defer(JOURNAL, uid, now)
    if state.get("scroll_uid") == uid:
        state["delivery_deferred"] = {"uid": uid, "at": now}
        write_json(JOURNAL, state)


def batch(items):
    meteors = [
        i for i in items if i["type_id"] == METEOR and i["amount"] == i["limit"] == 1
    ]
    if len({i["uid"] for i in meteors}) != len(meteors):
        raise ValueError("Meteor batch contains duplicate inventory/warehouse IDs")
    return meteors[:10] if len(meteors) >= 10 else []


def exchange_received(before, after, fee=0):
    identity = lambda i: (
        i["uid"],
        i["type_id"],
        i["amount"],
        i["limit"],
        i.get("plus"),
    )
    old = {i["uid"]: i for i in before["items"]}
    new = {i["uid"]: i for i in after["items"]}
    removed = [i for uid, i in old.items() if uid not in new]
    added = [i for uid, i in new.items() if uid not in old]
    return (
        len(removed) == 10
        and all(
            i["type_id"] == METEOR and i["amount"] == i["limit"] == 1 for i in removed
        )
        and len(added) == 1
        and added[0]["type_id"] == SCROLL
        and added[0]["amount"] == 1
        and all(
            identity(old[uid]) == identity(new[uid]) for uid in old.keys() & new.keys()
        )
        and before["silver"] - after["silver"] == fee
        and before.get("equipped_ammo") == after.get("equipped_ammo")
    )


def select_saved_dialog(loop, name, step):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        loop.check_stop()
        data = loop.town("service-dialog")
        if data["records"] == step["records"]:
            from conquest.dialog_geometry import scroll_direction

            if scroll_direction(data, step["option"], data.get("viewport")):
                loop.town(
                    "service-scroll-dialog",
                    name=name,
                    records=data["records"],
                    option=step["option"],
                )
                time.sleep(0.15)
                continue
            return loop.town(
                "service-select",
                name=name,
                option=step["option"],
                records=data["records"],
            )
        time.sleep(0.1)
    raise ValueError("Saved Meteor route dialog changed; no choice sent")


def open_saved_service(loop, name, step):
    from conquest.worker import request

    # Opening an NPC can first walk into interaction range. Retry only that
    # non-transactional opening, never a selected option, exchange or fare.
    for attempt in range(3):
        loop.town("service-open", name=name)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            loop.living()
            try:
                data = request(loop.info, "town", {"action": "service-dialog"})
            except ValueError as error:
                if not any(
                    t in str(error)
                    for t in ("not active", "absent", "changed during observation")
                ):
                    raise
            else:
                if data["records"] == step["records"]:
                    return
                raise ValueError("Saved Meteor route dialog changed; no choice sent")
            time.sleep(0.1)
        loop.record(
            "service_open_retry",
            npc=name,
            attempt=attempt + 1,
            activity="Reopening " + name + " after approaching interaction range",
        )
    raise ValueError("Service dialog did not open; no transaction issued")


def trip(loop, plan, *, before_submit=None):
    from conquest.navigation import read_terrain

    life = loop.living()["embedded_controls"]["life"]
    if life["map_id"] != plan["source_map"]:
        raise ValueError("Meteor route source map changed")
    if life["map_id"] == 1036 and plan["destination_map"] != 1036:
        from conquest.town_trade import stash_candidate

        if any(stash_candidate(item) for item in loop.town("supplies")["items"]):
            raise ValueError(
                "Stay in Market: deposit all protected valuables before returning to town"
            )
    if loop.terrain.map_id != life["map_id"]:
        loop.terrain = read_terrain(
            installation_path(r"C:\Program Files\Classic Conquer 2.0"), life["map_id"]
        )
    # The warehouse frontage rejects short walking clicks. Leave through the
    # observed clear eastbound jump before planning toward the controller.
    # This is an intermediate waypoint, so do not chase a one-tile offset.
    if life["map_id"] == 1036 and plan["destination_map"] != 1036:
        position = life["position"]
        if max(abs(a - b) for a, b in zip(position, (186, 188))) <= 4:
            # The live exit landed at (191,186); chasing (194,184) from
            # there hit the frontage again despite already leaving the bank.
            loop.travel(
                (191, 186),
                activity="Leaving Market warehouse toward the city transport",
                arrival_radius=2,
            )
    loop.record("meteor_travel", activity=plan["activity"])
    # The Market exit does not reliably open at the generic twelve-tile
    # service distance. Reach its qualified approach before clicking it.
    market_exit = life["map_id"] == 1036 and plan["destination_map"] != 1036
    if market_exit:
        current = loop.living()["embedded_controls"]["life"]["position"]
        if current[0] >= 239 and current[1] < 200:
            # Merchant booths occupy the northeast diagonal to the exit.
            # The central aisle was traversed during the delivery qualification.
            loop.travel(
                (225, 206),
                activity="Leaving merchant booths through the central Market aisle",
                arrival_radius=2,
            )
            current = loop.living()["embedded_controls"]["life"]["position"]
        if max(abs(a - b) for a, b in zip(current, plan["approach"])) > 4:
            waypoints = plan.get("waypoints", [])
            if waypoints:
                nearest = min(
                    range(len(waypoints)),
                    key=lambda i: max(
                        abs(a - b) for a, b in zip(current, waypoints[i])
                    ),
                )
                waypoints = waypoints[nearest:]
            for point in waypoints:
                loop.travel(tuple(point), activity=plan["activity"], arrival_radius=2)
    loop.travel(
        tuple(plan["approach"]),
        activity=plan["activity"],
        service_name=None if market_exit else plan["npc"],
    )
    service = loop.town("service-locate", name=plan["npc"])
    if service["identity"] != plan["identity"]:
        raise ValueError("Meteor route NPC identity changed")
    open_saved_service(loop, plan["npc"], plan["dialogs"][0])
    before = loop.town("supplies")
    if life["map_id"] == 1036 and plan["destination_map"] != 1036:
        if any(stash_candidate(item) for item in before["items"]):
            raise ValueError("Stay in Market: valuables appeared before departure")
    if before_submit:
        before_submit()
    for step in plan["dialogs"]:
        select_saved_dialog(loop, plan["npc"], step)
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        data = loop.health()["embedded_controls"]
        fresh = data.get("life")
        if (
            fresh
            and not fresh["dead_candidate"]
            and fresh["object_address"] == life["object_address"]
            and fresh["map_id"] == plan["destination_map"]
            and 0 <= time.time() - data.get("observed_at", 0) <= 1
        ):
            after = loop.town("supplies")
            if before["silver"] - after["silver"] != plan["fare"]:
                raise ValueError("Meteor route fare was not verified")
            loop.terrain = read_terrain(
                installation_path(r"C:\Program Files\Classic Conquer 2.0"),
                fresh["map_id"],
            )
            from conquest.merchants.service_visit import MarketVisit

            MarketVisit().departed(fresh["map_id"])
            return
        time.sleep(0.1)
    raise ValueError("Meteor route arrival unverified; no repeat payment issued")


PENDING = {
    "withdrawing",
    "travelling",
    "exchange_ready",
    "exchange_pending",
    "storing_scroll",
    "stored_in_market",
    "returning",
    "carried_in_market",
}
TERMINAL = {"completed", "operator_overridden"}
_AUDIT_THREAD_LOCK = threading.RLock()


def pending():
    from conquest.recovery_override import read_recovered

    return read_recovered(JOURNAL).get("phase") in PENDING


def _intent_path():
    """The durable fence between archiving a terminal record and replacing it."""
    return Path(str(JOURNAL) + ".archive-intent.json")


def _generic_audit_path():
    # Older builds used recovery_override's default audit beside the journal.
    # Keep it as evidence, but do not use it as a source of a future plan.
    return Path(str(JOURNAL) + ".audit.jsonl")


def _durable_json(path, value):
    write_json(path, value)
    with Path(path).open("r+b") as stream:
        stream.flush()
        os.fsync(stream.fileno())


@contextmanager
def _audit_lock():
    """Serialize audit de-duplication across threads and Meteor processes."""
    lock_path = Path(str(AUDIT) + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with _AUDIT_THREAD_LOCK:
        with lock_path.open("a+b") as stream:
            stream.write(b"0")
            stream.flush()
            stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                stream.seek(0)
                if os.name == "nt":
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _audit_rows(path):
    path = Path(path)
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise ValueError(f"Meteor audit cannot be read: {path}") from error
    rows = []
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError as error:
            raise ValueError(
                f"Meteor audit has malformed JSON at {path}:{number}"
            ) from error
        if not isinstance(row, dict):
            raise ValueError(f"Meteor audit has a non-record entry at {path}:{number}")
        rows.append(row)
    return rows


def _append_canonical_audit(record):
    """Append one content-addressed record and fsync it before journal replacement."""
    key = record["archive_key"]
    with _audit_lock():
        if any(row.get("archive_key") == key for row in _audit_rows(AUDIT)):
            return False
        AUDIT.parent.mkdir(parents=True, exist_ok=True)
        with AUDIT.open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
            )
            stream.flush()
            os.fsync(stream.fileno())
        return True


def _import_generic_override_audit():
    """Preserve pre-Wave-1 generic override evidence in the canonical audit."""
    source = _generic_audit_path()
    if source.resolve() == AUDIT.resolve() or not source.exists():
        return
    for row in _audit_rows(source):
        source_digest = hashlib.sha256(
            json.dumps(row, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        _append_canonical_audit(
            {
                "record_type": "generic_override_import",
                "archive_key": "generic-override:" + source_digest,
                "source_audit": str(source),
                "record": row,
            }
        )


def _archive_terminal_state(state):
    """Durably retain the *entire* terminal journal, exactly once."""
    _import_generic_override_audit()
    digest = hashlib.sha256(
        json.dumps(state, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    _append_canonical_audit(
        {
            "record_type": "meteor_terminal_journal",
            "archive_key": "terminal-journal:" + digest,
            "journal_digest": digest,
            "journal": state,
        }
    )


def _recover_archive_boundary():
    """Finish a crashed archive/replacement sequence without trusting old state."""
    intent_path = _intent_path()
    intent = read_json(intent_path)
    if not intent:
        return read_json(JOURNAL)
    terminal = intent.get("terminal_journal")
    if not isinstance(terminal, dict):
        raise ValueError("Meteor archive intent is unreadable; no replacement issued")
    _archive_terminal_state(terminal)
    current = read_json(JOURNAL)
    expected = intent.get("terminal_digest")
    current_digest = hashlib.sha256(
        json.dumps(current, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    # If the journal was already replaced, the archive was fsynced first.  It
    # is now safe to clear only the intent, never to restore historical IDs.
    if current_digest != expected:
        intent_path.unlink(missing_ok=True)
    return current


def _replace_overridden_journal(new_state):
    """Archive an overridden operation before atomically starting a new one.

    Leaving the intent until after the replacement makes every crash point
    retryable: either the terminal journal remains to be archived, or the new
    journal remains and the already-fsynced archive is simply de-duplicated.
    """
    current = _recover_archive_boundary()
    if current.get("phase") != "operator_overridden":
        raise ValueError("Meteor journal is not an overridden terminal operation")
    terminal_digest = hashlib.sha256(
        json.dumps(current, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    _durable_json(
        _intent_path(),
        {"terminal_digest": terminal_digest, "terminal_journal": current},
    )
    _archive_terminal_state(current)
    _durable_json(JOURNAL, new_state)
    _intent_path().unlink(missing_ok=True)


def fresh_replan(loop):
    """Start over from current memory observations after an operator override."""
    state = _recover_archive_boundary()
    if state.get("phase") != "operator_overridden":
        return False
    # consolidate deliberately rereads both bag and warehouse; this argument
    # is only retained for the historical public signature.
    return consolidate(loop, None)


def recheck(loop):
    """Read fresh memory-backed state for a Meteor hold without input."""
    life = loop.living()["embedded_controls"]["life"]
    supplies = loop.town("supplies")
    warehouse = loop.town("warehouse-items")
    evidence = {
        "observed_at": time.time(),
        "life": life,
        "supplies": supplies,
        "warehouse": warehouse,
    }
    evidence["evidence_digest"] = hashlib.sha256(
        json.dumps(evidence, sort_keys=True).encode()
    ).hexdigest()
    return evidence


def recheck_worker(info_path):
    """Read the Meteor replan inputs through the memory-backed worker only."""
    from conquest.worker import request

    health = request(info_path, "health")
    evidence = {
        "observed_at": time.time(),
        "life": (health.get("embedded_controls") or {}).get("life"),
        "supplies": request(info_path, "town", {"action": "supplies"}),
        "warehouse": request(info_path, "town", {"action": "warehouse-items"}),
    }
    evidence["evidence_digest"] = hashlib.sha256(
        json.dumps(evidence, sort_keys=True).encode()
    ).hexdigest()
    return evidence


def operator_override(
    loop=None,
    *,
    operator_confirmed=False,
    confirmation_reference=None,
    operator=None,
    incident_digest=None,
    fresh_evidence=None,
):
    """Close a Meteor recovery hold after explicit per-incident confirmation.

    This records the complete pre-override journal in an append-only audit
    record and stores only fresh read-only observations as replan evidence. It
    never repeats a fare, exchange, warehouse movement or return trip.
    """
    if operator_confirmed is not True:
        raise ValueError("Operator confirmation is required for this incident")
    if (
        not isinstance(confirmation_reference, str)
        or not confirmation_reference.strip()
    ):
        raise ValueError("A non-empty incident confirmation reference is required")
    if operator is not None and (not isinstance(operator, str) or not operator.strip()):
        raise ValueError("Operator must be a non-empty string when supplied")
    from conquest.recovery_override import evidence_digest

    state = _recover_archive_boundary()
    if (
        state.get("phase") not in PENDING
        and state.get("phase") != "operator_overridden"
    ):
        raise ValueError("No unresolved Meteor recovery hold is active")
    if state.get("phase") == "operator_overridden":
        prior = state.get("operator_override") or {}
        if incident_digest != prior.get("original_evidence_digest"):
            raise ValueError("Exact previewed Meteor incident digest is required")
        if confirmation_reference != prior.get("confirmation_reference"):
            raise ValueError(
                "Incident was already overridden with a different confirmation"
            )
        return state
    preview = evidence_digest(state)
    if not isinstance(incident_digest, str) or incident_digest != preview:
        raise ValueError("Exact previewed Meteor incident digest is required")
    try:
        fresh = recheck(loop) if loop is not None else fresh_evidence
        if not isinstance(fresh, dict):
            raise ValueError(
                "Meteor recheck did not provide fresh bag and warehouse observations"
            )
    except (ValueError, OSError, KeyError, TypeError) as error:
        fresh = {
            "recheck_unavailable": type(error).__name__,
            "reason": "Fresh farmer memory unavailable; resume requires a fresh replan",
        }
    from conquest.recovery_override import operator_override as close

    return close(
        JOURNAL,
        pending_phases=PENDING,
        operator_confirmed=operator_confirmed,
        confirmation_reference=confirmation_reference,
        operator=operator,
        fresh_evidence=fresh,
        audit_path=_generic_audit_path(),
        incident="meteor-consolidation",
        incident_digest=incident_digest,
    )


def save(state, phase=None, **fields):
    state.update(fields)
    if phase:
        state["phase"] = phase
    write_json(JOURNAL, state)


def carried(loop):
    from conquest.town_trade import stash_candidate

    return [i for i in loop.town("supplies")["items"] if stash_candidate(i)]


def approach_market_warehouse(loop, activity):
    life = loop.living()["embedded_controls"]["life"]
    if life["map_id"] != 1036:
        raise ValueError("Market warehouse approach requires Market")
    try:
        loop.town("service-close-panel", window="Dialog")
    except ValueError as error:
        if not any(text in str(error) for text in ("not active", "absent")):
            raise
    if (loop.town("vendor-status", vendor_type=0) or {}).get("reachable"):
        return
    policy = read_json(POLICY)
    target = tuple(policy.get("market_bank_approach", [182, 184]))
    if max(abs(a - b) for a, b in zip(life["position"], target)) <= 2:
        return
    # The old (186,188) approach gets trapped at the warehouse frontage.
    # Retain the verified corridor but accept nearby tiles in this crowd.
    # open_warehouse rechecks the NPC and interaction before any transaction.
    recovered = set()
    pending_points = [
        (tuple(p), activity, 2)
        for p in policy.get("market_bank_waypoints", [[186, 184]]) + [list(target)]
    ]
    while pending_points:
        point, note, radius = pending_points.pop(0)
        try:
            loop.travel(point, activity=note, vendor_type=0, arrival_radius=radius)
        except ValueError as error:
            from conquest.travel_progress import TravelStalled

            fresh = loop.living()["embedded_controls"]["life"]
            movement_stall = (
                isinstance(error, TravelStalled) and error.code == "no_progress"
            )
            if (
                not movement_stall and str(error) != "Town route remains obstructed"
            ) or fresh["map_id"] != 1036:
                raise
            if (loop.town("vendor-status", vendor_type=0) or {}).get("reachable"):
                return
            # Include the eastern frontage at (190,189), where the approach
            # can stall before reaching the old six-tile recovery envelope.
            if max(abs(a - b) for a, b in zip(fresh["position"], (183, 190))) <= 8:
                area = "frontage"
                recovery = [
                    (p, "Taking the western corridor to Market warehouse", 1)
                    for p in ((186, 199), (176, 199), (176, 183))
                ]
                try:
                    loop.town("service-close-panel", window="Inventory")
                except ValueError as close_error:
                    if not any(
                        text in str(close_error) for text in ("not active", "absent")
                    ):
                        raise
            elif max(abs(a - b) for a, b in zip(fresh["position"], (201, 215))) <= 4:
                area = "southern_crossing"
                recovery = [
                    (p, "Taking the western Market aisle to the warehouse", 2)
                    for p in ((189, 215), (189, 203))
                ]
            else:
                raise
            # A crossing recovery must not consume the warehouse's recovery.
            # Each area gets one bounded attempt; failed movement cannot replay
            # deposits or fares because this queue contains movement only.
            if area in recovered:
                raise
            recovered.add(area)
            pending_points = recovery + [(point, note, radius)] + pending_points
        if (loop.town("vendor-status", vendor_type=0) or {}).get("reachable"):
            return


def market_bank(loop, state):
    from conquest.banking import open_warehouse, close_warehouse
    from conquest.storage_halt import request_stop
    from conquest.merchants.delivery_route import (
        market_storage,
        receipt_for,
        warehouse_exhausted,
    )
    from conquest.no_transfer_town_recovery import warehouse_fallback_only

    if not warehouse_fallback_only(loop, state):
        market_storage(loop)
    approach_market_warehouse(
        loop, "Storing valuables in Market before returning to Phoenix"
    )
    open_warehouse(loop)
    while True:
        stored = loop.town("warehouse-items")
        items = carried(loop)
        if warehouse_exhausted(loop, stored, items):
            save(state, "market_full")
            request_stop(loop, stored, items)
        if not items:
            break
        item = items[0]
        receipt = loop.town("warehouse-deposit", uid=item["uid"])
        if receipt.get("verified_in_warehouse") is not True:
            raise ValueError("Market deposit receipt missing; no return issued")
        state.setdefault("receipts", []).append(receipt)
        save(state)
        loop.record(
            "valuable_stored",
            **receipt,
            plus=item.get("plus"),
            activity="Valuable safely stored in Market",
        )
    scroll = state.get("scroll_uid")
    consumed = state.get("user_confirmed_scroll_consumption") or {}
    manually_used = (
        consumed.get("uid") == scroll
        and consumed.get("confirmed") is True
        and consumed.get("source") == "explicit user confirmation"
    )
    moved = state.get("user_confirmed_scroll_transfer") or {}
    manually_moved = (
        moved.get("uid") == scroll
        and moved.get("type_id") == SCROLL
        and moved.get("confirmed") is True
        and moved.get("source") == "explicit user confirmation"
        and moved.get("destination") == "another_character"
        and not any(i["uid"] == scroll for i in stored["items"])
        and not any(i["uid"] == scroll for i in loop.town("supplies")["items"])
    )
    delivered = receipt_for(scroll, SCROLL) if scroll else None
    if (
        scroll
        and not manually_used
        and not manually_moved
        and not delivered
        and not any(
            i["uid"] == scroll and i["type_id"] == SCROLL for i in stored["items"]
        )
    ):
        raise ValueError(
            "Expected MeteorScroll is not in Market storage; no return issued"
        )
    if manually_moved:
        # Operator testimony resolves this historical trip only. It is not a
        # merchant receipt or proof of the other character's current inventory.
        state["scroll_resolution"] = "operator_reported_external_transfer"
    save(state, "stored_in_market", market_verified_at=time.time())
    close_warehouse(loop)


def resume(loop):
    """Reconcile item IDs before continuing an interrupted ten-Meteor trip."""
    state = _recover_archive_boundary()
    if state.get("phase") == "operator_overridden":
        # The terminal record is evidence only.  Never resume it: a new plan
        # is built from freshly observed bag and warehouse contents.
        return fresh_replan(loop)
    if state.get("phase") not in PENDING:
        return False
    policy = read_json(POLICY)
    route = policy.get("origins", {}).get(str(state["origin"]))
    if not route:
        raise ValueError("Meteor trip origin has no verified transport")
    from conquest.banking import open_warehouse, close_warehouse
    from conquest.navigation import read_terrain

    loop.phase = "restocking"
    world = loop.living()["embedded_controls"]["life"]["map_id"]
    loop.terrain = read_terrain(
        installation_path(r"C:\Program Files\Classic Conquer 2.0"), world
    )
    if world == 1036:
        # An interrupted approach may leave the shared confirmation model on
        # Open Booth. Reconcile its exact negative action before any ordinary
        # delivery read, even when the original Market budget has expired.
        from conquest.merchants.open_booth_cancel_1078 import cleanup

        cleanup(loop)
    if world == state["origin"] and state["phase"] == "returning":
        if carried(loop):
            raise ValueError(
                "Protected valuables unexpectedly carried after Market return"
            )
        if state.get("return_submitted_at"):
            before = state.get("return_before")
            after = loop.town("supplies")
            fare = route["return"]["fare"]
            if (
                not isinstance(before, dict)
                or type(fare) is not int
                or fare < 0
                or before.get("silver") - after["silver"] != fare
                or any(
                    before.get(key) != after.get(key)
                    for key in ("items", "equipped_ammo", "capacity")
                )
            ):
                raise ValueError(
                    "Meteor return fare or ownership is uncertain; no new input issued"
                )
        from conquest.merchants.service_visit import MarketVisit

        MarketVisit().departed(world)
        open_warehouse(loop)
        save(state, "completed", completed_at=time.time())
        loop.record(
            "meteor_loop_complete",
            activity="Market banking complete; checking Phoenix supplies before farming",
        )
        return True
    if world == state["origin"] and state["phase"] == "withdrawing":
        open_warehouse(loop)
        for uid in state["meteor_uids"]:
            bag = loop.town("supplies")["items"]
            bank = loop.town("warehouse-items")["items"]
            in_bag = [i for i in bag if i["uid"] == uid]
            in_bank = [i for i in bank if i["uid"] == uid]
            if (
                len(in_bag) + len(in_bank) != 1
                or (in_bag or in_bank)[0]["type_id"] != METEOR
            ):
                raise ValueError(
                    "Meteor withdrawal state is ambiguous; no additional withdrawal issued"
                )
            if in_bag:
                continue
            receipt = loop.town("warehouse-withdraw-meteor", uid=uid)
            if receipt.get("verified_in_inventory") is not True:
                raise ValueError("Meteor withdrawal receipt missing")
            state.setdefault("withdrawals", []).append(receipt)
            save(state)
        # The server chooses the consumed Meteors. Leave exactly the recorded
        # batch in inventory so its receipt cannot consume unrelated IDs.
        for item in loop.town("supplies")["items"]:
            if item["type_id"] == METEOR and item["uid"] not in state["meteor_uids"]:
                receipt = loop.town("warehouse-deposit", uid=item["uid"])
                if receipt.get("verified_in_warehouse") is not True:
                    raise ValueError(
                        "Extra Meteor deposit unverified; no departure issued"
                    )
        save(state, "travelling")
    if world == state["origin"] and state["phase"] == "travelling":
        if state.get("departure_attempted"):
            raise ValueError("Meteor departure uncertain; no repeat fare issued")
        close_warehouse(loop)

        # Movement and NPC qualification are retryable.  Mark the departure
        # only at the trip's pre-submit fence, immediately before the first
        # fare/dialogue input.  A failure while walking must not strand this
        # journal behind a false payment hold.
        def mark_departure_submission():
            save(state, departure_attempted=True, departure_submitted_at=time.time())

        trip(loop, route["outbound"], before_submit=mark_departure_submission)
        world = 1036
        save(state, "exchange_ready")
    if world != 1036:
        raise ValueError("Unexpected map during Meteor trip; valuables preserved")
    if state["phase"] in ("travelling", "carried_in_market"):
        save(state, "exchange_ready")
    exchange = policy["exchange"]
    if state["phase"] == "exchange_ready":
        if not exchange.get("transaction_verified"):
            raise ValueError("Meteor exchange has not been verified")
        bag = loop.town("supplies")
        expected = set(state["meteor_uids"])
        if expected != {i["uid"] for i in bag["items"] if i["type_id"] == METEOR}:
            raise ValueError("Recorded Meteor batch is missing; no exchange issued")
        loop.travel(
            tuple(exchange["approach"]),
            activity="Heading to MillionaireLee to pack ten Meteors",
            service_name="MillionaireLee",
        )
        npc = loop.town("service-locate", name="MillionaireLee")
        if npc["identity"] != exchange["identity"]:
            raise ValueError("MillionaireLee identity changed")
        loop.town("service-open", name="MillionaireLee")
        steps = exchange["dialogs"]
        for step in steps[:-1]:
            select_saved_dialog(loop, "MillionaireLee", step)
        before = loop.town("supplies")
        save(state, "exchange_pending", before=before)
        select_saved_dialog(loop, "MillionaireLee", steps[-1])
    if state["phase"] == "exchange_pending":
        before = state["before"]
        deadline = time.monotonic() + 5
        while True:
            after = loop.town("supplies")
            if exchange_received(before, after, exchange["fee"]):
                break
            if time.monotonic() >= deadline:
                raise ValueError(
                    "Meteor exchange unverified; no repeat exchange or return issued"
                )
            time.sleep(0.1)
        old = {i["uid"] for i in before["items"]}
        new = {i["uid"] for i in after["items"]}
        if old - new != set(state["meteor_uids"]):
            raise ValueError("Exchange removed a different batch; remain in Market")
        scroll = next(i for i in after["items"] if i["uid"] not in old)
        save(
            state,
            "storing_scroll",
            scroll_uid=scroll["uid"],
            after=after,
            exchange_verified=True,
        )
        loop.record(
            "meteor_exchange_verified",
            activity="Ten Meteors packed; storing the scroll in Market",
            scroll_uid=scroll["uid"],
        )
    if state["phase"] in ("storing_scroll", "stored_in_market", "returning"):
        # A verified bank receipt survives a later movement interruption. Do
        # not walk back to the warehouse unless valuables remain to deposit.
        if (
            state["phase"] == "storing_scroll"
            or not state.get("market_verified_at")
            or carried(loop)
        ):
            market_bank(loop, state)
        save(state, "returning")
        if state.get("return_submitted_at"):
            # A previous process may have selected the return dialogue before
            # losing its arrival observation.  Only the origin-map branch above
            # can settle that submission; never select it again in Market.
            raise ValueError(
                "Meteor return submission is uncertain; no repeat fare issued"
            )

        def mark_return_submission():
            save(
                state,
                return_submitted_at=time.time(),
                return_before=loop.town("supplies"),
            )

        trip(loop, route["return"], before_submit=mark_return_submission)
        # Arrival is checked by trip; the reopened origin bank lets shopping
        # finish its cash transfer without reusing the old city balance.
        open_warehouse(loop)
        save(state, "completed", completed_at=time.time())
        loop.record(
            "meteor_loop_complete",
            activity="Market banking complete; checking Phoenix supplies before farming",
        )
        return True
    raise ValueError("Meteor journal needs reconciliation")


def consolidate(loop, stored=None):
    """Start a qualified batch; finish back at the original open warehouse."""
    policy = read_json(POLICY)
    if not policy.get("enabled") or not policy.get("qualified"):
        return False
    if not policy.get("exchange"):
        raise ValueError("Meteor exchange has not been qualified")
    previous = _recover_archive_boundary()
    if previous and previous.get("phase") not in TERMINAL:
        raise ValueError(
            "An unfinished Meteor transfer needs reconciliation; no new batch withdrawn"
        )
    # Commit old exact storage intent before any operation can replace its
    # journal. A crash on either side is safe and UID-idempotent.
    from conquest.stored_scroll_queue import capture

    capture(JOURNAL, previous)
    # `stored` used to be a caller-provided snapshot.  It could be stale by
    # the time an override was confirmed, so every new operation obtains its
    # own read-only bag and warehouse observations here.
    before = loop.town("supplies")
    stored = loop.town("warehouse-items")
    loose = [
        i
        for i in before["items"]
        if i["type_id"] == METEOR and i["amount"] == i["limit"] == 1
    ]
    items = batch(loose + stored["items"])
    if not items:
        return False
    life = loop.living()["embedded_controls"]["life"]
    origin = life["map_id"]
    route = policy.get("origins", {}).get(str(origin))
    if not route:
        return False
    if not all(route[leg].get("verified") for leg in ("outbound", "return")):
        raise ValueError("Meteor consolidation requires a verified round trip")
    bag_uids = {i["uid"] for i in before["items"]}
    needed = sum(i["uid"] not in bag_uids for i in items)
    if before["capacity"] - len(before["items"]) < needed:
        raise ValueError("Insufficient free inventory slots for the ten-Meteor batch")
    from conquest.banking import transfer, transport_reserve

    reserve = (
        route["outbound"]["fare"]
        + route["return"]["fare"]
        + policy["exchange"]["fee"]
        + transport_reserve()
    )
    if before["silver"] < reserve:
        bank = loop.town("warehouse-money")
        if bank["stored_silver"] < reserve - before["silver"]:
            raise ValueError("Insufficient transport reserve for Meteor consolidation")
        transfer(loop, "withdraw", reserve - before["silver"])
    state = {
        "origin": origin,
        "meteor_uids": [i["uid"] for i in items],
        "started_at": time.time(),
        "phase": "withdrawing",
    }
    if previous.get("phase") == "operator_overridden":
        _replace_overridden_journal(state)
    else:
        save(state)
    loop.record(
        "meteor_consolidation_started",
        activity="Withdrawing ten Meteors for Market packing and banking",
    )
    return resume(loop)
