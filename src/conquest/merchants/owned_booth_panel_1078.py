"""Once-only owned-panel opening and empirical qualification; no asset input."""

import json
import re
import threading
import time
import uuid

from conquest.capture import CaptureUnavailable
from conquest.memory_build_layout import CLIENT_SHA256_1078
from conquest.merchants.booth_listing_once_1078 import (
    _profile,
    _row,
    _farmer_journals_clear,
)

KIND = "owned_booth_panel_1078"
PENDING = "owned_booth_panel_1078_pending"
CAPABILITY = "owned_booth_panel_1078_capability"
ASSETS = (
    "identity",
    "character",
    "character_uid",
    "server",
    "map_id",
    "position",
    "silver",
    "capacity",
    "inventory",
    "booth",
    "own_booth_uid",
)
WORKERS = {}
LOCK = threading.RLock()


def _closed(snapshot, request, profile):
    if (
        any(
            type(request.get(key)) is not int or request[key] <= 0
            for key in ("expected_character_uid", "expected_own_booth_uid")
        )
        or snapshot["identity"] != request["expected_identity"]
        or snapshot["character_uid"] != request["expected_character_uid"]
        or snapshot["character_uid"] != profile.character_uid
        or snapshot["character"] != profile.name
        or snapshot["server"] != profile.server
        or snapshot["own_booth_uid"] != request["expected_own_booth_uid"]
        or not snapshot["own_booth_uid"]
        or snapshot["booth_open"]
        or snapshot["map_id"] != 1036
        or snapshot["hp"] <= 0
        or snapshot.get("trade") is not None
        or snapshot.get("request") is not None
        or snapshot.get("closed_modal") is False
        or not 0 <= time.time() - snapshot["timestamp"] <= 1
    ):
        raise ValueError(
            "Owned panel request identity, ownership or closed state changed"
        )


def _unchanged(snapshot, baseline):
    if any(snapshot[key] != baseline[key] for key in ASSETS):
        raise ValueError(
            "Owned panel assets or actor changed; read-only reconciliation required"
        )
    if (
        snapshot["hp"] <= 0
        or snapshot.get("trade") is not None
        or snapshot.get("request") is not None
        or snapshot.get("closed_modal") is False
        or not 0 <= time.time() - snapshot["timestamp"] <= 1
    ):
        raise ValueError("Owned panel observation is unsafe or expired")


def pending(journal, character):
    key = journal.get(character, PENDING)
    row = _row(journal, key) if key else None
    if not row or row["kind"] != KIND or row["character"] != character:
        return None
    return row


def _markers(journal, key):
    with journal.db() as db:
        return db.execute(
            "SELECT payload FROM transaction_steps WHERE transaction_id=? "
            "AND stage='panel_click' AND status='before_action'",
            (key,),
        ).fetchall()


def status(journal, character, key):
    row = _row(journal, key)
    if not row or row["kind"] != KIND or row["character"] != character:
        raise ValueError("Unknown exact owned panel request")
    return {
        "request_id": key,
        "phase": row["phase"],
        "result": json.loads(row["result_json"] or "{}"),
        "running": key in WORKERS and WORKERS[key].is_alive(),
    }


def require_receipt(runtime, character, snapshot):
    from conquest.merchants.listing_capability_1078 import _digest

    saved = runtime.journal.get(character, CAPABILITY) or {}
    row = _row(runtime.journal, saved.get("request_id")) if saved else None
    if (
        not row
        or row["kind"] != KIND
        or row["character"] != character
        or row["phase"] != "verified"
    ):
        raise ValueError("Owned panel live receipt is unavailable")
    before, result = json.loads(row["before_json"]), json.loads(row["result_json"])
    evidence = result["evidence"]
    from conquest.merchants.owned_booth_hover_1078 import PINS

    submitted = evidence["input"]
    candidate, selected = submitted["candidate"], submitted["selected"]
    observations = evidence["observations"]
    profile = _profile(character)
    if (
        saved.get("digest") != _digest(evidence)
        or not result.get("closed_to_open_verified")
        or before.get("client_sha256") != CLIENT_SHA256_1078
        or before["snapshot"]["booth_open"] is not False
        or before["profile_id"] != profile.id
        or snapshot["character_uid"] != profile.character_uid
        or snapshot["character"] != profile.name
        or snapshot["server"] != profile.server
        or snapshot["identity"] != before["snapshot"]["identity"]
        or snapshot["character_uid"] != before["snapshot"]["character_uid"]
        or snapshot["character"] != before["snapshot"]["character"]
        or snapshot.get("client_sha256") != CLIENT_SHA256_1078
        or submitted.get("native_code_sha256") != [pin[2] for pin in PINS]
        or submitted.get("identity") != before["snapshot"]["identity"]
        or submitted.get("hwnd") != before["hwnd"]
        or candidate["owned_booth_uid"] != before["snapshot"]["own_booth_uid"]
        or selected["owned_booth_uid"] != candidate["owned_booth_uid"]
        or int(selected["selected_actor"], 16)
        != int(candidate["actor_address"], 16) + 0x10
        or len(observations) != 2
        or observations[1]["timestamp"] <= observations[0]["timestamp"]
    ):
        raise ValueError("Owned panel live receipt identity or evidence changed")
    with runtime.journal.db() as db:
        marker = db.execute(
            "SELECT payload FROM transaction_steps WHERE transaction_id=? "
            "AND stage='panel_click' AND status='before_action'",
            (row["id"],),
        ).fetchall()
    if len(marker) != 1 or json.loads(marker[0][0]) != evidence["input"]:
        raise ValueError("Owned panel once-only input evidence changed")
    for sample in observations:
        _verify_open(sample, before["snapshot"], fresh=False)
    return saved


def prepare_due(ui, character, snapshot):
    """Prepare only a due closed-panel request backed by an actual prior open.

    This does not click, grant input, advance a timer, or retry a terminal or
    uncertain request. The existing route loop admits the normal safe window.
    """
    runtime = ui.runtime
    if snapshot.get("booth_open") or not runtime.refills[character].due():
        return None
    if not runtime.refill_enabled(character):
        return {"state": "waiting", "blocker": "refill_paused_or_global_stop"}
    row = pending(runtime.journal, character)
    if row and row["phase"] != "verified":
        return {
            "state": "waiting",
            "blocker": "owned_panel_request_needs_reconciliation",
            "request_id": row["id"],
            "phase": row["phase"],
        }
    try:
        require_receipt(runtime, character, snapshot)
    except (ValueError, KeyError, TypeError) as error:
        return {
            "state": "waiting",
            "blocker": "owned_panel_live_receipt_required",
            "note": str(error),
        }
    request = {
        "action": "merchant-owned-panel-prepare-1078",
        "character": character,
        "request_id": "booth-open1078-auto-" + uuid.uuid4().hex,
        "expected_identity": snapshot["identity"],
        "expected_character_uid": snapshot["character_uid"],
        "expected_own_booth_uid": snapshot["own_booth_uid"],
    }
    _closed(snapshot, request, _profile(character))
    result = prepare(ui, request)
    return {"state": "owned_panel_prepared", **result}


def prepare(ui, body):
    fields = {
        "action",
        "character",
        "request_id",
        "expected_identity",
        "expected_character_uid",
        "expected_own_booth_uid",
    }
    if set(body) != fields or not re.fullmatch(
        r"booth-open1078-[A-Za-z0-9_-]{8,80}", str(body["request_id"])
    ):
        raise ValueError("Exact owned booth panel request required")
    character, key = body["character"], body["request_id"]
    runtime, journal = ui.runtime, ui.runtime.journal
    if _row(journal, key):
        if json.loads(_row(journal, key)["before_json"])["request"] != body:
            raise ValueError("Owned panel request ID cannot be rebound")
        return status(journal, character, key)
    if getattr(ui, "grant", None):
        raise ValueError(
            "Release the existing handoff before a new owned panel request"
        )
    _idle(ui, character)
    from conquest.merchants.observe_1078 import observe
    from conquest.merchants.listing_capability_1078 import require

    first = observe(runtime, character, booth_target_preflight=True)
    second = observe(runtime, character)
    profile = _profile(character)
    _closed(first, body, profile)
    _closed(second, body, profile)
    _unchanged(second, first)
    require(journal, character, second)
    observer = runtime.observers.get(character)
    if observer is None or observer.adapter.identity != second["identity"]:
        raise ValueError("Exact merchant observer changed")
    before = {
        "request": dict(body),
        "profile_id": profile.id,
        "snapshot": second,
        "target_candidate": first["booth_target_preflight"],
        "hwnd": observer.hwnd,
        "client_sha256": CLIENT_SHA256_1078,
    }
    with runtime.lock:
        _idle(ui, character)
        if getattr(ui, "grant", None):
            raise ValueError(
                "Another handoff was granted during owned panel observation"
            )
        if runtime.handoff and not runtime.handoff.startswith(
            f"merchant-refill:{character}:"
        ):
            raise ValueError("Another exact merchant handoff is pending")
        if not journal.begin(key, character, KIND, before):
            return status(journal, character, key)
        journal.set(character, PENDING, key)
        from conquest.merchants.listing_handoff_1078 import request_handoff

        handoff = request_handoff(runtime, character)
    return {
        **status(journal, character, key),
        "handoff_request_id": handoff,
        "scope": "listing_1078",
        "mode": "open_panel",
    }


def _idle(ui, character, *, key=None):
    r, c = ui.runtime, ui.coordinator
    if (
        ui.closed
        or ui.app.closing
        or r.stop_event.is_set()
        or c.stopped
        or not r.refill_enabled(character)
        or c.manual_active()
        or r.manual_handoff_status() is not None
        or c.manual_session_blocked(character)
        or c.manual_session_blocked("Farmer")
        or r.connecting
        or r.refilling
        or ui.calibrating
        or getattr(r, "delivery_window", None)
    ):
        raise CaptureUnavailable(
            "Owned panel opening stopped or held by manual/merchant work"
        )
    from conquest.merchants.background_probe import probe_busy
    from conquest.merchants.delivery_reservation import active

    if probe_busy(ui) or active(r.journal, character):
        raise CaptureUnavailable("Another probe or delivery owns merchant input")
    _farmer_journals_clear(r)
    with r.journal.db() as db:
        rows = db.execute(
            "SELECT id,kind,phase FROM transactions WHERE phase NOT IN "
            "('verified','aborted','operator_overridden')"
        ).fetchall()
    if any(
        row["id"] != key or row["kind"] != KIND or row["phase"] != "prepared"
        for row in rows
    ):
        raise ValueError("Reconcile the pending merchant transaction first")


def validate_grant(ui, character):
    row = pending(ui.runtime.journal, character)
    if not row or row["phase"] != "prepared":
        raise ValueError("No unsent owned panel request")
    if _markers(ui.runtime.journal, row["id"]):
        raise ValueError(
            "Owned panel click was already marked; reconcile without input"
        )
    _idle(ui, character, key=row["id"])
    before = json.loads(row["before_json"])
    from conquest.merchants.observe_1078 import observe

    snapshot = observe(ui.runtime, character, booth_target_preflight=True)
    _closed(snapshot, before["request"], _profile(character))
    _unchanged(snapshot, before["snapshot"])
    return {
        "mode": "open_panel",
        "profile_id": before["profile_id"],
        "panel_request_id": row["id"],
    }


def _verify_open(snapshot, baseline, *, fresh=True):
    if fresh:
        _unchanged(snapshot, baseline)
    elif any(snapshot[key] != baseline[key] for key in ASSETS):
        raise ValueError("Panel receipt assets changed")
    preflight = snapshot.get("listing_preflight") or {}
    owned = preflight.get("owned_booth") or {}
    if (
        snapshot["booth_open"] is not True
        or snapshot.get("closed_modal") is not True
        or preflight.get("layout_observed") is not True
        or not preflight.get("inventory_grid")
        or not preflight.get("booth_grid")
        or owned.get("model_key") != 25
        or owned.get("model_owner_verified") is not True
        or owned.get("owner_uid") != baseline["own_booth_uid"]
        or (preflight.get("price_modal") or {}).get("observed") is not False
    ):
        raise ValueError("Exact owned Booth and Inventory opening is not verified")


def reconcile(ui, character, key):
    journal = ui.runtime.journal
    row = _row(journal, key)
    if not row or row["character"] != character or row["kind"] != KIND:
        raise ValueError("Panel request changed")
    if row["phase"] in ("verified", "aborted", "operator_overridden"):
        return status(journal, character, key)
    with journal.db() as db:
        markers = db.execute(
            "SELECT payload FROM transaction_steps WHERE transaction_id=? "
            "AND stage='panel_click' AND status='before_action'",
            (key,),
        ).fetchall()
    if not markers:
        return status(journal, character, key)
    if len(markers) != 1:
        raise ValueError("Ambiguous owned panel click marker")
    before = json.loads(row["before_json"])
    from conquest.merchants.observe_1078 import observe

    first = observe(ui.runtime, character, listing_preflight=True)
    _verify_open(first, before["snapshot"])
    time.sleep(0.15)
    second = observe(ui.runtime, character, listing_preflight=True)
    _verify_open(second, before["snapshot"])
    if second["timestamp"] <= first["timestamp"]:
        raise ValueError("Panel verification needs distinct fresh samples")
    from conquest.merchants.listing_capability_1078 import _digest

    evidence = {"input": json.loads(markers[0][0]), "observations": [first, second]}
    journal.transition(
        key,
        "verified",
        {
            "closed_to_open_verified": True,
            "assets_unchanged": True,
            "evidence": evidence,
        },
    )
    journal.set(character, CAPABILITY, {"request_id": key, "digest": _digest(evidence)})
    attention = journal.get(character, "attention") or {}
    if attention.get("transaction_id") == key:
        journal.set(character, "attention", None)
    return status(journal, character, key)


def step(ui, character, snapshot):
    row = pending(ui.runtime.journal, character)
    if not row or row["phase"] == "verified":
        return None  # prepare_due separately admits new requests from real receipts.
    key = row["id"]
    with LOCK:
        if key in WORKERS and WORKERS[key].is_alive():
            return status(ui.runtime.journal, character, key)
        if row["phase"] != "prepared" or _markers(ui.runtime.journal, key):
            return reconcile(ui, character, key)
        grant = getattr(ui, "grant", None) or {}
        authority = grant.get("listing_authority") or {}
        if (
            grant.get("scope") != "listing_1078"
            or grant.get("character") != character
            or authority.get("mode") != "open_panel"
            or authority.get("panel_request_id") != key
        ):
            from conquest.merchants.listing_handoff_1078 import request_handoff

            return {
                "state": "waiting_panel_handoff",
                "request_id": key,
                "handoff_request_id": request_handoff(ui.runtime, character),
            }
        if not ui.runtime.can_start_work(12):
            raise CaptureUnavailable("Owned panel opening needs twelve grant seconds")
        before = json.loads(row["before_json"])
        token = ui.coordinator.fence.capture()
        worker = threading.Thread(
            target=_run,
            args=(ui, character, before, dict(grant), token),
            daemon=True,
            name=KIND,
        )
        WORKERS[key] = worker
        worker.start()
    return status(ui.runtime.journal, character, key)


def _run(ui, character, before, grant, token):
    journal, key = ui.runtime.journal, before["request"]["request_id"]
    attempted = False
    try:
        if _markers(journal, key):
            attempted = True
            reconcile(ui, character, key)
            return
        from conquest.memory import MemorySession
        from conquest.desktop_runtime import physical_coordinates
        from conquest.merchants.memory import GuiReader, HoverNotReady
        from conquest.merchants.reader_1078 import open_read_only_1078
        from conquest.merchants.booth_target_1078 import collect
        from conquest.merchants.owned_booth_hover_1078 import (
            qualify,
            assert_selected,
            PINS,
        )
        from conquest.input_probe import MessageTarget
        from conquest.layout_revision import SharedLayoutRevision
        from conquest.foreground import foreground_click
        from conquest.focus_recovery import activate_client
        from conquest.scene_pointer import wait_scene_pointer
        from conquest.merchants.listing_handoff_1078 import farmer_safe

        target = MessageTarget(before["snapshot"]["identity"]["pid"], before["hwnd"])
        deadline = time.monotonic() + min(20, grant["expires_at"] - time.time() - 3)
        control = ui.app.control.snapshot()
        focus = False
        with (
            MemorySession(target.pid, CLIENT_SHA256_1078) as session,
            physical_coordinates(),
        ):
            if session.identity != before["snapshot"]["identity"]:
                raise ValueError("Merchant process changed")
            gui = GuiReader.for_session(session)
            reader = open_read_only_1078(session, character)
            base = qualify(session)

            def policy():
                _idle(ui, character, key=key)
                if (
                    time.monotonic() >= deadline
                    or ui.grant != grant
                    or ui.app.control.snapshot() != control
                    or not ui.safe_to_yield()
                    or not panel_scope_allows(ui, character, key)
                ):
                    raise CaptureUnavailable(
                        "Exact owned panel handoff or control revision changed"
                    )
                farmer_safe(
                    ui, grant["listing_authority"]["farmer_target"], deadline=deadline
                )
                session.assert_identity()
                state = target.snapshot()
                if (
                    state["root_hwnd"] != target.hwnd
                    or focus
                    and state["foreground"] != target.hwnd
                ):
                    raise CaptureUnavailable("Owned panel native foreground changed")

            coordinator = ui.coordinator
            with (
                coordinator.fence.bind_worker(token),
                coordinator.owned_panel_scope(character, policy),
            ):
                with coordinator.lease(character, purpose=KIND):
                    if not activate_client(target.hwnd, session.identity):
                        raise CaptureUnavailable("Native focus unavailable")
                    focus = True
                    policy()
                    layout = SharedLayoutRevision(
                        target, windows=gui.windows, gui_size=gui.viewport_size
                    )
                    revision = layout.stable()

                    def fresh():
                        policy()
                        layout.assert_current(revision)
                        snap = reader.read_manual_ownership()
                        _closed(snap, before["request"], _profile(character))
                        _unchanged(snap, before["snapshot"])
                        candidate = collect(session, snap)
                        point = tuple(
                            candidate["projection_candidate"]["candidate_point"]
                        )
                        width, height = gui.viewport_size()
                        for window in gui.windows():
                            x, y, w, h = window["geometry"]
                            if w >= width - 10 and h >= height - 10:
                                continue
                            if x <= point[0] <= x + w and y <= point[1] <= y + h:
                                raise ValueError("GUI covers exact owned booth point")
                        return candidate, point

                    candidate, point = fresh()

                    def guard():
                        current, actual = fresh()
                        if current != candidate or actual != point:
                            raise ValueError("Owned booth target moved")
                        wait_scene_pointer(gui.session, point, policy)
                        return assert_selected(session, gui, base, candidate)

                    def hover():
                        until = time.monotonic() + 1.5
                        while True:
                            try:
                                return guard()
                            except HoverNotReady:
                                if time.monotonic() >= until:
                                    raise
                                time.sleep(0.025)

                    def mark():
                        nonlocal attempted
                        selected = guard()
                        if _markers(journal, key):
                            raise ValueError(
                                "Owned panel click was already marked; no replay"
                            )
                        journal.step(
                            key,
                            "panel_click",
                            "before_action",
                            {
                                "candidate": candidate,
                                "selected": selected,
                                "identity": session.identity,
                                "hwnd": target.hwnd,
                                "native_code_sha256": [pin[2] for pin in PINS],
                                "grant_request_id": grant["request_id"],
                                "at": time.time(),
                            },
                        )
                        attempted = True

                    foreground_click(
                        target,
                        *point,
                        expected_size=revision.client_size,
                        expected_origin=revision.client_origin,
                        require_foreground=True,
                        before_press=hover,
                        before_mouse_down=mark,
                        layout_guard=lambda: (
                            policy(),
                            layout.assert_current(revision),
                        ),
                    )
                    journal.transition(key, "submitted", {"click_attempted": True})
            until = time.monotonic() + 3
            while True:
                try:
                    reconcile(ui, character, key)
                    break
                except ValueError:
                    if time.monotonic() >= until:
                        raise
                    time.sleep(0.05)
    except Exception as error:
        row = _row(journal, key)
        if row and row["phase"] not in ("verified", "aborted", "operator_overridden"):
            if row["phase"] != "uncertain":
                journal.transition(
                    key,
                    "uncertain" if attempted else "aborted",
                    {"reason": str(error), "click_attempted": attempted},
                )
            journal.step(
                key,
                "panel_failed",
                "error",
                {"reason": str(error), "click_attempted": attempted},
            )
            journal.set(
                character,
                "attention",
                {"kind": KIND, "transaction_id": key, "reason": str(error)},
            )


def panel_scope_allows(ui, character, key):
    grant = getattr(ui, "grant", None) or {}
    authority = grant.get("listing_authority") or {}
    return (
        grant.get("scope") == "listing_1078"
        and grant.get("character") == character
        and authority.get("mode") == "open_panel"
        and authority.get("panel_request_id") == key
        and authority.get("profile_id") == _profile(character).id
    )


def dispatch(ui, body):
    if body["action"] == "merchant-owned-panel-prepare-1078":
        return prepare(ui, body)
    if set(body) != {"action", "character", "request_id"}:
        raise ValueError("Exact panel request ID required")
    if body["action"] == "merchant-owned-panel-reconcile-1078":
        return reconcile(ui, body["character"], body["request_id"])
    if body["action"] == "merchant-owned-panel-status-1078":
        return status(ui.runtime.journal, body["character"], body["request_id"])
    raise ValueError("Unknown owned panel operation")
