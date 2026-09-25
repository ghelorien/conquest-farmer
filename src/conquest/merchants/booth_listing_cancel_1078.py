"""Exact-request cleanup of a price dialog whose OK was never attempted.

Cancel is the sole permitted game control. Its durable press marker is consumed
once, including after a crash; later calls can only observe settlement.
"""

from contextlib import nullcontext
from copy import deepcopy
import json
import threading
import time

from conquest.capture import CaptureUnavailable
from conquest.memory import MemorySession
from conquest.memory_build_layout import CLIENT_SHA256_1078, read_build_layout
from conquest.merchants.memory import GuiReader, HoverNotReady, unpack
from conquest.merchants.reader_1078 import open_read_only_1078


def _pending(journal, request_id, character, *, allow_cancel=False):
    from conquest.merchants.booth_listing_once_1078 import KIND, _row

    row = _row(journal, request_id)
    if (
        not row
        or row["kind"] != KIND
        or row["character"] != character
        or row["phase"] not in ("prepared", "uncertain")
    ):
        raise ValueError(
            "Cancellation requires the exact unresolved pre-confirmation listing"
        )
    trace = journal.trace(request_id)
    if any(step["stage"] == "confirm_press" for step in trace):
        raise ValueError(
            "Listing OK may have been submitted; cancellation is forbidden"
        )
    if not allow_cancel and any(step["stage"] == "cancel_press" for step in trace):
        raise ValueError(
            "Cancellation was already attempted; observe its result without replay"
        )
    return json.loads(row["before_json"])


def purchase_adjusted(old, snapshot, before):
    """Scheduled-refill baseline after provable player purchases only.

    A scheduled refill hold can outlive buyers at our own booth. Rows that
    left the booth with the exactly matching net silver gain are purchases,
    never evidence about the listing; every other ownership field, including
    the whole inventory and the listed item, still compares exactly. Returns
    (baseline, purchases); purchases is None when nothing was bought.
    """
    if before.get("scheduled_foreground_refill") is not True:
        return old, None
    from conquest.merchants.sales import hold_purchases

    purchases = hold_purchases(old, snapshot)
    if purchases is None:
        return old, None
    return {
        **old,
        "booth": deepcopy(snapshot["booth"]),
        "silver": snapshot["silver"],
    }, purchases


def _unchanged(snapshot, before, profile):
    """Return proven hold purchases (or None); raise on any other change."""
    from conquest.merchants.booth_listing_once_1078 import (
        OWNERSHIP_FIELDS,
        _validate_snapshot,
    )

    _validate_snapshot(snapshot, profile, before["request"])
    old, purchases = purchase_adjusted(before["snapshot"], snapshot, before)
    if any(snapshot[key] != old[key] for key in OWNERSHIP_FIELDS if key in old):
        raise ValueError(
            "Original listing ownership changed; cancellation is unavailable"
        )
    return purchases


def _cancel_marker(journal, request_id, character, payload):
    """Atomic once-only marker before any Cancel mouse-down."""
    from conquest.merchants.booth_listing_once_1078 import KIND

    with journal.db() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT * FROM transactions WHERE id=?", (request_id,)
        ).fetchone()
        if (
            not row
            or row["kind"] != KIND
            or row["character"] != character
            or row["phase"] not in ("prepared", "uncertain")
            or db.execute(
                "SELECT 1 FROM transaction_steps WHERE transaction_id=? "
                "AND stage IN ('confirm_press','cancel_press')",
                (request_id,),
            ).fetchone()
        ):
            raise ValueError(
                "Listing/cancellation journal changed before Cancel mouse-down"
            )
        db.execute(
            "INSERT INTO transaction_steps(transaction_id,stage,status,payload,timestamp) VALUES(?,?,?,?,?)",
            (
                request_id,
                "cancel_press",
                "before_mouse_down",
                json.dumps(payload, sort_keys=True),
                time.time(),
            ),
        )


def reconcile_cancel(ui, request_id, character):
    from conquest.merchants.booth_listing_once_1078 import (
        _profile,
        _status,
        _row,
        KIND,
        OWNERSHIP_FIELDS,
    )

    journal = ui.runtime.journal
    row = _row(journal, request_id)
    if row and row["phase"] in ("aborted", "verified", "operator_overridden"):
        return _status(journal, request_id, character)
    before = _pending(journal, request_id, character, allow_cancel=True)
    if not any(step["stage"] == "cancel_press" for step in journal.trace(request_id)):
        return _status(journal, request_id, character)
    profile = _profile(character)
    identity = before["request"]["expected_identity"]
    with MemorySession(identity["pid"], CLIENT_SHA256_1078) as session:
        if session.identity != identity:
            raise ValueError("Cancelled listing process identity changed")
        reader = open_read_only_1078(session, profile.name)
        gui = GuiReader.for_session(session)
        layout = read_build_layout(session)
        from conquest.merchants.listing_preflight_1078 import _modal_code_verified

        _modal_code_verified(session, layout)
        model = gui.model(25, layout.merchant_booth_vtable_rva)

        def cleared():
            return (
                not any(w["name"] == "Add Item to Booth" for w in gui.windows())
                and session.read(model + 0x50, 16) == bytes(16)
                and gui.model(25, layout.merchant_booth_vtable_rva) == model
            )

        first = reader.read_manual_ownership()
        _unchanged(first, before, profile)
        if not cleared():
            return _status(journal, request_id, character)
        time.sleep(0.25)
        second = reader.read_manual_ownership()
        purchases = _unchanged(second, before, profile)
        if not cleared() or any(first[key] != second[key] for key in OWNERSHIP_FIELDS):
            return _status(journal, request_id, character)
        session.assert_identity()
        _pending(journal, request_id, character, allow_cancel=True)
        result = {
            "uid": before["request"]["item_uid"],
            "cancel_verified": True,
            "stock_unchanged": True,
            "listing_submitted": False,
            "confirmation_attempted": False,
            "cancellation_attempted": True,
            "first": first,
            "second": second,
            "replay_allowed": False,
            "foreground_listing_qualified": False,
        }
        within = None
        if purchases:
            # Listed stock is unchanged; only other booth rows were bought.
            result["player_purchases"] = purchases
            from conquest.merchants.sales import record_hold_purchases

            def within(db, row):
                record_hold_purchases(
                    db,
                    row["character"],
                    request_id,
                    before["snapshot"],
                    second,
                    purchases,
                )

        journal.transition(request_id, "aborted", result, within=within)
        attention = journal.get(character, "attention") or {}
        if (
            attention.get("kind") == KIND
            and attention.get("transaction_id") == request_id
        ):
            journal.set(character, "attention", None)
    return _status(journal, request_id, character)


def dispatch_cancel(ui, request_id, character):
    from conquest.merchants.booth_listing_once_1078 import (
        WORKER_LOCK,
        WORKERS,
        _profile,
        _status,
        _row,
        _policy,
        _merchant_intent,
        _farmer_safe_market,
    )

    journal = ui.runtime.journal
    with WORKER_LOCK:
        worker = WORKERS.get(request_id)
        if worker and worker.is_alive():
            raise ValueError(
                "Wait for the exact listing input worker to release before cancellation"
            )
        row = _row(journal, request_id)
        if row and row["phase"] in ("verified", "aborted", "operator_overridden"):
            return _status(journal, request_id, character)
        if any(step["stage"] == "cancel_press" for step in journal.trace(request_id)):
            return reconcile_cancel(ui, request_id, character)
        before = _pending(journal, request_id, character)
        profile = _profile(character)
        control = ui.app.control.snapshot()
        farmer = _farmer_safe_market(ui)
        intent = _merchant_intent(ui.runtime, character)
        _policy(
            ui,
            character,
            profile,
            control,
            request_id=request_id,
            farmer_target=farmer,
            merchant_intent=intent,
            phases=("prepared", "uncertain"),
            explicit_cleanup=True,
        )
        if not ui.runtime.can_start_work(6):
            raise CaptureUnavailable(
                "A fresh safe handoff with six seconds remaining is required to cancel"
            )
        token = ui.coordinator.fence.capture() if ui.coordinator.fence else None
        journal.step(
            request_id,
            "cancel_requested",
            "prepared",
            {
                "control": control,
                "farmer_target": farmer,
                "identity": before["request"]["expected_identity"],
                "item_uid": before["request"]["item_uid"],
            },
        )
        worker = threading.Thread(
            target=_run_cancel,
            args=(
                ui,
                character,
                profile,
                request_id,
                before,
                control,
                farmer,
                intent,
                token,
            ),
            daemon=True,
            name="booth-1078-cancel-once",
        )
        WORKERS[request_id] = worker
        worker.start()
    return _status(journal, request_id, character)


def _run_cancel(
    ui, character, profile, request_id, before, control, farmer, intent, token
):
    from conquest.merchants.booth_listing_once_1078 import (
        KIND,
        WORKER_LOCK,
        WORKERS,
        _policy,
        _modal,
    )
    from conquest.foreground import foreground_click
    from conquest.focus_recovery import activate_client
    from conquest.desktop_runtime import physical_coordinates
    from conquest.input_probe import MessageTarget
    from conquest.layout_revision import SharedLayoutRevision

    journal, coordinator = ui.runtime.journal, ui.coordinator
    request = before["request"]
    marked = False
    focused = False
    stage = "open_exact_process"
    deadline = time.monotonic() + 12
    try:
        with (
            MemorySession(
                request["expected_identity"]["pid"], CLIENT_SHA256_1078
            ) as session,
            physical_coordinates(),
        ):
            if session.identity != request["expected_identity"]:
                raise ValueError("Exact listing process changed before cancellation")
            target = MessageTarget(session.identity["pid"], before["hwnd"])
            reader = open_read_only_1078(session, profile.name)
            gui = GuiReader.for_session(session)
            booth_vtable = read_build_layout(session).merchant_booth_vtable_rva
            model = gui.model(25, booth_vtable)

            def policy():
                _policy(
                    ui,
                    character,
                    profile,
                    control,
                    request_id=request_id,
                    deadline=deadline,
                    farmer_target=farmer,
                    merchant_intent=intent,
                    phases=("prepared", "uncertain"),
                    explicit_cleanup=True,
                )
                session.assert_identity()
                native = target.snapshot()
                if (
                    native["root_hwnd"] != target.hwnd
                    or focused
                    and native["foreground"] != target.hwnd
                ):
                    raise CaptureUnavailable(
                        "Exact native merchant foreground changed during cancellation"
                    )

            def check():
                policy()
                coordinator.check()

            fence = coordinator.fence
            stage = "acquire_input"
            with (
                fence.bind_worker(token) if fence else nullcontext(),
                coordinator.booth_listing_once_scope(character, policy),
            ):
                with coordinator.lease(character, purpose=KIND):
                    stage = "baseline"
                    check()
                    _pending(journal, request_id, character)
                    baseline = reader.read_manual_ownership()
                    _unchanged(baseline, before, profile)
                    stage = "native_focus"
                    if not activate_client(target.hwnd, session.identity):
                        raise CaptureUnavailable(
                            "Native merchant focus unavailable for exact cancellation"
                        )
                    focused = True
                    check()
                    stage = "stable_layout"
                    layout = SharedLayoutRevision(
                        target, windows=gui.windows, gui_size=gui.viewport_size
                    )
                    revision = layout.stable()
                    baseline = reader.read_manual_ownership()
                    _unchanged(baseline, before, profile)
                    stage = "native_cancel_binding"
                    window, points, buffer = _modal(
                        gui, model, baseline, request["item_uid"]
                    )
                    digits = buffer.replace(",", "")
                    if digits and (
                        not digits.isascii()
                        or not digits.isdigit()
                        or not str(request["price"]).startswith(digits)
                    ):
                        raise ValueError(
                            "Native partial price is not a prefix of this exact listing request"
                        )
                    binding = (window, points, buffer)
                    point = tuple(
                        round(value * physical / logical)
                        for value, physical, logical in zip(
                            points["Cancel"], revision.client_size, revision.gui_size
                        )
                    )
                    if any(
                        not 1 < value < maximum - 2
                        for value, maximum in zip(point, revision.client_size)
                    ):
                        raise ValueError(
                            "Native Cancel point is outside the exact client"
                        )

                    def fresh():
                        check()
                        layout.assert_current(revision)
                        _pending(journal, request_id, character)
                        snapshot = reader.read_manual_ownership()
                        _unchanged(snapshot, before, profile)
                        if (
                            gui.model(25, booth_vtable) != model
                            or unpack(gui.session, model + 0x4C, "<I")[0]
                            != request["expected_own_booth_uid"]
                            or _modal(gui, model, snapshot, request["item_uid"])
                            != binding
                        ):
                            raise ValueError(
                                "Exact native price dialog changed before cancellation"
                            )
                        gui.assert_hovered(window, "Cancel")

                    def hover():
                        until = time.monotonic() + 1.5
                        while True:
                            try:
                                fresh()
                                return
                            except HoverNotReady:
                                if time.monotonic() >= until:
                                    raise
                                check()
                                time.sleep(0.02)

                    def press():
                        nonlocal marked
                        # A transient native HoveredId gap at mouse-down is
                        # still pre-input. Reobserve the exact unchanged dialog
                        # under the same grant/Stop checks before consuming the
                        # once-only Cancel marker.
                        hover()
                        _cancel_marker(
                            journal,
                            request_id,
                            character,
                            {
                                "uid": request["item_uid"],
                                "price_buffer": buffer,
                                "identity": session.identity,
                                "owned_booth_uid": request["expected_own_booth_uid"],
                                "window": {
                                    "name": window["name"],
                                    "address": window["address"],
                                },
                                "control": "Cancel",
                            },
                        )
                        marked = True

                    stage = "native_cancel_hover_and_press"
                    foreground_click(
                        target,
                        *point,
                        revision.client_size,
                        require_foreground=True,
                        before_press=hover,
                        before_mouse_down=press,
                        layout_guard=lambda: (check(), layout.assert_current(revision)),
                    )
                    stage = "cancel_reconciliation"
                    until = time.monotonic() + 3
                    while time.monotonic() < until:
                        # The click may settle after grant expiration; this
                        # tail only observes and can never replay Cancel.
                        if (
                            reconcile_cancel(ui, request_id, character)["phase"]
                            == "aborted"
                        ):
                            return
                        session.assert_identity()
                        time.sleep(0.1)
                    raise CaptureUnavailable(
                        "Cancel result uncertain; only read-only reconciliation is allowed"
                    )
    except BaseException as error:
        failure = {
            "confirmation_attempted": False,
            "cancellation_attempted": marked,
            "replay_allowed": False,
            "reason": str(error),
            "error_type": type(error).__name__,
            "failure_stage": stage,
        }
        try:
            # Existing uncertainty is evidence, not a legal transition back
            # into itself. Append diagnostics without replacing that receipt.
            journal.step(
                request_id,
                "cancel_failed",
                "uncertain" if marked else "before_press",
                failure,
            )
            from conquest.merchants.booth_listing_once_1078 import _row

            row = _row(journal, request_id)
            if row and row["phase"] == "prepared":
                journal.transition(
                    request_id, "uncertain", failure, expected="prepared"
                )
        except (ValueError, OSError):
            pass
        try:
            from conquest.merchants.booth_listing_once_1078 import _row

            row = _row(journal, request_id)
            if row and row["phase"] not in ("prepared", "uncertain"):
                return
            journal.set(
                character,
                "attention",
                {
                    "kind": KIND,
                    "transaction_id": request_id,
                    "note": "Exact pre-confirmation listing cleanup failed at "
                    + stage
                    + ": "
                    + str(error),
                    "cancel_failure": failure,
                },
            )
        except (ValueError, OSError):
            pass
    finally:
        with WORKER_LOCK:
            if WORKERS.get(request_id) is threading.current_thread():
                WORKERS.pop(request_id, None)
