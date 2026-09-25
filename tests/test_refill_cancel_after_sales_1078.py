"""Scheduled 1078 refill Cancel after player purchases during the listing hold.

Live incident 2026-09-25 (Dutch, booth-list1078-refill-984ef4df...): a farmer
threat stopped a scheduled listing after drag_press. The exact "Add Item to
Booth" dialog stayed open while players bought four booth items. Every Cancel
admission compared ownership against the pre-listing baseline, so it failed
forever, refill stayed blocked, and the purchases were never journaled.

Failure modes, written before the fix; each one is exercised below. Only
process memory reads, the native window/focus/layout surface and the input
worker are faked. The refill step, Cancel admission, dispatch, worker policy,
Cancel reconciliation, cursor settlement and sales journal are the real code.

F1  Sales during the hold with the exact dialog open: Cancel is admitted,
    pressed once, settles aborted; refill resumes with a fresh request ID;
    the purchases are journaled exactly once as one verified sale. (E2E; a
    repeatable JSON artifact is written twice and must be byte-identical.)
F2  A booth item was added (the listing may have been submitted): blocked,
    no Cancel.
F3  Inventory changed, or the listed item left inventory: blocked.
F4  Silver decreased, did not change, or the gain does not match the removed
    items' listed prices net of the 3% booth deduction: blocked.
F5  A removed booth item plus a price (or any non-slot) change on a remaining
    item: blocked.
F6  Slot indexes changed while nothing was removed: blocked (compaction must
    be explained by a purchase).
F7  An open trade/request window during the hold: blocked.
F8  No sales (existing exact path): pressed once, settled, refill resumes and
    the settlement writes no sale or gap rows of its own.
F9  A purchase lands between admission and the Cancel mouse-down: the worker
    re-verifies with the same tolerance and still presses once; an added booth
    item landing there prevents the press.
F10 Dialog closed after drag_press (the drag never landed) with sales: no
    Cancel press, the hold stays unresolved, and no sale is invented.
F11 Pre-press pointer-only failure (no input at all) with sales: aborted with
    the same purchase proof, and the sale is journaled exactly once.
F12 Restart after the durable Cancel marker but before mouse-down: Cancel is
    never pressed again.
F13 Restart after Cancel landed but before settlement: read-only settlement
    applies the tolerance; still exactly one press and one sale receipt.
F14 The sales baseline cannot prove the hold interval (it holds a pending
    anchor or differs from the listing baseline): Cancel still settles, but a
    labelled observation gap is recorded instead of a sale.
F15 A tampered settlement receipt (purchase evidence or snapshots) never
    releases the refill.
F16 Post-settlement sales observation and repeated reconciliation never
    double count the hold's purchases.
"""

from contextlib import contextmanager, nullcontext
from copy import deepcopy
import json
import threading
import time
from types import SimpleNamespace as NS

import pytest

from conquest.capture import CaptureUnavailable
from conquest.memory_build_layout import CLIENT_SHA256_1078
from conquest.merchants import booth_listing_cancel_1078 as cancel
from conquest.merchants import booth_listing_once_1078 as listing
from conquest.merchants import listing_capability_1078 as capability
from conquest.merchants import refill_1078 as refill
from conquest.merchants import sales
from conquest.merchants.journal import Journal
from conquest.merchants.listing_preflight_1078 import _MODAL_RENDER_SHA256
from test_merchant_listing_capability_1078 import make_ui

KEY = "booth-list1078-refill-984ef4dff7af454da9bb71dfdf2d2959"
IDENTITY = {"pid": 1, "creation_time_100ns": 2, "path": "C:/ImConquer.exe"}
LISTED = 296650414
OLD = 1_790_356_509.0  # fixed listing-baseline time keeps the artifact stable
START_SILVER = 11_049_896


def stock(uid, name, type_id, plus, slot, price=None):
    return dict(
        uid=uid,
        type_id=type_id,
        name=name,
        plus=plus,
        gem1=0,
        gem2=0,
        bound=False,
        quantity=1,
        slot=slot,
        price=price,
    )


def ownership():
    """The incident's shape, reduced: six booth rows and the listed scroll."""
    return dict(
        client_sha256=CLIENT_SHA256_1078,
        identity=dict(IDENTITY),
        character="Dutch",
        character_uid=8,
        server="America",
        map_id=1036,
        position=[232, 205],
        hp=93,
        silver=START_SILVER,
        capacity=40,
        inventory=[
            stock(LISTED, "MeteorScroll", 720027, 0, 0),
            stock(296793258, "DestinyCap", 114506, 1, 1),
        ],
        booth=[
            stock(295190266, "OxhideBoots", 160013, 2, 0, 6_930_000),
            stock(296640192, "Coat", 132705, 1, 1, 50_000),
            stock(296641679, "GuardCoronet", 118504, 1, 2, 49_500),
            stock(294733803, "WarriorHook", 410013, 2, 3, 1_000_000),
            stock(296630173, "JadeCoronet", 118545, 1, 4, 49_500),
            stock(296759224, "Coat", 132505, 1, 5, 50_000),
        ],
        own_booth_uid=18,
        booth_open=True,
        trade=None,
        request=None,
    )


# The four incident purchases: Coat, GuardCoronet, JadeCoronet, Coat.
INCIDENT_SALES = (296640192, 296641679, 296630173, 296759224)

REQUEST = {
    "action": "merchant-booth-list-once-1078",
    "character": "Dutch",
    "request_id": KEY,
    "item_uid": LISTED,
    "item_fingerprint": {
        "uid": LISTED,
        "type_id": 720027,
        "name": "MeteorScroll",
        "plus": 0,
        "gem1": 0,
        "gem2": 0,
        "bound": False,
        "quantity": 1,
    },
    "price": 750_000,
    "expected_identity": dict(IDENTITY),
    "expected_character_uid": 8,
    "expected_own_booth_uid": 18,
}


class Game:
    """Fake client memory/window state; only the fakes below read it."""

    def __init__(self, *, dialog=True):
        self.state = ownership()
        self.dialog = dialog
        self.trace = []
        self.before_mouse_down = []
        self.crash = None

    def ownership(self):
        return {**deepcopy(self.state), "timestamp": time.time()}

    def buy(self, *uids):
        booth = self.state["booth"]
        removed = [row for row in booth if row["uid"] in uids]
        assert len(removed) == len(uids)
        booth[:] = [row for row in booth if row["uid"] not in uids]
        for slot, row in enumerate(booth):
            row["slot"] = slot
        self.state["silver"] += sum(row["price"] * 97 // 100 for row in removed)
        self.trace.append(
            {
                "event": "player_purchase",
                "uids": sorted(uids),
                "silver_after": self.state["silver"],
            }
        )


class Coordinator:
    fence = None
    stopped = False
    purpose = None
    owner = None

    def __init__(self):
        self.policies = []

    def manual_active(self):
        return False

    def manual_session_blocked(self, character):
        return False

    @contextmanager
    def booth_listing_once_scope(self, character, policy):
        policy()
        self.policies.append(policy)
        try:
            yield
        finally:
            self.policies.remove(policy)

    @contextmanager
    def lease(self, character, purpose):
        yield

    def check(self):
        for policy in list(self.policies):
            policy()


def install(monkeypatch, tmp_path, game):
    from conquest import (
        desktop_runtime,
        focus_recovery,
        foreground,
        input_probe,
        layout_revision,
        protected_withdrawal,
    )
    from conquest.merchants import (
        delivery_operation,
        delivery_probe,
        listing_preflight_1078,
        trade_qualification_prep,
    )

    # Farmer holds are read from files; keep them inside this test.
    monkeypatch.setattr(protected_withdrawal, "JOURNAL", tmp_path / "pw.sqlite3")
    monkeypatch.setattr(delivery_operation, "JOURNAL", tmp_path / "md.sqlite3")
    monkeypatch.setattr(delivery_probe, "JOURNAL", tmp_path / "probe.json")
    monkeypatch.setattr(trade_qualification_prep, "JOURNAL", tmp_path / "prep.json")
    monkeypatch.setattr(
        listing,
        "_profile",
        lambda character: NS(
            id="test-dutch", name="Dutch", server="America", character_uid=8
        ),
    )
    # Farmer parking proof is a farmer memory read.
    monkeypatch.setattr(
        listing,
        "_farmer_safe_market",
        lambda ui, target=None, deadline=None: {"pid": 10, "hwnd": 11},
    )

    class Session:
        identity = dict(IDENTITY)
        expected_sha256 = CLIENT_SHA256_1078

        def assert_identity(self):
            pass

        def read(self, address, size):
            # Model +0x50 holds the pending selection while the dialog is open.
            return bytes(size) if not game.dialog else b"\x01" + bytes(size - 1)

    session = Session()
    gui = NS(
        session=session,
        model=lambda index, vtable: 0x1000,
        windows=lambda: (
            ([{"name": "Add Item to Booth", "address": 0x5000}] if game.dialog else [])
            + [{"name": "Booth", "address": 0x4000}]
        ),
        assert_hovered=lambda window, label: None,
        viewport_size=lambda: (1024, 768),
    )
    for module in (cancel, listing):
        monkeypatch.setattr(
            module, "MemorySession", lambda pid, sha: nullcontext(session)
        )
        monkeypatch.setattr(
            module,
            "open_read_only_1078",
            lambda session, name: NS(read_manual_ownership=game.ownership),
        )
        monkeypatch.setattr(
            module, "read_build_layout", lambda session: NS(merchant_booth_vtable_rva=2)
        )
        monkeypatch.setattr(module, "GuiReader", NS(for_session=lambda session: gui))
    monkeypatch.setattr(cancel, "unpack", lambda session, address, fmt: (18,))

    def modal(gui, model, snapshot, uid):
        if not game.dialog:
            raise ValueError("Add Item to Booth window is not open")
        if uid != LISTED:
            raise ValueError("Price dialog selected another item")
        return (
            {"name": "Add Item to Booth", "address": 0x5000},
            {
                "##Amount": (300.0, 280.0),
                "OK": (240.0, 300.0),
                "Cancel": (400.0, 300.0),
            },
            "",
        )

    monkeypatch.setattr(listing, "_modal", modal)
    monkeypatch.setattr(
        listing_preflight_1078, "_modal_code_verified", lambda session, layout: None
    )
    monkeypatch.setattr(desktop_runtime, "physical_coordinates", nullcontext)
    monkeypatch.setattr(
        focus_recovery,
        "activate_client",
        lambda hwnd, identity: game.trace.append({"event": "native_focus"}) or True,
    )

    class Target:
        def __init__(self, pid, hwnd):
            self.pid, self.hwnd = pid, hwnd

        def snapshot(self):
            return {"root_hwnd": self.hwnd, "foreground": self.hwnd}

    monkeypatch.setattr(input_probe, "MessageTarget", Target)

    class Layout:
        def __init__(self, target, windows, gui_size):
            pass

        def stable(self):
            return NS(client_size=(1024, 768), gui_size=(1024, 768))

        def assert_current(self, revision):
            pass

    monkeypatch.setattr(layout_revision, "SharedLayoutRevision", Layout)

    def click(
        target,
        x,
        y,
        size,
        *,
        require_foreground=False,
        before_press=None,
        before_mouse_down=None,
        layout_guard=None,
        **kwargs,
    ):
        before_press()
        for hook in game.before_mouse_down:
            hook()
        layout_guard()
        before_mouse_down()
        if game.crash == "before_mouse_down":
            raise OSError("simulated app crash after the durable Cancel marker")
        game.trace.append({"event": "cancel_click", "point": [x, y]})
        game.dialog = False
        if game.crash == "after_click":
            raise OSError("simulated app crash before Cancel settlement")

    monkeypatch.setattr(foreground, "foreground_click", click)
    dispatched = []
    monkeypatch.setattr(
        listing,
        "dispatch",
        lambda ui, body, scheduled_refill=False: (
            dispatched.append(dict(body)) or {"phase": "prepared"}
        ),
    )
    monkeypatch.setattr(
        "conquest.merchants.listing_plan_1078.plan",
        lambda runtime, character, snapshot: [{"uid": LISTED, "price": 750_000}],
    )
    return dispatched


def qualify(journal):
    """The earlier verified live listing that qualifies this input path."""
    item = stock(90, "Coat", 130805, 2, 0)
    base = {**ownership(), "inventory": [item], "booth": [], "timestamp": time.time()}
    request = {
        **REQUEST,
        "request_id": "booth-list1078-capability01",
        "item_uid": 90,
        "item_fingerprint": listing._item_fingerprint(item),
        "price": 73_000,
    }
    journal.begin(
        request["request_id"],
        "Dutch",
        listing.KIND,
        {
            "request": request,
            "snapshot": base,
            "profile_id": "test-dutch",
            "client_sha256": CLIENT_SHA256_1078,
            "listing_engine_revision": capability.ENGINE_REVISION,
            "control": {"enabled": False, "paused": False, "revision": 1},
            "farmer_target": {"pid": 10, "hwnd": 11},
        },
    )
    for stage, status, payload in (
        ("baseline", "verified", {}),
        ("drag_press", "before_action", {}),
        (
            "native_dialog",
            "verified",
            {"uid": 90, "renderer_sha256": _MODAL_RENDER_SHA256},
        ),
        ("amount_press", "before_action", {}),
        ("price", "verified", {}),
        (
            "confirm_press",
            "before_mouse_down",
            {"uid": 90, "price": 73_000, "owned_booth_uid": 18},
        ),
    ):
        journal.step(request["request_id"], stage, status, payload)
    after = {
        **deepcopy(base),
        "inventory": [],
        "booth": [{**item, "price": 73_000}],
        "timestamp": time.time(),
    }
    capability.settle(
        journal, request["request_id"], after, {**after, "timestamp": time.time()}
    )


def listing_baseline():
    """Original preflight shape: closed-window booleans, no trade/request."""
    snapshot = {**ownership(), "timestamp": OLD}
    del snapshot["trade"], snapshot["request"]
    snapshot.update(closed_modal=True, trade_open=False, request_open=False)
    return snapshot


def setup(tmp_path, monkeypatch, game, *, pre_press=False):
    dispatched = install(monkeypatch, tmp_path, game)
    journal = Journal(tmp_path / "journal.sqlite3")
    qualify(journal)
    baseline = listing_baseline()
    # The last sales observation before the hold began (runtime order).
    sales.observe(
        journal,
        {**ownership(), "timestamp": OLD - 2},
    )
    before = {
        "request": deepcopy(REQUEST),
        "profile_id": "test-dutch",
        "hwnd": 55,
        "control": {"enabled": False, "paused": False},
        "client_sha256": CLIENT_SHA256_1078,
        "farmer_target": {"pid": 10, "hwnd": 11},
        "snapshot": baseline,
        "merchant_intent": {"operations": False, "refill": True},
        "price_plan": {"uid": LISTED, "price": 750_000},
        "routine_refill_permitted": False,
        "listing_engine_revision": capability.ENGINE_REVISION,
        "scheduled_foreground_refill": True,
        "farmer_grant": None,
    }
    journal.begin(KEY, "Dutch", listing.KIND, before)
    ui = make_ui(journal, {"identity": dict(IDENTITY)})
    runtime = ui.runtime
    runtime.stop_event = threading.Event()
    runtime.manual_handoff_status = lambda: None
    runtime.delivery_window = runtime.refill_window = None
    runtime.refilling = runtime.connecting = False
    runtime.farmer_bot_owned = lambda: False
    ui.closed = ui.calibrating = False
    ui.grant = None
    ui.app.closing = False
    ui.coordinator = Coordinator()
    state = runtime.refills["Dutch"].state()
    state.update(
        pending=True,
        status="paused_budget",
        listed=0,
        cursor=[LISTED],
        listing1078_engine=1,
        listing1078_request=deepcopy(REQUEST),
    )
    journal.set("Dutch", "refill", state)
    if not pre_press:
        for stage, status, payload in (
            ("foreground", "before_action", {"hwnd": 55}),
            ("foreground", "verified", {"hwnd": 55}),
            ("baseline", "verified", {"uid": LISTED, "price": 750_000}),
            ("drag_pointer", "before_action", {}),
            ("drag_press", "before_action", {}),
        ):
            journal.step(KEY, stage, status, payload)
        journal.transition(
            KEY,
            "uncertain",
            {
                "input_attempted": True,
                "confirmation_attempted": False,
                "unchanged_ownership_verified": False,
                "replay_allowed": False,
                "reason": "1078 listing input qualification failed: Farmer handoff "
                "rejected: nearby_living_or_unknown_monster",
            },
        )
    return NS(j=journal, ui=ui, before=before, dispatched=dispatched, game=game)


def join_workers():
    for thread in threading.enumerate():
        if thread.name in ("booth-1078-cancel-once", "booth-1078-list-once"):
            thread.join(20)
            assert not thread.is_alive()


def tick(x):
    """One runtime observer tick in production order (runtime.py)."""
    snapshot = x.game.ownership()
    if not x.j.pending("Dutch"):
        sales.observe(x.j, snapshot)
    result = refill.step(x.ui, "Dutch", snapshot)
    join_workers()
    return result


def clicks(game):
    return [row for row in game.trace if row["event"] == "cancel_click"]


def sale_rows(journal):
    with journal.db() as db:
        return [
            {
                "phase": row["phase"],
                "items": json.loads(row["items"]),
                "silver": row["silver"],
                "note": row["note"],
            }
            for row in db.execute("SELECT * FROM sales ORDER BY id")
        ]


def sales_events(journal):
    with journal.db() as db:
        return [
            {"event": row["event"], "payload": json.loads(row["payload"])}
            for row in db.execute(
                "SELECT event,payload FROM events WHERE event IN "
                "('sale_verified','sale_unconfirmed','sales_observation_gap') "
                "ORDER BY id"
            )
        ]


def phase(journal):
    return listing._row(journal, KEY)["phase"]


def markers(journal):
    return [
        step["stage"]
        for step in journal.trace(KEY)
        if step["stage"] in ("cancel_press", "confirm_press")
    ]


VOLATILE = {"timestamp", "observed_at", "created", "updated", "at", "to"}


def normalized(value):
    if isinstance(value, dict):
        return {
            key: normalized(item) for key, item in value.items() if key not in VOLATILE
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


NET_INCIDENT = 193_030  # 2 x 48,500 + 2 x 48,015 (3% deduction, exact)


# F1 + F16 (end to end, repeatable artifact) -------------------------------


def run_incident(root, monkeypatch):
    root.mkdir()
    game = Game(dialog=True)
    x = setup(root, monkeypatch, game)
    game.buy(*INCIDENT_SALES)
    ticks = [tick(x)]  # admission -> dispatch_cancel -> worker -> settlement
    ticks.append(tick(x))  # sales.observe resumes; cursor settles; fresh ID
    # F16: later observations and repeated read-only reconciliation.
    for _ in range(2):
        sales.observe(x.j, game.ownership())
    cancel.reconcile_cancel(x.ui, KEY, "Dutch")
    row = listing._row(x.j, KEY)
    state = x.ui.runtime.refills["Dutch"].state()
    artifact = {
        "scenario": "incident_sales_during_open_dialog_hold",
        "request": {"request_id": KEY, "item_uid": LISTED, "price": 750_000},
        "game_trace": game.trace,
        # A tick's receipt phase races the worker thread; state is stable.
        "ticks": [
            {"state": row.get("state"), "blocker": row.get("blocker")} for row in ticks
        ],
        "transaction": {
            "phase": row["phase"],
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
        "sales": sale_rows(x.j),
        "sales_events": normalized(sales_events(x.j)),
        "refill_after": normalized(
            {
                key: state.get(key)
                for key in ("pending", "listed", "listing1078_request")
            }
        ),
        "fresh_dispatches": normalized(x.dispatched),
        "silver": {"before": START_SILVER, "after": game.state["silver"]},
    }
    path = root / "refill-cancel-after-sales-e2e.json"
    path.write_text(json.dumps(artifact, indent=2, sort_keys=True), encoding="utf-8")
    return path


def test_incident_sales_during_hold_cancel_settles_and_sales_journaled_once(
    tmp_path, monkeypatch
):
    first = run_incident(tmp_path / "first", monkeypatch)
    second = run_incident(tmp_path / "second", monkeypatch)
    assert first.read_bytes() == second.read_bytes()
    artifact = json.loads(first.read_text(encoding="utf-8"))
    # Cancel admitted and pressed exactly once; the hold settled aborted.
    assert [row["event"] for row in artifact["game_trace"]] == [
        "player_purchase",
        "native_focus",
        "cancel_click",
    ]
    assert artifact["ticks"][0]["state"] == "listing_cancel_pending"
    transaction = artifact["transaction"]
    assert transaction["phase"] == "aborted"
    stages = [step["stage"] for step in transaction["steps"]]
    assert stages.count("cancel_press") == 1 and "confirm_press" not in stages
    result = transaction["result"]
    assert result["cancel_verified"] is True
    assert result["listing_submitted"] is False
    assert result["replay_allowed"] is False
    assert sorted(i["uid"] for i in result["player_purchases"]["items"]) == sorted(
        INCIDENT_SALES
    )
    assert result["player_purchases"]["silver"] == NET_INCIDENT
    # Refill resumed with a fresh request for the still-held scroll.
    assert artifact["ticks"][1]["state"] == "listing_pending"
    assert artifact["refill_after"]["listing1078_request"]["request_id"] == (
        "<fresh-refill-request-id>"
    )
    assert artifact["refill_after"]["listed"] == 0
    assert len(artifact["fresh_dispatches"]) == 1
    assert artifact["fresh_dispatches"][0]["item_uid"] == LISTED
    # F16: exactly one verified sale, silver equal to the actual balance delta.
    assert artifact["silver"]["after"] - START_SILVER == NET_INCIDENT
    assert len(artifact["sales"]) == 1
    receipt = artifact["sales"][0]
    assert receipt["phase"] == "verified" and receipt["silver"] == NET_INCIDENT
    assert sorted(i["uid"] for i in receipt["items"]) == sorted(INCIDENT_SALES)
    assert [e["event"] for e in artifact["sales_events"]] == ["sale_verified"]
    event = artifact["sales_events"][0]["payload"]
    assert event["listing_hold_transaction_id"] == KEY
    assert event["from"] == OLD - 2
    assert event["deduction"] == 199_000 - NET_INCIDENT


# F2-F7: anything not provably a player purchase keeps the hold ------------


def mutate(game, mutation):
    state = game.state
    if mutation == "booth_item_added":
        game.buy(296640192)
        state["booth"].append(stock(299, "Blade", 410020, 1, 5, 80_000))
    elif mutation == "listed_item_moved_to_booth":
        game.buy(296640192)
        listed = state["inventory"].pop(0)
        state["booth"].append({**listed, "slot": 5, "price": 750_000})
    elif mutation == "inventory_changed":
        game.buy(296640192)
        state["inventory"][1]["quantity"] = 2
    elif mutation == "inventory_gained":
        game.buy(296640192)
        state["inventory"].append(stock(300, "Coat", 132705, 1, 2))
    elif mutation == "listed_item_gone":
        game.buy(296640192)
        state["inventory"].pop(0)
    elif mutation == "silver_decreased":
        game.buy(296640192)
        state["silver"] = START_SILVER - 1_000
    elif mutation == "silver_unchanged":
        game.buy(296640192)
        state["silver"] = START_SILVER
    elif mutation == "silver_over_net":
        game.buy(296640192)
        state["silver"] += 1
    elif mutation == "silver_under_net":
        game.buy(296640192)
        state["silver"] -= 1
    elif mutation == "silver_gross_not_net":
        game.buy(296640192)
        state["silver"] = START_SILVER + 50_000
    elif mutation == "remaining_price_changed":
        game.buy(296640192)
        state["booth"][0]["price"] += 1
    elif mutation == "remaining_plus_changed":
        game.buy(296640192)
        state["booth"][0]["plus"] += 1
    elif mutation == "remaining_field_added":
        game.buy(296640192)
        state["booth"][0]["unknown_native_field"] = 1
    elif mutation == "slot_only_no_sale":
        for row in state["booth"]:
            row["slot"] += 1
    elif mutation == "trade_open":
        game.buy(296640192)
        state["trade"] = {"participant": "Visitor", "participant_uid": 5}
    elif mutation == "request_open":
        game.buy(296640192)
        state["request"] = {"participant": "Visitor", "participant_uid": 5}
    elif mutation == "own_booth_changed":
        game.buy(296640192)
        state["own_booth_uid"] = 19
    elif mutation == "position_changed":
        game.buy(296640192)
        state["position"] = [233, 205]
    else:
        raise AssertionError(mutation)


@pytest.mark.parametrize(
    "mutation",
    [
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
        "remaining_plus_changed",
        "remaining_field_added",
        "slot_only_no_sale",
        "trade_open",
        "request_open",
        "own_booth_changed",
        "position_changed",
    ],
)
def test_unproven_change_keeps_the_hold_without_cancel(tmp_path, monkeypatch, mutation):
    game = Game(dialog=True)
    x = setup(tmp_path, monkeypatch, game)
    mutate(game, mutation)
    result = tick(x)
    assert result["blocker"] == "listing_receipt_needs_reconciliation"
    assert phase(x.j) == "uncertain"
    assert not clicks(game) and markers(x.j) == []
    assert not any(s["stage"] == "cancel_requested" for s in x.j.trace(KEY))
    assert sale_rows(x.j) == [] and x.dispatched == []
    state = x.ui.runtime.refills["Dutch"].state()
    assert state["listing1078_request"] == REQUEST
    # The pure rule agrees with the refill path.
    with pytest.raises(ValueError):
        refill.pending_cancel(x.ui.runtime, "Dutch", game.ownership())


# F8: existing exact path unchanged ----------------------------------------


def test_no_sales_exact_path_is_unchanged(tmp_path, monkeypatch):
    game = Game(dialog=True)
    x = setup(tmp_path, monkeypatch, game)
    with x.j.db() as db:
        events_before = db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    assert tick(x)["state"] == "listing_cancel_pending"
    assert phase(x.j) == "aborted" and len(clicks(game)) == 1
    result = json.loads(listing._row(x.j, KEY)["result_json"])
    assert "player_purchases" not in result
    with x.j.db() as db:
        # Settlement itself writes no sale/gap rows on the exact path.
        assert db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == events_before
    assert sale_rows(x.j) == []
    assert tick(x)["state"] == "listing_pending"
    assert len(x.dispatched) == 1 and x.dispatched[0]["request_id"] != KEY
    assert sale_rows(x.j) == []


# F9: purchase or foreign change between admission and mouse-down ----------


def test_purchase_before_mouse_down_is_tolerated_and_pressed_once(
    tmp_path, monkeypatch
):
    game = Game(dialog=True)
    x = setup(tmp_path, monkeypatch, game)
    game.buy(296640192)
    game.before_mouse_down.append(lambda: game.buy(296641679))
    assert tick(x)["state"] == "listing_cancel_pending"
    assert phase(x.j) == "aborted" and len(clicks(game)) == 1
    assert tick(x)["state"] == "listing_pending"
    rows = sale_rows(x.j)
    assert len(rows) == 1 and rows[0]["silver"] == 48_500 + 48_015
    assert sorted(i["uid"] for i in rows[0]["items"]) == [296640192, 296641679]


def test_added_booth_item_before_mouse_down_prevents_cancel(tmp_path, monkeypatch):
    game = Game(dialog=True)
    x = setup(tmp_path, monkeypatch, game)
    game.buy(296640192)
    game.before_mouse_down.append(
        lambda: game.state["booth"].append(stock(299, "Blade", 410020, 1, 5, 80_000))
    )
    tick(x)
    assert not clicks(game) and markers(x.j) == []
    assert phase(x.j) == "uncertain"
    failure = [s for s in x.j.trace(KEY) if s["stage"] == "cancel_failed"]
    assert len(failure) == 1 and failure[0]["status"] == "before_press"
    assert sale_rows(x.j) == [] and x.dispatched == []


# F10: dialog closed after drag_press (drag never landed) + sales ----------


def test_closed_dialog_after_drag_press_with_sales_never_cancels_or_invents(
    tmp_path, monkeypatch
):
    game = Game(dialog=False)
    x = setup(tmp_path, monkeypatch, game)
    game.buy(*INCIDENT_SALES)
    for _ in range(2):
        result = tick(x)
        assert result.get("blocker") == "listing_receipt_needs_reconciliation" or (
            result.get("state") == "listing_cancel_pending"
        )
    assert not clicks(game) and markers(x.j) == []
    assert phase(x.j) == "uncertain"
    assert sale_rows(x.j) == [] and x.dispatched == []
    assert x.ui.runtime.refills["Dutch"].state()["listing1078_request"] == REQUEST


# F11: pre-press pointer-only failure + sales ------------------------------


def test_pre_press_failure_with_sales_aborts_with_purchase_proof(tmp_path, monkeypatch):
    game = Game(dialog=False)
    x = setup(tmp_path, monkeypatch, game, pre_press=True)
    game.buy(296640192, 296759224)

    def threat(*args, **kwargs):
        raise CaptureUnavailable("Farmer handoff rejected: nearby monster")

    monkeypatch.setattr(listing, "_farmer_safe_market", threat)
    listing._run(x.ui, "Dutch", listing._profile("Dutch"), x.before, None)
    row = listing._row(x.j, KEY)
    result = json.loads(row["result_json"])
    assert row["phase"] == "aborted"
    assert result["input_attempted"] is False
    assert result["unchanged_ownership_verified"] is True
    assert result["player_purchases"]["silver"] == 2 * 48_500
    assert not clicks(game) and markers(x.j) == []
    rows = sale_rows(x.j)
    assert len(rows) == 1 and rows[0]["phase"] == "verified"
    assert rows[0]["silver"] == 2 * 48_500
    sales.observe(x.j, game.ownership())
    assert len(sale_rows(x.j)) == 1


def test_pre_press_failure_with_unproven_change_stays_uncertain(tmp_path, monkeypatch):
    game = Game(dialog=False)
    x = setup(tmp_path, monkeypatch, game, pre_press=True)
    mutate(game, "booth_item_added")

    def threat(*args, **kwargs):
        raise CaptureUnavailable("Farmer handoff rejected: nearby monster")

    monkeypatch.setattr(listing, "_farmer_safe_market", threat)
    listing._run(x.ui, "Dutch", listing._profile("Dutch"), x.before, None)
    assert phase(x.j) == "uncertain"
    assert sale_rows(x.j) == []


# F12/F13: restart around the Cancel boundary ------------------------------


def test_restart_after_cancel_marker_before_mouse_down_never_presses(
    tmp_path, monkeypatch
):
    game = Game(dialog=True)
    x = setup(tmp_path, monkeypatch, game)
    game.buy(*INCIDENT_SALES)
    game.crash = "before_mouse_down"
    tick(x)
    assert markers(x.j) == ["cancel_press"] and not clicks(game)
    game.crash = None
    for _ in range(3):  # "restarted" app keeps observing
        result = tick(x)
        assert result["blocker"] == "listing_receipt_needs_reconciliation"
    assert markers(x.j) == ["cancel_press"] and not clicks(game)
    assert phase(x.j) == "uncertain"
    assert sale_rows(x.j) == [] and x.dispatched == []


def test_restart_after_cancel_landed_settles_read_only_with_one_receipt(
    tmp_path, monkeypatch
):
    game = Game(dialog=True)
    x = setup(tmp_path, monkeypatch, game)
    game.buy(*INCIDENT_SALES)
    game.crash = "after_click"
    tick(x)
    assert len(clicks(game)) == 1 and phase(x.j) == "uncertain"
    game.crash = None
    # Restarted app: read-only reconciliation settles, then the cursor
    # releases and a fresh ID is dispatched in the same observer tick.
    assert tick(x)["state"] == "listing_pending"
    assert phase(x.j) == "aborted"
    assert len(clicks(game)) == 1 and markers(x.j) == ["cancel_press"]
    assert len(x.dispatched) == 1
    rows = sale_rows(x.j)
    assert len(rows) == 1 and rows[0]["silver"] == NET_INCIDENT


# F14: baseline cannot prove the hold interval ------------------------------


@pytest.mark.parametrize("baseline", ["pending_anchor", "different_booth"])
def test_unprovable_sales_interval_records_gap_not_sale(
    tmp_path, monkeypatch, baseline
):
    game = Game(dialog=True)
    x = setup(tmp_path, monkeypatch, game)
    with x.j.db() as db:
        saved = json.loads(
            db.execute("SELECT snapshot FROM sales_baseline").fetchone()[0]
        )
        if baseline == "pending_anchor":
            saved["_sales_anchor"] = deepcopy(saved)
            saved["_sales_pending_since"] = OLD - 2
        else:
            saved["booth"] = saved["booth"][1:]
        db.execute("UPDATE sales_baseline SET snapshot=?", (json.dumps(saved),))
    game.buy(*INCIDENT_SALES)
    tick(x)
    assert phase(x.j) == "aborted" and len(clicks(game)) == 1
    tick(x)
    assert len(x.dispatched) == 1
    assert sale_rows(x.j) == []
    events = sales_events(x.j)
    assert [e["event"] for e in events] == ["sales_observation_gap"]
    assert events[0]["payload"]["transaction_id"] == KEY
    assert events[0]["payload"]["reason"]


# F15: tampered settlement receipt never releases refill -------------------


@pytest.mark.parametrize(
    "tamper", ["purchase_silver", "second_added", "first_inventory"]
)
def test_tampered_settlement_receipt_keeps_refill_blocked(
    tmp_path, monkeypatch, tamper
):
    game = Game(dialog=True)
    x = setup(tmp_path, monkeypatch, game)
    game.buy(*INCIDENT_SALES)
    tick(x)
    assert phase(x.j) == "aborted"
    with x.j.db() as db:
        result = json.loads(
            db.execute(
                "SELECT result_json FROM transactions WHERE id=?", (KEY,)
            ).fetchone()[0]
        )
        if tamper == "purchase_silver":
            result["player_purchases"]["silver"] += 1
        elif tamper == "second_added":
            result["second"]["booth"].append(stock(299, "Blade", 410020, 1, 9, 1))
        else:
            result["first"]["inventory"] = []
        db.execute(
            "UPDATE transactions SET result_json=? WHERE id=?",
            (json.dumps(result), KEY),
        )
    result = refill.step(x.ui, "Dutch", game.ownership())
    assert result["blocker"] == "listing_receipt_needs_reconciliation"
    assert x.dispatched == []
    assert x.ui.runtime.refills["Dutch"].state()["listing1078_request"] == REQUEST
