"""A scheduled 1078 refill drag that never produced the price dialog.

Live incident 2026-09-25 15:25:58 (Dutch, booth-list1078-refill-7cafde4b...):
the farmer's Farming On revoked the grant after ``drag_press``. The drag
worker released the mouse in ``foreground_drag``'s ``finally`` before it
journaled ``uncertain``; there is no ``drag_release`` and no ``native_dialog``
step. Memory afterwards: no "Add Item to Booth" window and every ownership
field equal to the listing baseline. Yet each observer tick admitted the row
to Cancel, took merchant input (bridge input_owner 'Dutch'), started the
Cancel worker and failed at native_cancel_binding ("Exactly one live Add Item
to Booth panel is required") -- 1023 times. That blocked controller deploys,
farmer resume and Dutch's refills indefinitely.

In ``booth_listing_once_1078._run`` the only journal stages before the price
dialog exists are ``foreground``, ``baseline``, ``drag_pointer`` and
``drag_press``; ``drag_release`` is written before the destination mouse-up,
``native_dialog`` once the dialog is observed, then ``amount_*``,
``price_digit_*``, ``price``, ``confirm_pointer`` and ``confirm_press``. A
listing can only be submitted through OK in that dialog, and a submitted
listing always moves the item from inventory into a new booth row.

Failure modes, written before the fix. Only process memory reads, the native
window/focus/layout surface and the input worker are faked; the refill step,
reconcile, Cancel admission/dispatch, settlement, cursor release and sales
journal are the real code.

N1  Live incident (no purchases): the first tick settles the hold aborted
    read-only -- no input lease, focus, click, drag, Cancel dispatch or farmer
    handoff request -- releases the refill cursor and plans a fresh request ID.
    (E2E; the JSON artifact is written twice and must be byte-identical.)
N2  Same, with player purchases from our booth in between: aborted with the
    d9571bb purchase proof and exactly one verified sale; later observation
    and repeated settlement never double count. (E2E, byte-identical.)
N3  The dialog is open (the finally mouse-up landed over the booth): this path
    does not settle; the existing exact Cancel path presses Cancel once.
N4  Any later marker (drag_release, native_dialog, amount_*, price_digit_*,
    price, confirm_pointer, confirm_press, cancel_press) is never settled by
    this path. With no panel open the step no longer dispatches Cancel, takes
    an input lease or requests a farmer handoff; it returns a blocker.
N5  Inventory changed, listed item gone or moved, a booth row added, silver
    unmatched, trade/request open, position/booth/process changed: the hold
    stays uncertain and repeated ticks take no input and request no handoff.
N6  The two observations differ, or come from another process: blocked.
N7  A restart/crash between the two observations writes nothing; the next
    tick settles once. A second settlement racing the first is refused by
    the phase check, so exactly one sale is recorded. A crash after the
    aborted receipt but before the cursor release is finished by the next
    tick without new observations or input.
N8  Farmer grant active or not, Farming On or Off: the same read-only
    settlement; nothing waits for or requests a handoff.
N9  A live listing/Cancel worker for the request, a non-scheduled (operator)
    receipt, or an uncertain row not written by the listing worker (so the
    drag's finally mouse-up is not proven) is never settled by this path.
N10 A tampered no-dialog receipt (snapshots, purchases, flags or an injected
    later stage) never releases the refill cursor.
"""

import json
import threading
import time
from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace as NS

import pytest
import test_refill_cancel_after_sales_1078 as base
from test_merchant_listing_capability_1078 import make_ui

from conquest.memory_build_layout import CLIENT_SHA256_1078
from conquest.merchants import booth_listing_cancel_1078 as cancel
from conquest.merchants import booth_listing_once_1078 as listing
from conquest.merchants import listing_capability_1078 as capability
from conquest.merchants import listing_handoff_1078 as handoff
from conquest.merchants import refill_1078 as refill
from conquest.merchants import sales
from conquest.merchants.journal import Journal

KEY = "booth-list1078-refill-7cafde4b6e1742b69cd0d5c75d54c01a"
REQUEST = {**base.REQUEST, "request_id": KEY}
LIVE_REASON = "Farmer handoff was revoked or expired"
LOOP_FAILURE = {
    "cancellation_attempted": False,
    "confirmation_attempted": False,
    "error_type": "ValueError",
    "failure_stage": "native_cancel_binding",
    "reason": "Exactly one live Add Item to Booth panel is required",
    "replay_allowed": False,
}


class Coordinator(base.Coordinator):
    """Records every input lease/scope; the fix must never take one here."""

    def __init__(self, game):
        super().__init__()
        self.game = game

    @contextmanager
    def booth_listing_once_scope(self, character, policy):
        self.game.trace.append({"event": "input_scope", "character": character})
        with super().booth_listing_once_scope(character, policy):
            yield

    @contextmanager
    def lease(self, character, purpose):
        self.game.trace.append({"event": "input_lease", "purpose": purpose})
        yield


def live_setup(
    tmp_path,
    monkeypatch,
    game,
    *,
    farmer_on=False,
    grant=None,
    extra_stages=(),
    loop_evidence=3,
    result=None,
):
    dispatched = base.install(monkeypatch, tmp_path, game)
    from conquest import foreground

    monkeypatch.setattr(
        foreground,
        "foreground_drag",
        lambda *a, **kw: game.trace.append({"event": "drag"}),
    )
    game.memory_reads = 0
    game.read_hooks = []

    def memory_read():
        game.memory_reads += 1
        for hook in list(game.read_hooks):
            hook(game.memory_reads)
        return game.ownership()

    for module in (cancel, listing):
        monkeypatch.setattr(
            module,
            "open_read_only_1078",
            lambda session, name: NS(read_manual_ownership=memory_read),
        )
    journal = Journal(tmp_path / "journal.sqlite3")
    base.qualify(journal)
    sales.observe(journal, {**base.ownership(), "timestamp": base.OLD - 2})
    before = {
        "request": deepcopy(REQUEST),
        "profile_id": "test-dutch",
        "hwnd": 55,
        "control": {"enabled": False, "paused": False},
        "client_sha256": CLIENT_SHA256_1078,
        "farmer_target": {"pid": 10, "hwnd": 11},
        "snapshot": base.listing_baseline(),
        "merchant_intent": {"operations": False, "refill": True},
        "price_plan": {"uid": base.LISTED, "price": 750_000},
        "routine_refill_permitted": False,
        "listing_engine_revision": capability.ENGINE_REVISION,
        "scheduled_foreground_refill": True,
        "farmer_grant": None,
    }
    journal.begin(KEY, "Dutch", listing.KIND, before)
    ui = make_ui(journal, {"identity": dict(base.IDENTITY)})
    runtime = ui.runtime
    runtime.stop_event = threading.Event()
    runtime.manual_handoff_status = lambda: None
    runtime.delivery_window = runtime.refill_window = None
    runtime.refilling = runtime.connecting = False
    runtime.farmer_bot_owned = lambda: False
    ui.closed = ui.calibrating = False
    ui.grant = grant
    ui.app.closing = False
    ui.app.control.snapshot = lambda: {
        "enabled": farmer_on,
        "paused": False,
        "revision": 20,
    }
    ui.coordinator = Coordinator(game)
    handoffs = []
    monkeypatch.setattr(
        handoff,
        "request_handoff",
        lambda runtime, character: (
            handoffs.append(character) or f"merchant-refill:{character}:1"
        ),
    )
    cancel_dispatches = []
    original_dispatch_cancel = cancel.dispatch_cancel

    def dispatch_cancel(ui, request_id, character):
        cancel_dispatches.append(request_id)
        return original_dispatch_cancel(ui, request_id, character)

    monkeypatch.setattr(cancel, "dispatch_cancel", dispatch_cancel)
    state = runtime.refills["Dutch"].state()
    state.update(
        pending=True,
        status="checking",
        listed=3,
        cursor=[base.LISTED],
        listing1078_engine=1,
        listing1078_request=deepcopy(REQUEST),
    )
    journal.set("Dutch", "refill", state)
    for stage, status, payload in (
        ("foreground", "before_action", {"hwnd": 55, "identity": base.IDENTITY}),
        ("foreground", "verified", {"hwnd": 55, "identity": base.IDENTITY}),
        (
            "baseline",
            "verified",
            {"uid": base.LISTED, "price": 750_000, "owned_booth_uid": 18},
        ),
        (
            "drag_pointer",
            "before_action",
            {"source": [1161, 210], "destination": [487, 262]},
        ),
        ("drag_press", "before_action", {}),
        *extra_stages,
    ):
        journal.step(KEY, stage, status, payload)
    journal.transition(
        KEY,
        "uncertain",
        result
        or {
            "input_attempted": True,
            "confirmation_attempted": False,
            "unchanged_ownership_verified": False,
            "replay_allowed": False,
            "reason": LIVE_REASON,
        },
    )
    # The live loop's evidence: cancel_requested / cancel_failed(before_press).
    for _ in range(loop_evidence):
        journal.step(KEY, "cancel_requested", "prepared", {"item_uid": base.LISTED})
        journal.step(KEY, "cancel_failed", "before_press", LOOP_FAILURE)
    journal.set(
        "Dutch",
        "attention",
        {
            "kind": listing.KIND,
            "transaction_id": KEY,
            "note": "Exact pre-confirmation listing cleanup failed at "
            "native_cancel_binding: " + LOOP_FAILURE["reason"],
            "cancel_failure": LOOP_FAILURE,
        },
    )
    return NS(
        j=journal,
        ui=ui,
        game=game,
        before=before,
        dispatched=dispatched,
        handoffs=handoffs,
        cancel_dispatches=cancel_dispatches,
    )


def tick(x):
    """One runtime observer tick in production order (runtime.py)."""
    snapshot = x.game.ownership()
    if not x.j.pending("Dutch"):
        sales.observe(x.j, snapshot)
    result = refill.step(x.ui, "Dutch", snapshot)
    base.join_workers()
    return result


INPUT_EVENTS = (
    "input_scope",
    "input_lease",
    "native_focus",
    "cancel_click",
    "drag",
)


def inputs(game):
    return [row for row in game.trace if row["event"] in INPUT_EVENTS]


def phase(journal):
    return listing._row(journal, KEY)["phase"]


def result_of(journal):
    return json.loads(listing._row(journal, KEY)["result_json"])


def stages(journal):
    return [step["stage"] for step in journal.trace(KEY)]


def refill_request(x):
    return x.ui.runtime.refills["Dutch"].state()["listing1078_request"]


def normalized(value):
    """Drop wall-clock fields; name any refill request ID other than KEY."""
    if isinstance(value, dict):
        return {
            key: normalized(item)
            for key, item in value.items()
            if key not in base.VOLATILE
        }
    if isinstance(value, list):
        return [normalized(item) for item in value]
    if (
        isinstance(value, str)
        and value.startswith("booth-list1078-refill-")
        and value != KEY
    ):
        return "<fresh-refill-request-id>"
    return value


# N1 + N2 (end to end, repeatable artifact) ---------------------------------


def run_live(root, monkeypatch, *, purchases):
    root.mkdir()
    game = base.Game(dialog=False)
    x = live_setup(root, monkeypatch, game)
    if purchases:
        game.buy(*base.INCIDENT_SALES)
    loop_steps = len(x.j.trace(KEY))
    ticks = [tick(x)]  # read-only settlement -> cursor release -> fresh plan
    reads_after_settlement = game.memory_reads
    # Later read-only observation and a repeated settlement attempt.
    for _ in range(2):
        sales.observe(x.j, game.ownership())
    repeated = cancel.settle_without_dialog(x.ui, KEY, "Dutch")
    row = listing._row(x.j, KEY)
    state = x.ui.runtime.refills["Dutch"].state()
    artifact = {
        "scenario": "scheduled_refill_drag_without_price_dialog"
        + ("_with_player_purchases" if purchases else ""),
        "request": {"request_id": KEY, "item_uid": base.LISTED, "price": 750_000},
        "game_trace": game.trace,
        "input_events": inputs(game),
        "farmer_handoff_requests": x.handoffs,
        "cancel_dispatches": x.cancel_dispatches,
        "memory_reads_for_settlement": reads_after_settlement,
        "ticks": [
            {"state": row.get("state"), "blocker": row.get("blocker")} for row in ticks
        ],
        "repeated_settlement": repeated,
        "transaction": {
            "phase": row["phase"],
            "loop_steps_before": loop_steps,
            "steps": [
                {
                    "stage": step["stage"],
                    "status": step["status"],
                    "payload": normalized(json.loads(step["payload"])),
                }
                for step in x.j.trace(KEY)
            ],
            "result": normalized(json.loads(row["result_json"])),
        },
        "attention_after": x.j.get("Dutch", "attention"),
        "sales": base.sale_rows(x.j),
        "sales_events": normalized(base.sales_events(x.j)),
        "refill_after": normalized(
            {
                key: state.get(key)
                for key in ("pending", "listed", "listing1078_request")
            }
        ),
        "fresh_dispatches": normalized(x.dispatched),
        "silver": {"before": base.START_SILVER, "after": game.state["silver"]},
    }
    path = root / "refill-drag-no-dialog-e2e.json"
    path.write_text(json.dumps(artifact, indent=2, sort_keys=True), encoding="utf-8")
    return path


def check_settled(artifact):
    # No merchant input and no farmer handoff at any point.
    assert artifact["input_events"] == []
    assert artifact["farmer_handoff_requests"] == []
    assert artifact["cancel_dispatches"] == []
    # Two fresh exact observations, nothing more.
    assert artifact["memory_reads_for_settlement"] == 2
    assert artifact["ticks"] == [{"state": "listing_pending", "blocker": None}]
    assert artifact["repeated_settlement"] is False
    transaction = artifact["transaction"]
    assert transaction["phase"] == "aborted"
    names = [step["stage"] for step in transaction["steps"]]
    for marker in (
        "drag_release",
        "native_dialog",
        "confirm_press",
        "cancel_press",
    ):
        assert marker not in names
    # Nothing is appended to the hold except its terminal receipt.
    assert len(names) == transaction["loop_steps_before"] + 1
    assert transaction["steps"][-1]["stage"] == "transaction"
    assert transaction["steps"][-1]["status"] == "aborted"
    result = transaction["result"]
    assert result["no_dialog_abort_verified"] is True
    assert result["dialog_observed"] is False
    assert result["listing_submitted"] is False
    assert result["confirmation_attempted"] is False
    assert result["cancellation_attempted"] is False
    assert result["replay_allowed"] is False
    assert result["prior_result"]["reason"] == LIVE_REASON
    assert artifact["attention_after"] is None
    # The old ID is consumed; refill resumes with a fresh one.
    fresh = artifact["refill_after"]["listing1078_request"]
    assert fresh["request_id"] == "<fresh-refill-request-id>"
    assert len(artifact["fresh_dispatches"]) == 1
    assert artifact["fresh_dispatches"][0]["item_uid"] == base.LISTED
    assert artifact["refill_after"]["listed"] == 3  # nothing claimed as listed
    return result


def test_live_drag_without_dialog_settles_read_only_and_refill_resumes(
    tmp_path, monkeypatch
):
    first = run_live(tmp_path / "first", monkeypatch, purchases=False)
    second = run_live(tmp_path / "second", monkeypatch, purchases=False)
    assert first.read_bytes() == second.read_bytes()
    artifact = json.loads(first.read_text(encoding="utf-8"))
    result = check_settled(artifact)
    assert "player_purchases" not in result
    # No sale is invented. (Like the exact Cancel path, a hold without
    # purchases leaves the pre-hold sales baseline; the next observation
    # labels that paused interval as a gap, never as a sale.)
    assert artifact["sales"] == []
    assert [e["event"] for e in artifact["sales_events"]] in (
        [],
        ["sales_observation_gap"],
    )
    assert artifact["game_trace"] == []


def test_live_drag_without_dialog_with_purchases_records_one_sale(
    tmp_path, monkeypatch
):
    first = run_live(tmp_path / "first", monkeypatch, purchases=True)
    second = run_live(tmp_path / "second", monkeypatch, purchases=True)
    assert first.read_bytes() == second.read_bytes()
    artifact = json.loads(first.read_text(encoding="utf-8"))
    result = check_settled(artifact)
    assert [row["event"] for row in artifact["game_trace"]] == ["player_purchase"]
    assert sorted(i["uid"] for i in result["player_purchases"]["items"]) == sorted(
        base.INCIDENT_SALES
    )
    assert result["player_purchases"]["silver"] == base.NET_INCIDENT
    assert artifact["silver"]["after"] - base.START_SILVER == base.NET_INCIDENT
    assert len(artifact["sales"]) == 1
    receipt = artifact["sales"][0]
    assert receipt["phase"] == "verified" and receipt["silver"] == base.NET_INCIDENT
    assert [e["event"] for e in artifact["sales_events"]] == ["sale_verified"]
    event = artifact["sales_events"][0]["payload"]
    assert event["listing_hold_transaction_id"] == KEY
    assert event["from"] == base.OLD - 2


# N3: dialog open -> the existing exact Cancel path, unchanged ---------------


@pytest.mark.parametrize("purchases", [False, True])
def test_open_dialog_keeps_the_existing_cancel_path(tmp_path, monkeypatch, purchases):
    game = base.Game(dialog=True)
    x = live_setup(tmp_path, monkeypatch, game)
    if purchases:
        game.buy(*base.INCIDENT_SALES)
    assert tick(x)["state"] == "listing_cancel_pending"
    assert x.cancel_dispatches == [KEY]
    assert len(base.clicks(game)) == 1
    assert phase(x.j) == "aborted"
    result = result_of(x.j)
    assert result["cancel_verified"] is True
    assert "no_dialog_abort_verified" not in result
    assert stages(x.j).count("cancel_press") == 1
    assert tick(x)["state"] == "listing_pending"
    assert len(x.dispatched) == 1 and x.dispatched[0]["request_id"] != KEY
    assert len(base.sale_rows(x.j)) == (1 if purchases else 0)


# N4: later markers are never settled here; no panel means no input ----------


@pytest.mark.parametrize(
    "marker",
    [
        ("drag_release", "before_action"),
        ("native_dialog", "verified"),
        ("amount_pointer", "before_action"),
        ("amount_press", "before_action"),
        ("price_digit_1", "before_action"),
        ("price", "verified"),
        ("confirm_pointer", "before_action"),
        ("confirm_press", "before_mouse_down"),
        ("cancel_press", "before_mouse_down"),
    ],
)
@pytest.mark.parametrize("farmer_on", [False, True])
def test_later_marker_is_never_settled_and_no_panel_takes_no_input(
    tmp_path, monkeypatch, marker, farmer_on
):
    game = base.Game(dialog=False)
    x = live_setup(
        tmp_path,
        monkeypatch,
        game,
        farmer_on=farmer_on,
        extra_stages=((*marker, {}),),
    )
    for _ in range(3):
        tick(x)
    result = result_of(x.j)
    assert "no_dialog_abort_verified" not in result
    assert inputs(game) == []
    assert x.cancel_dispatches == []
    if marker[0] == "cancel_press":
        # A landed Cancel still settles only through the existing read-only
        # Cancel reconciliation; refill then plans normally.
        assert phase(x.j) == "aborted" and result["cancel_verified"] is True
        return
    assert phase(x.j) == "uncertain"
    assert refill_request(x) == REQUEST
    assert x.handoffs == []
    assert x.dispatched == []
    assert base.sale_rows(x.j) == []


def test_no_panel_blocker_is_reported_without_dispatch(tmp_path, monkeypatch):
    game = base.Game(dialog=False)
    x = live_setup(
        tmp_path,
        monkeypatch,
        game,
        extra_stages=(("drag_release", "before_action", {}),),
    )
    loop_steps = len(x.j.trace(KEY))
    for _ in range(5):
        result = tick(x)
        assert result["state"] == "waiting"
        assert result["blocker"] == "listing_cancel_dialog_absent"
    # No new cancel_requested / cancel_failed rows: the loop is gone.
    assert len(x.j.trace(KEY)) == loop_steps
    assert inputs(game) == [] and x.cancel_dispatches == []


# N5: anything but an unchanged (or purchase-adjusted) baseline stays held ---


MUTATIONS = [
    "booth_item_added",
    "listed_item_moved_to_booth",
    "inventory_changed",
    "inventory_gained",
    "listed_item_gone",
    "silver_decreased",
    "silver_unchanged",
    "silver_over_net",
    "silver_under_net",
    "silver_gross_not_net",
    "remaining_price_changed",
    "slot_only_no_sale",
    "trade_open",
    "request_open",
    "own_booth_changed",
    "position_changed",
]


@pytest.mark.parametrize("mutation", MUTATIONS + ["silver_only_changed", "hp_changed"])
@pytest.mark.parametrize("farmer_on", [False, True])
def test_unproven_change_stays_blocked_without_input_loop(
    tmp_path, monkeypatch, mutation, farmer_on
):
    game = base.Game(dialog=False)
    x = live_setup(tmp_path, monkeypatch, game, farmer_on=farmer_on)
    if mutation == "silver_only_changed":
        game.state["silver"] += 1_000
    elif mutation == "hp_changed":
        game.state["hp"] -= 1
    else:
        base.mutate(game, mutation)
    loop_steps = len(x.j.trace(KEY))
    for _ in range(3):
        result = tick(x)
        assert result["blocker"] == "listing_receipt_needs_reconciliation"
    assert phase(x.j) == "uncertain"
    assert len(x.j.trace(KEY)) == loop_steps
    assert inputs(game) == []
    assert x.cancel_dispatches == [] and x.handoffs == []
    assert base.sale_rows(x.j) == [] and x.dispatched == []
    assert refill_request(x) == REQUEST


# N6: unstable or foreign observations --------------------------------------


@pytest.mark.parametrize("change", ["between_reads", "process_identity"])
def test_unstable_or_foreign_observation_is_blocked(tmp_path, monkeypatch, change):
    game = base.Game(dialog=False)
    x = live_setup(tmp_path, monkeypatch, game)
    if change == "between_reads":

        def hook(count):
            if count == 2:
                game.state["position"] = [233, 205]

        game.read_hooks.append(hook)
    else:
        game.state["identity"] = {**base.IDENTITY, "creation_time_100ns": 3}
    result = tick(x)
    assert result["blocker"] == "listing_receipt_needs_reconciliation"
    assert phase(x.j) == "uncertain"
    assert inputs(game) == [] and x.cancel_dispatches == []
    assert refill_request(x) == REQUEST


def test_dialog_appearing_between_reads_is_not_settled(tmp_path, monkeypatch):
    game = base.Game(dialog=False)
    x = live_setup(tmp_path, monkeypatch, game)

    def hook(count):
        if count == 2:
            game.dialog = True

    game.read_hooks.append(hook)
    assert cancel.settle_without_dialog(x.ui, KEY, "Dutch") is False
    assert phase(x.j) == "uncertain"


# N7: restart / race around the settlement ---------------------------------


def test_crash_between_observations_writes_nothing_then_settles_once(
    tmp_path, monkeypatch
):
    game = base.Game(dialog=False)
    x = live_setup(tmp_path, monkeypatch, game)
    game.buy(*base.INCIDENT_SALES)

    def crash(count):
        if count == 2:
            raise OSError("simulated app crash between the two observations")

    game.read_hooks.append(crash)
    loop_steps = len(x.j.trace(KEY))
    result = tick(x)
    assert result["blocker"] == "listing_receipt_needs_reconciliation"
    assert phase(x.j) == "uncertain" and len(x.j.trace(KEY)) == loop_steps
    assert base.sale_rows(x.j) == []
    game.read_hooks.clear()
    # "Restarted" app: a fresh journal handle, fresh observations.
    x.ui.runtime.journal = Journal(x.j.path)
    assert tick(x)["state"] == "listing_pending"
    assert phase(x.j) == "aborted"
    assert len(base.sale_rows(x.j)) == 1
    assert tick(x)["state"] == "listing_pending"  # fresh request only
    assert len(base.sale_rows(x.j)) == 1
    assert inputs(game) == []


def test_racing_second_settlement_is_refused_and_counts_once(tmp_path, monkeypatch):
    game = base.Game(dialog=False)
    x = live_setup(tmp_path, monkeypatch, game)
    game.buy(*base.INCIDENT_SALES)
    nested = []

    def race(count):
        if count == 2 and not nested:
            # Another observer settles while this one sits between reads.
            game.read_hooks.clear()
            nested.append(cancel.settle_without_dialog(x.ui, KEY, "Dutch"))

    game.read_hooks.append(race)
    with pytest.raises(ValueError):
        cancel.settle_without_dialog(x.ui, KEY, "Dutch")
    assert nested == [True]
    assert phase(x.j) == "aborted"
    assert len(base.sale_rows(x.j)) == 1
    assert cancel.settle_without_dialog(x.ui, KEY, "Dutch") is False
    assert len(base.sale_rows(x.j)) == 1


def test_crash_after_receipt_before_cursor_release_finishes_read_only(
    tmp_path, monkeypatch
):
    game = base.Game(dialog=False)
    x = live_setup(tmp_path, monkeypatch, game)
    game.buy(*base.INCIDENT_SALES)
    assert cancel.settle_without_dialog(x.ui, KEY, "Dutch") is True
    assert refill_request(x) == REQUEST  # cursor not yet released
    reads = game.memory_reads
    assert tick(x)["state"] == "listing_pending"
    assert game.memory_reads == reads  # no new observation needed
    assert refill_request(x)["request_id"] != KEY
    assert len(base.sale_rows(x.j)) == 1 and inputs(game) == []


# N8: farmer grant active or not -> identical read-only settlement ------------


@pytest.mark.parametrize(
    "farmer_on,grant",
    [
        (False, None),
        (True, None),
        (False, {"scope": "market_visit", "character": "Dutch", "request_id": "g1"}),
        (True, {"scope": "market_visit", "character": "Dutch", "request_id": "g1"}),
    ],
)
def test_farmer_grant_state_does_not_change_settlement(
    tmp_path, monkeypatch, farmer_on, grant
):
    game = base.Game(dialog=False)
    x = live_setup(tmp_path, monkeypatch, game, farmer_on=farmer_on, grant=grant)
    loop_steps = len(x.j.trace(KEY))
    tick(x)
    assert phase(x.j) == "aborted"
    assert result_of(x.j)["no_dialog_abort_verified"] is True
    assert len(x.j.trace(KEY)) == loop_steps + 1
    assert refill_request(x) is None or refill_request(x)["request_id"] != KEY
    assert inputs(game) == [] and x.cancel_dispatches == []
    if not farmer_on:
        assert x.handoffs == []


# N9: out of this path's scope ---------------------------------------------


def test_live_worker_is_never_settled(tmp_path, monkeypatch):
    game = base.Game(dialog=False)
    x = live_setup(tmp_path, monkeypatch, game)
    monkeypatch.setitem(listing.WORKERS, KEY, NS(is_alive=lambda: True))
    assert cancel.settle_without_dialog(x.ui, KEY, "Dutch") is False
    result = tick(x)
    assert result["blocker"] == "listing_receipt_needs_reconciliation"
    assert phase(x.j) == "uncertain" and game.memory_reads == 0
    assert inputs(game) == []


def test_operator_receipt_is_never_settled(tmp_path, monkeypatch):
    game = base.Game(dialog=False)
    x = live_setup(tmp_path, monkeypatch, game)
    before = {**x.before, "scheduled_foreground_refill": False}
    with x.j.db() as db:
        db.execute(
            "UPDATE transactions SET before_json=? WHERE id=?",
            (json.dumps(before, sort_keys=True), KEY),
        )
    assert cancel.settle_without_dialog(x.ui, KEY, "Dutch") is False
    tick(x)
    assert phase(x.j) == "uncertain" and inputs(game) == []


def test_uncertain_row_not_written_by_the_listing_worker_is_not_settled(
    tmp_path, monkeypatch
):
    game = base.Game(dialog=False)
    # A prepared row (crash mid-drag) later marked uncertain by a Cancel
    # worker failure: the drag's finally mouse-up is not proven.
    x = live_setup(
        tmp_path, monkeypatch, game, result={**LOOP_FAILURE}, loop_evidence=1
    )
    assert cancel.settle_without_dialog(x.ui, KEY, "Dutch") is False
    for _ in range(2):
        tick(x)
    assert phase(x.j) == "uncertain"
    assert inputs(game) == [] and x.cancel_dispatches == []


# N10: tampered no-dialog receipt never releases the cursor ------------------


@pytest.mark.parametrize(
    "tamper",
    [
        "second_inventory",
        "first_booth",
        "purchase_silver",
        "cancellation_flag",
        "listing_flag",
        "injected_drag_release",
        "injected_cancel_press",
    ],
)
def test_tampered_no_dialog_receipt_keeps_refill_blocked(tmp_path, monkeypatch, tamper):
    game = base.Game(dialog=False)
    x = live_setup(tmp_path, monkeypatch, game)
    game.buy(*base.INCIDENT_SALES)
    assert cancel.settle_without_dialog(x.ui, KEY, "Dutch") is True
    with x.j.db() as db:
        if tamper.startswith("injected_"):
            db.execute(
                "INSERT INTO transaction_steps(transaction_id,stage,status,payload,"
                "timestamp) VALUES(?,?,?,?,?)",
                (KEY, tamper[len("injected_") :], "before_action", "{}", time.time()),
            )
        else:
            result = json.loads(
                db.execute(
                    "SELECT result_json FROM transactions WHERE id=?", (KEY,)
                ).fetchone()[0]
            )
            if tamper == "second_inventory":
                result["second"]["inventory"] = result["second"]["inventory"][1:]
            elif tamper == "first_booth":
                result["first"]["booth"] = deepcopy(result["second"]["booth"])[1:]
            elif tamper == "purchase_silver":
                result["player_purchases"]["silver"] += 1
            elif tamper == "cancellation_flag":
                result["cancellation_attempted"] = True
            else:
                result["listing_submitted"] = True
            db.execute(
                "UPDATE transactions SET result_json=? WHERE id=?",
                (json.dumps(result), KEY),
            )
    result = refill.step(x.ui, "Dutch", game.ownership())
    assert result["blocker"] == "listing_receipt_needs_reconciliation"
    assert x.dispatched == []
    assert refill_request(x) == REQUEST
