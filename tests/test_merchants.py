import copy
from dataclasses import replace
import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace
import pytest

from conquest.capture import CaptureUnavailable
from conquest.merchants.pricing import (
    ItemKey,
    Listing,
    price_item,
    socket_name,
    parse_booth_price,
    wait_booth_price,
)
from conquest.merchants.journal import Journal
from conquest.merchants.coordination import InputCoordinator, install, check_input
from conquest.merchants.controller import MerchantController, validate_trade, received
from conquest.merchants.market import MarketSnapshot


@pytest.mark.parametrize(
    "raw,expected",
    [(b"123,456", 123456), (b"123456", 123456), (b"999,999,999", 999999999), (b"1", 1)],
)
def test_native_booth_price_format(raw, expected):
    assert parse_booth_price(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"0",
        b"12,34",
        b"123,456x",
        b"-123",
        b"1,000,000,000",
        b"123 456",
        b"123.456",
    ],
)
def test_invalid_native_booth_price_is_never_accepted(raw):
    with pytest.raises(ValueError):
        parse_booth_price(raw)


@pytest.mark.parametrize("mode", ["renders", "never", "stopped"])
def test_price_verification_waits_for_render_without_repeating_input(mode):
    elapsed = [0.0]
    checks = []

    def sleep(seconds):
        elapsed[0] += seconds

    def read():
        return b"123,456" if elapsed[0] >= 0.1 and mode == "renders" else b""

    def check():
        checks.append(elapsed[0])
        if mode == "stopped" and elapsed[0] >= 0.04:
            raise CaptureUnavailable("Stopped")

    args = dict(clock=lambda: elapsed[0], sleep=sleep, timeout=0.2)
    if mode == "renders":
        assert wait_booth_price(read, 123456, check, **args) == 123456
    else:
        with pytest.raises(CaptureUnavailable if mode == "stopped" else ValueError):
            wait_booth_price(read, 123456, check, **args)
    assert elapsed[0] <= 0.22


from conquest.merchants.recovery import Recovery, credential_path, save_credentials


KEY = ItemKey("Coat", "Super", 2, ("No socket", "No socket"))


def listings(prices):
    return [Listing(f"seller{i}", KEY, p) for i, p in enumerate(prices)]


@pytest.mark.parametrize(
    "prices,target,excluded",
    [
        ([10, 100, 100, 100], 99, ("seller0",)),
        ([50, 100, 100, 100], 49, ()),
        ([49, 100, 100, 100], 99, ("seller0",)),
        ([101, 110, 120, 130], 99, ()),
        ([1, 1, 1, 1], None, ()),
        ([100, 100, 100], 99, ()),
        ([1_010_101_010] * 4, 999_999_999, ()),
        ([1_010_101_011] * 4, None, ()),
    ],
)
def test_pricing_outlier_boundary_and_rounding(prices, target, excluded):
    result = price_item(KEY, listings(prices))
    assert result.price == target and result.excluded == excluded


@pytest.mark.parametrize(
    "price", [0, -1, 1_000_000_000, 2_147_483_647, True, 99.5, "100"]
)
def test_invalid_booth_price_cannot_remove_existing_listing(price):
    from conquest.merchants.driver import MerchantDriver

    calls = []
    driver = SimpleNamespace(click=lambda *args: calls.append(args))
    with pytest.raises(ValueError, match="booth silver range"):
        MerchantDriver.list_item(
            driver, {}, dict(uid=1, price=100, slot=0), price, lambda: None
        )
    assert calls == []


def test_owned_sellers_match_but_do_not_count_as_independent_sellers():
    rows = listings([100, 110, 120, 130]) + [
        Listing("SPIRITUAL", KEY, 1),
        Listing("Dutch", KEY, 1),
        Listing("seller0", KEY, 200),
    ]
    assert price_item(KEY, rows).price == 1
    assert price_item(KEY, rows).sellers == 4
    assert price_item(KEY, rows[:3] + rows[4:]).price == 1


@pytest.mark.parametrize(
    "spiritual,dutch,target",
    [
        (99, 99, 99),
        (100, 100, 100),
        (90, 95, 90),
        (95, 90, 90),
        (101, 120, 99),
        (100, 120, 100),
    ],
)
def test_owned_floor_matches_and_only_lower_outside_prices_trigger_undercut(
    spiritual, dutch, target
):
    rows = listings([100, 110, 120, 130]) + [
        Listing("SpIrItUaL", KEY, spiritual),
        Listing("DUTCH", KEY, dutch),
    ]
    result = price_item(KEY, rows)
    assert result.price == target and result.sellers == 4
    # Simulate both merchants applying the quote, then repeated scans.
    for _ in range(3):
        rows = listings([100, 110, 120, 130]) + [
            Listing(c, KEY, target) for c in ("Spiritual", "Dutch")
        ]
        assert price_item(KEY, rows).price == target


def test_owned_floor_uses_valid_competitors_and_never_changes_outlier_median():
    result = price_item(
        KEY, listings([10, 100, 100, 100]) + [Listing("Dutch", KEY, 90)]
    )
    assert result.price == 90 and result.excluded == ("seller0",)


def test_owned_matching_respects_attributes_server_and_stack_rounding():
    rows = listings([100, 110, 120, 130])
    assert (
        price_item(
            KEY,
            rows
            + [
                Listing("Dutch", replace(KEY, plus=1), 1),
                Listing("Spiritual", KEY, 1, server="Europe"),
            ],
        ).price
        == 99
    )
    rows.append(Listing("Dutch", KEY, 101, quantity=2))
    assert price_item(KEY, rows, quantity=2).price == 101
    assert price_item(KEY, rows, quantity=3).price == 152
    assert price_item(KEY, rows, quantity=1).price == 51


def test_group_attributes_and_quantity_currency():
    rows = listings([200, 220, 240, 260])
    rows = [replace(r, quantity=2) for r in rows]
    assert price_item(KEY, rows, quantity=3).price == 297
    for wrong in (
        replace(KEY, plus=1),
        replace(KEY, quality="Elite"),
        replace(KEY, category="Boots"),
        replace(KEY, sockets=("Empty", "No socket")),
    ):
        assert price_item(wrong, rows).price is None
    assert price_item(KEY, [replace(r, server="Europe") for r in rows]).price is None
    with pytest.raises(ValueError):
        replace(KEY, currency="CP")
    with pytest.raises(ValueError):
        socket_name(177)


def item(uid=1, **extra):
    return dict(
        uid=uid,
        type_id=130009,
        name="TaijiRobe",
        plus=2,
        gem1=0,
        gem2=0,
        quantity=1,
        bound=False,
        slot=0,
        price=None,
        **extra,
    )


def snapshot():
    return {
        "character": "Spiritual",
        "identity": {"pid": 100, "creation_time_100ns": 1000},
        "timestamp": time.time(),
        "capacity": 40,
        "inventory": [item()],
        "silver": 1000,
        "booth": [],
        "booth_open": True,
        "request": None,
        "trade": {
            "participant": "Parasite",
            "participant_uid": 999,
            "own_items": [],
            "items": [item(2)],
            "own_silver": 0,
            "other_silver": 0,
            "accepted": False,
            "other_accepted": False,
        },
    }


@pytest.mark.parametrize("one_time", [False, True])
def test_last_booth_slot_goes_to_highest_total_value_and_queue_stays_ranked(
    tmp_path, monkeypatch, one_time
):
    from conquest.merchants.runtime import MerchantRuntime

    state = snapshot()
    state["trade"] = None
    state["inventory"] = [item(uid) for uid in (10, 20, 30, 40, 50)]
    state["inventory"][2]["quantity"] = 2
    state["booth"] = [
        {**item(uid), "bound": True, "price": 900} for uid in range(100, 131)
    ]
    prices = {10: 100, 20: 200, 30: 150, 40: 5000, 50: 200}
    market = SimpleNamespace(
        data={"observed_at": time.time()},
        key_for=lambda i: replace(KEY, category=str(i["uid"])),
        comparisons=lambda i: [
            Listing(
                "seller" + str(n),
                replace(KEY, category=str(i["uid"])),
                prices[i["uid"]],
            )
            for n in range(0 if i["uid"] == 40 else 4)
        ],
    )
    monkeypatch.setattr(
        "conquest.merchants.runtime.MarketSnapshot", lambda data: market
    )
    monkeypatch.setattr(
        "conquest.merchants.runtime.PriceHistory",
        lambda path: SimpleNamespace(
            remember=lambda market: None, catalog=lambda: {}, quotes=lambda: {}
        ),
    )
    path = tmp_path / "market.json"
    path.write_text("{}")
    journal = Journal(tmp_path / "journal.sqlite3")
    guard = InputCoordinator(lambda: True, path=tmp_path / "input.lock")
    runtime = MerchantRuntime(object(), guard, journal=journal, market_path=path)
    if one_time:
        runtime.list_once("Spiritual", "once:priority")
        journal.set(
            "Spiritual", "market_refresh", {"pending": False, "phase": "ready"}
        )  # Supplied market fixture is collected.
    else:
        runtime.enable("Spiritual", True)
    submitted = []

    def list_item(before, stock, price, check):
        check()
        submitted.append(stock["uid"])
        state["inventory"] = [i for i in state["inventory"] if i["uid"] != stock["uid"]]
        state["booth"].append({**stock, "price": price})

    driver = SimpleNamespace(
        read=lambda: copy.deepcopy(state),
        require_qualified=lambda capability: None,
        prepare_listing=lambda *args: None,
        list_item=list_item,
        wait_for=lambda predicate, check: (
            copy.deepcopy(state) if predicate(state) else None
        ),
    )
    runtime.controllers["Spiritual"] = MerchantController(
        "Spiritual", journal, driver, guard
    )
    runtime.observers["Spiritual"] = SimpleNamespace(
        adapter=SimpleNamespace(
            identity=state["identity"], assert_identity=lambda: None
        ),
        lock=threading.RLock(),
    )
    runtime.disconnected = lambda character: False
    runtime.step("Spiritual")
    assert submitted == [30]  # Quantity two: 297 total outranks a 198 single item.
    assert journal.get("Spiritual", "inventory_queue") == [20, 50, 10, 40]
    deferred = journal.get("Spiritual", "deferred")
    assert [p["uid"] for p in deferred if p["uid"] < 100] == [20, 50, 10, 40]
    assert journal.get("Spiritual", "stock_marker")["inventory"] == [10, 20, 40, 50]
    if one_time:
        assert not runtime.enabled("Spiritual")


@pytest.mark.parametrize("same_process,target", [(True, 90), (False, 99)])
def test_runtime_passes_only_current_process_owned_prices(
    tmp_path, monkeypatch, same_process, target
):
    from conquest.merchants.runtime import MerchantRuntime

    state = snapshot()
    state.update(trade=None, server="America")
    peer = {
        **state,
        "character": "Dutch",
        "identity": {"pid": 200, "creation_time_100ns": 2000},
        "inventory": [],
        "booth": [{**item(2), "price": 90}],
    }
    data = market_data()
    data["observed_at"] = time.time()
    path = tmp_path / "market.json"
    path.write_text(json.dumps(data))
    journal = Journal(tmp_path / "journal.sqlite3")
    guard = InputCoordinator(lambda: True, path=tmp_path / "input.lock")
    runtime = MerchantRuntime(object(), guard, journal=journal, market_path=path)
    runtime.list_once("Spiritual", "once:owned")
    submitted = []

    def list_item(before, stock, price, check):
        check()
        submitted.append(price)
        state["inventory"] = []
        state["booth"] = [{**stock, "price": price}]

    driver = SimpleNamespace(
        read=lambda: copy.deepcopy(state),
        require_qualified=lambda capability: None,
        prepare_listing=lambda *args: None,
        list_item=list_item,
        wait_for=lambda predicate, check: (
            copy.deepcopy(state) if predicate(state) else None
        ),
    )
    runtime.controllers["Spiritual"] = MerchantController(
        "Spiritual", journal, driver, guard
    )
    runtime.observers["Spiritual"] = SimpleNamespace(
        adapter=SimpleNamespace(
            identity=state["identity"], assert_identity=lambda: None
        ),
        lock=threading.RLock(),
    )
    runtime.observers["Dutch"] = SimpleNamespace(
        adapter=SimpleNamespace(
            identity={
                **peer["identity"],
                "creation_time_100ns": 2000 if same_process else 3000,
            }
        )
    )
    runtime.latest["Dutch"] = peer
    runtime.disconnected = lambda character: False
    with pytest.raises(CaptureUnavailable, match="Fetching fresh"):
        runtime.step("Spiritual")
    assert not submitted
    journal.set("Spiritual", "market_refresh", {"pending": False, "phase": "ready"})
    runtime.step("Spiritual")
    assert submitted == [target]
    assert runtime.latest["Spiritual"]["booth"][0]["price"] == target
    assert not runtime.enabled("Spiritual")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda s: s["trade"].update(participant="parasite"),
        lambda s: s["trade"].update(participant="Other"),
        lambda s: s["trade"].update(participant_uid=0),
        lambda s: s["trade"].update(own_items=[item(5)]),
        lambda s: s["trade"].update(own_silver=1),
        lambda s: s.update(capacity=1),
        lambda s: s["trade"].update(items=[]),
        lambda s: s["trade"]["items"][0].update(bound=True),
        lambda s: s["trade"]["items"][0].update(uid=1),
        lambda s: s["trade"]["items"][0].update(quantity=0),
    ],
)
def test_reject_untrusted_or_unreceivable_trade(mutate):
    state = snapshot()
    mutate(state)
    with pytest.raises(ValueError):
        validate_trade(state)


def test_receipt_requires_actual_items_silver_and_closed_trade():
    before = snapshot()
    after = copy.deepcopy(before)
    assert not received(before, after)
    after["trade"] = None
    assert not received(before, after)
    after["inventory"].append(item(2))
    assert received(before, after)
    after["inventory"][-1]["plus"] = 1
    assert not received(before, after)
    after["inventory"][-1]["plus"] = 2
    after["silver"] = 999
    assert not received(before, after)


@pytest.fixture
def journal(tmp_path):
    return Journal(tmp_path / "merchant.sqlite3")


def test_journal_intent_isolation_idempotence_and_restart(journal):
    state = snapshot()
    assert journal.begin("one", "Spiritual", "delivery", state)
    changed = copy.deepcopy(state)
    changed["silver"] += 1
    with pytest.raises(ValueError, match="reused"):
        journal.begin("one", "Spiritual", "delivery", changed)


def test_durable_intent_has_one_receipt_and_blocks_other_transactions(journal):
    state = snapshot()
    assert journal.begin("one", "Spiritual", "delivery", state)
    assert not journal.begin("one", "Spiritual", "delivery", state)
    with pytest.raises(ValueError):
        journal.begin("two", "Spiritual", "delivery", state)
    journal.begin("two", "Dutch", "delivery", state)
    journal.transition("one", "uncertain")
    restarted = Journal(journal.path)
    assert restarted.pending("Spiritual")[0]["phase"] == "uncertain"
    restarted.transition("one", "verified", {"items": [item(2)]})
    restarted.transition("one", "verified", {})
    assert len(restarted.events()) == 1
    assert len(restarted.pending("Dutch")) == 1
    assert restarted.pending("Spiritual") == []


def test_transaction_phase_cas_rejects_illegal_and_stale_transitions(journal):
    state = snapshot()
    journal.begin("one", "Spiritual", "delivery", state)
    with pytest.raises(ValueError, match="Illegal"):
        journal.transition("one", "prepared", {})
    journal.transition("one", "submitted", expected="prepared")
    with pytest.raises(ValueError, match="phase changed"):
        journal.transition("one", "uncertain", expected="prepared")
    journal.transition("one", "verified", {"receipt": "kept"}, expected="submitted")
    journal.transition("one", "verified", {"receipt": "discarded"})
    with journal.db() as db:
        assert json.loads(
            db.execute(
                "SELECT result_json FROM transactions WHERE id=?", ("one",)
            ).fetchone()[0]
        ) == {"receipt": "kept"}
    assert [
        row["status"] for row in journal.trace("one") if row["stage"] == "transaction"
    ] == ["prepared", "submitted", "verified"]


def test_scan_duplicate_coalescing_and_pause_persist(journal):
    journal.set("Dutch", "enabled", False)
    first = journal.request_scan("Dutch", "a", 1)
    assert journal.request_scan("Dutch", "a", 2) == first
    assert journal.request_scan("Dutch", "b", 2) == first
    assert Journal(journal.path).get("Dutch", "enabled") is False
    assert journal.get("Spiritual", "scan") is None
    assert journal.complete_scan("Dutch", "a", changed=0, deferred=1, now=10)
    assert journal.request_scan("Dutch", "b", 11)["pending"] is False
    journal.request_scan("Dutch", "c", 12)
    assert journal.request_scan("Dutch", "a", 13)["pending"] is False
    assert journal.get("Dutch", "scan")["request_id"] == "c"


class Driver:
    def __init__(self, states):
        self.states, self.inputs = states, []

    def require_qualified(self, capability):
        pass

    def prepare_listing(self, *args):
        pass

    def read(self):
        return copy.deepcopy(
            self.states.pop(0) if len(self.states) > 1 else self.states[0]
        )

    def accept_trade(self, state):
        self.inputs.append("accept")

    def wait_for(self, predicate, check):
        check()
        state = self.read()
        if not predicate(state):
            raise ValueError("Disconnected before receipt")
        return state


def controller(journal, tmp_path, states, authorized=True):
    state = states[0]
    journal.set("Spiritual", "enabled", True)
    if authorized:
        journal.set(
            "Spiritual",
            "accepted_request",
            {
                "identity": state["identity"],
                "participant_uid": 999,
                "opened_at": time.time(),
            },
        )
    driver = Driver(states)
    coordinator = InputCoordinator(lambda: True, path=tmp_path / "input.lock")
    return MerchantController("Spiritual", journal, driver, coordinator), driver


def test_trade_confirm_and_restart_reconcile_never_duplicate(journal, tmp_path):
    before = snapshot()
    after = copy.deepcopy(before)
    after.update(trade=None, inventory=[item(), item(2)])
    control, driver = controller(journal, tmp_path, [before, before, after])
    control.accept_delivery()
    assert driver.inputs == ["accept"]
    assert journal.pending("Spiritual") == []
    assert journal.events()[0]["event"] == "delivery_verified"
    assert journal.get("Spiritual", "new_stock")


def test_changing_offer_and_pid_reuse_never_confirm(journal, tmp_path):
    for changed in ("items", "identity"):
        before = snapshot()
        after = copy.deepcopy(before)
        if changed == "items":
            after["trade"]["items"] = [item(3)]
        else:
            after["identity"]["creation_time_100ns"] += 1
        control, driver = controller(journal, tmp_path, [before, after])
        with pytest.raises(ValueError, match="changed"):
            control.accept_delivery()
        assert driver.inputs == [] and journal.pending("Spiritual") == []


def test_outgoing_trade_is_not_authorized(journal, tmp_path):
    control, driver = controller(journal, tmp_path, [snapshot()], authorized=False)
    with pytest.raises(ValueError, match="incoming request"):
        control.accept_delivery()
    assert not driver.inputs


def test_disconnect_leaves_uncertain_intent_then_receipt_reconciles(journal, tmp_path):
    before = snapshot()
    control, driver = controller(journal, tmp_path, [before])
    with pytest.raises(ValueError):
        control.accept_delivery()
    assert journal.pending("Spiritual")[0]["phase"] == "uncertain"
    with pytest.raises(ValueError, match="reconciliation"):
        control.reconcile(before)
    after = copy.deepcopy(before)
    after.update(trade=None, inventory=[item(), item(2)])
    control.reconcile(after)
    assert not journal.pending("Spiritual") and driver.inputs == ["accept"]


@pytest.mark.parametrize(
    "blocker", ["none", "confirmation", "dialog", "trade", "identity", "attributes"]
)
def test_only_proven_unsubmitted_listings_can_automatically_retry(
    journal, tmp_path, blocker
):
    from conquest.merchants.controller import MerchantController

    before = snapshot()
    before.update(trade=None, booth_open=True, windows=[])
    record = {
        "uid": 1,
        "price": 100,
        "item": before["inventory"][0],
        "snapshot": before,
    }
    journal.begin("interrupted", "Spiritual", "listing", record)
    journal.transition(
        "interrupted",
        "uncertain",
        {"confirmation_attempted": blocker == "confirmation"},
    )
    after = copy.deepcopy(before)
    if blocker == "dialog":
        after["windows"] = [{"name": "Add Item to Booth"}]
    if blocker == "trade":
        after["request"] = {"participant": "Parasite"}
    if blocker == "identity":
        after["identity"]["creation_time_100ns"] += 1
    if blocker == "attributes":
        after["inventory"][0]["plus"] += 1
    c = MerchantController("Spiritual", journal, None, InputCoordinator())
    if blocker == "none":
        c.reconcile(after)
        assert not journal.pending("Spiritual")
    else:
        with pytest.raises(ValueError, match="reconciliation"):
            c.reconcile(after)


def test_driver_marks_only_failures_before_confirmation_as_unsubmitted():
    from conquest.merchants.driver import MerchantDriver
    from conquest.merchants.controller import ListingNotSubmitted

    for attempted in (False, True):

        def fail(*args, submission):
            submission["attempted"] = attempted
            raise CaptureUnavailable("Lost focus")

        driver = SimpleNamespace(_list_item=fail)
        with pytest.raises(CaptureUnavailable) as caught:
            MerchantDriver.list_item(driver, {}, None, 100, lambda: None)
        assert isinstance(caught.value, ListingNotSubmitted) == (not attempted)


def test_exclusive_input_revocation_manual_stop_and_nested_lease(tmp_path):
    safe = [True]
    manual = [False]
    guard = InputCoordinator(
        lambda: safe[0], lambda: manual[0], tmp_path / "input.lock"
    )
    errors = []
    with guard.lease("Spiritual"):
        with pytest.raises(CaptureUnavailable):
            with guard.lease("Dutch"):
                pass
        assert guard.owner == "Spiritual"

        def other():
            try:
                with guard.lease("Dutch"):
                    errors.append("bad")
            except CaptureUnavailable:
                errors.append("blocked")

        worker = threading.Thread(target=other)
        worker.start()
        worker.join()
        assert errors == ["blocked"]
        safe[0] = False
        with pytest.raises(CaptureUnavailable):
            guard.check()
    assert guard.owner is None
    safe[0] = True
    manual[0] = True
    with pytest.raises(CaptureUnavailable):
        with guard.lease("Dutch"):
            pass
    manual[0] = False
    guard.stop()
    with pytest.raises(CaptureUnavailable):
        guard.check()
    guard.resume()
    guard.check()


def test_recovery_limit_survives_restart_does_not_resume_paused_merchant(journal):
    now = [1000]
    recovery = Recovery("Dutch", journal, clock=lambda: now[0])
    journal.set("Dutch", "enabled", False)
    calls = []
    for _ in range(3):
        assert recovery.attempt(lambda: calls.append(1))
        now[0] += 100
    with pytest.raises(ValueError, match="exhausted"):
        Recovery("Dutch", journal, clock=lambda: now[0]).attempt(
            lambda: calls.append(1)
        )
    assert len(calls) == 3
    recovery.retry()
    assert journal.get("Dutch", "enabled") is False
    assert Recovery("Spiritual", journal).state()["attempts"] == 0


def market_data():
    return {
        "source": "https://conqueronline.net/market",
        "server": "America",
        "complete": True,
        "observed_at": 1000,
        "total": 4,
        "listings": [
            dict(
                name=name,
                category="Coat",
                quality="Super",
                plus=2,
                sockets=["No socket", "No socket"],
                seller=f"Seller{i}",
                price=100 + i * 10,
                server="America",
            )
            for i, name in enumerate(
                ["TaijiRobe", "TaoRobe", "AnotherRobe", "OtherRobe"]
            )
        ],
    }


@pytest.mark.parametrize(
    "mode,target",
    [
        ("fresh", 90),
        ("sold", 99),
        ("stale", 80),
        ("future", 80),
        ("wrong_server", 80),
        ("closed", 80),
        ("incomparable", 99),
        ("own_snapshot", 85),
    ],
)
def test_plan_prefers_fresh_owned_booths_over_delayed_website(mode, target):
    data = market_data()
    data["listings"].append({**data["listings"][0], "seller": "Dutch", "price": 80})
    data["total"] = 5
    market = MarketSnapshot(data, now=1000)
    state = snapshot()
    state.update(server="America", timestamp=1000, trade=None)
    peer = {
        **state,
        "character": "Dutch",
        "identity": {"pid": 200, "creation_time_100ns": 2000},
        "inventory": [],
        "booth": [{**item(2), "price": 90}],
    }
    if mode == "sold":
        peer["booth"] = []
    if mode == "stale":
        peer["timestamp"] = 994
    if mode == "future":
        peer["timestamp"] = 1001
    if mode == "wrong_server":
        peer["server"] = "Europe"
    if mode == "closed":
        peer["booth_open"] = False
    if mode == "incomparable":
        peer["booth"][0]["plus"] = 1
    if mode == "own_snapshot":
        state["booth"] = [{**item(3), "price": 85}]
    control = MerchantController("Spiritual", None, None, None, clock=lambda: 1000)
    plans = control.plan(state, market, owned_snapshots=[peer])
    assert all(p["price"] == target for p in plans)


def test_owned_match_is_allowed_in_sparse_market():
    state = snapshot()
    state.update(
        server="America", timestamp=1000, trade=None, booth=[{**item(2), "price": 90}]
    )
    data = market_data()
    data["listings"].pop()
    data["total"] = 3
    market = MarketSnapshot(data, now=1000)
    control = MerchantController("Spiritual", None, None, None, clock=lambda: 1000)
    assert all(p["price"] == 90 for p in control.plan(state, market))


def test_equipment_compares_across_names_and_levels_and_snapshot_expires():
    market = MarketSnapshot(market_data(), now=1000)
    assert price_item(market.key_for(item()), market.comparisons(item())).price == 99
    for mutate in (
        lambda d: d.update(complete=False),
        lambda d: d.update(server="Europe"),
        lambda d: d.update(total=5),
        lambda d: d.update(observed_at=0),
    ):
        data = market_data()
        mutate(data)
        with pytest.raises(ValueError):
            MarketSnapshot(data, now=1000)
    unknown = item()
    unknown["name"] = "Unknown"
    with pytest.raises(ValueError):
        market.key_for(unknown)


def test_notification_cursor_persisted_and_farmer_policy_unchanged(journal):
    from conquest.discord_notify import Notifications

    notifier = Notifications()
    notifier.merchants(journal.path, 999)
    journal.event("Spiritual", "scan_completed", changed=2, deferred=3)
    journal.event("Dutch", "persistent_failure", note="Reconnect retries exhausted")
    notifier.merchants(journal.path, 1000)
    assert len(notifier.state["queue"]) == 2
    assert "Spiritual" in notifier.state["queue"][0]["content"]
    assert "Dutch" in notifier.state["queue"][1]["content"]
    restarted = Notifications(json.loads(json.dumps(notifier.state)))
    restarted.merchants(journal.path, 1001)
    assert len(restarted.state["queue"]) == 2
    restarted.enqueue("Farmer remains unchanged", 1001, "test")
    assert "Parasite" in restarted.state["queue"][-1]["content"]


def test_notification_first_adoption_skips_history_and_deduplicates_active_failure(
    journal,
):
    from conquest.discord_notify import Notifications

    journal.event("Spiritual", "scan_completed", changed=9, deferred=4)
    journal.event("Dutch", "persistent_failure", note="Reconnect retries exhausted")
    notifier = Notifications()
    notifier.merchants(journal.path, 1000)
    cursor = notifier.state["merchant_cursor"]
    assert cursor == 2 and notifier.state["queue"] == []
    assert notifier.state["merchant_failures"] == {
        "Dutch": "Reconnect retries exhausted"
    }

    restarted = Notifications(json.loads(json.dumps(notifier.state)))
    restarted.merchants(journal.path, 1001)
    assert (
        restarted.state["merchant_cursor"] == cursor and restarted.state["queue"] == []
    )
    journal.event("Dutch", "persistent_failure", note="Reconnect retries exhausted")
    journal.event("Spiritual", "scan_completed", changed=1, deferred=0)
    restarted.merchants(journal.path, 1002)
    assert restarted.state["merchant_cursor"] == 4
    assert len(restarted.state["queue"]) == 1
    assert restarted.state["queue"][0]["kind"] == "merchant_scan_completed"

    again = Notifications(json.loads(json.dumps(restarted.state)))
    again.merchants(journal.path, 1003)
    assert len(again.state["queue"]) == 1 and again.state["merchant_cursor"] == 4
    journal.event("Dutch", "recovery_verified", attempts=2)
    journal.event("Dutch", "persistent_failure", note="Reconnect retries exhausted")
    again.merchants(journal.path, 1004)
    assert [row["kind"] for row in again.state["queue"][-2:]] == [
        "merchant_recovery_verified",
        "merchant_persistent_failure",
    ]


def test_separate_encrypted_credentials(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    save_credentials("Spiritual", "example-account", "example-password")
    path = credential_path("Spiritual")
    assert b"example-password" not in path.read_bytes()
    assert not credential_path("Dutch").exists()
    from conquest.reconnect import load_credentials

    assert load_credentials(path)["username"] == "example-account"
    with pytest.raises(ValueError):
        credential_path("../escape")


@pytest.mark.parametrize("old_price", [10, 1000])
def test_repricing_increases_and_decreases_require_matching_receipt(
    journal, tmp_path, old_price
):
    from conquest.merchants.controller import identities

    before = snapshot()
    before.update(trade=None, inventory=[], booth=[item()])
    before["booth"][0]["price"] = old_price
    after = copy.deepcopy(before)
    after["booth"][0]["price"] = 99
    control, driver = controller(journal, tmp_path, [before, after])
    driver.list_item = lambda *args: driver.inputs.append("list")
    plan = {
        "uid": 1,
        "price": 99,
        "old_price": old_price,
        "observed_at": time.time(),
        "attributes": list(identities(before["booth"])[1]),
    }
    control.apply_price(plan)
    assert driver.inputs == ["list"] and not journal.pending("Spiritual")
    assert journal.events()[0]["event"] == "listing_verified"


def test_expired_repricing_never_sends_input(journal, tmp_path):
    before = snapshot()
    before.update(trade=None)
    control, driver = controller(journal, tmp_path, [before])
    with pytest.raises(ValueError, match="expired"):
        control.apply_price(
            {
                "uid": 1,
                "price": 99,
                "old_price": None,
                "observed_at": 0,
                "attributes": [],
            }
        )
    assert not journal.pending("Spiritual") and not driver.inputs


def test_preflight_focus_denial_has_no_transaction_to_reconcile(journal, tmp_path):
    from conquest.merchants.controller import identities

    before = snapshot()
    before.update(trade=None)
    control, driver = controller(journal, tmp_path, [before])

    def denied(*args):
        raise CaptureUnavailable("Waiting for foreground focus")

    driver.prepare_listing = denied
    plan = {
        "uid": 1,
        "price": 99,
        "old_price": None,
        "observed_at": time.time(),
        "attributes": list(identities(before["inventory"])[1]),
    }
    with pytest.raises(CaptureUnavailable, match="foreground"):
        control.apply_price(plan)
    assert not journal.pending("Spiritual") and not driver.inputs
    with journal.db() as db:
        assert db.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 0


def test_listing_failure_before_confirm_is_immediately_verified_aborted(
    journal, tmp_path
):
    from conquest.merchants.controller import identities, ListingNotSubmitted

    before = snapshot()
    before.update(trade=None, windows=[])
    after = copy.deepcopy(before)
    control, driver = controller(journal, tmp_path, [before, after])
    driver.list_item = lambda *args: (_ for _ in ()).throw(
        ListingNotSubmitted("Focus changed before confirmation")
    )
    plan = {
        "uid": 1,
        "price": 99,
        "old_price": None,
        "observed_at": time.time(),
        "attributes": list(identities(before["inventory"])[1]),
    }
    assert control.apply_price(plan) is None and not journal.pending("Spiritual")
    with journal.db() as db:
        row = db.execute(
            "SELECT phase,result_json FROM transactions WHERE kind='listing'"
        ).fetchone()
    result = json.loads(row["result_json"])
    assert row["phase"] == "aborted" and result["outcome"] == "not_submitted"
    assert result["confirmation_attempted"] is False


def test_missing_booth_calibration_is_verified_before_listing_intent(
    journal, tmp_path, monkeypatch
):
    from contextlib import nullcontext

    before = snapshot()
    before["trade"] = None
    after = copy.deepcopy(before)
    after.update(inventory=[], booth=[{**item(), "price": 99}])
    control, driver = controller(journal, tmp_path, [before, before, after])
    qualified = [False]
    calls = []

    def require(capability):
        if not qualified[0]:
            raise ValueError("Needs calibration")

    def verify(probe, j, check):
        check()
        assert not j.pending("Spiritual")
        calls.append("verified")
        qualified[0] = True

    driver.require_qualified = require
    driver.observer = SimpleNamespace(lock=threading.RLock())
    driver.list_item = lambda *args: calls.append("listed")
    monkeypatch.setattr(
        "conquest.merchants.qualification.verify_booth_controls", verify
    )
    monkeypatch.setattr("conquest.desktop_runtime.physical_coordinates", nullcontext)
    control.apply_price(
        {
            "uid": 1,
            "price": 99,
            "old_price": None,
            "observed_at": time.time(),
            "attributes": [130009, 2, 0, 0, 1],
        }
    )
    assert calls == ["verified", "listed"] and not journal.pending("Spiritual")


def test_browser_cells_validate_totals_and_unknown_stack_quantities():
    from conquest.merchants.market import browser_pages

    row = [
        "TaijiRobe\n\nTaoist Robe",
        "Super",
        "+2",
        "No socket\nNo socket",
        "Trader",
        "America",
        "100,000",
    ]
    data = dict(
        source="https://conqueronline.net/market",
        server="America",
        last_page=True,
        observed_at=time.time(),
        initial_total=1,
        final_total=1,
        initial_change="same",
        final_change="same",
        pages=[{"page": 1, "rows": [row]}],
    )
    result = browser_pages(data, [{"id": 130009, "name": "TaijiRobe"}])
    assert result["listings"][0]["quantity"] == 1
    result = browser_pages(data, [])
    assert result["listings"][0]["quantity"] is None
    assert not MarketSnapshot(result).listings
    data["final_total"] = 2
    with pytest.raises(ValueError, match="changed"):
        browser_pages(data, [])


def test_recurring_rollout_rejects_missing_or_invented_receipts(journal, tmp_path):
    from conquest.merchants.rollout import verify_rollout

    with pytest.raises(ValueError, match="incomplete"):
        verify_rollout(tmp_path / "absent.json", journal, tmp_path)


def test_equipment_type_mapping_does_not_require_exact_name_or_quality():
    data = market_data()
    data["equipment_categories"] = {"130": "Coat"}
    market = MarketSnapshot(data, now=1000)
    unseen = item()
    unseen["name"] = "UnlistedLowerLevelCoat"
    assert price_item(market.key_for(unseen), market.comparisons(unseen)).price == 99
    changed = item()
    changed["type_id"] = 130008
    assert market.key_for(changed).quality == "Elite"
    data["ambiguous_equipment_types"] = ["130"]
    with pytest.raises(ValueError, match="conflicting"):
        MarketSnapshot(data, now=1000).key_for(unseen)


def test_one_time_completion_atomically_pauses_and_duplicate_never_resumes(journal):
    state, created = journal.request_once("Dutch", "once:a", now=10)
    assert created and state["pending"] and state["one_time"]
    assert journal.get("Dutch", "enabled") is True
    assert journal.request_once("Dutch", "once:a")[1] is False
    assert journal.complete_scan("Dutch", "once:a", changed=10, deferred=15, now=20)
    restarted = Journal(journal.path)
    assert restarted.get("Dutch", "enabled") is False
    assert restarted.get("Dutch", "scan")["next_scan"] is None
    state, created = restarted.request_once("Dutch", "once:a")
    assert not created and not state["pending"]
    assert restarted.get("Dutch", "enabled") is False
    assert restarted.get("Spiritual", "enabled") is None


def test_one_time_request_respects_unfinished_work_and_manual_pause(journal):
    journal.request_scan("Dutch", "ongoing")
    with pytest.raises(ValueError, match="existing scan"):
        journal.request_once("Dutch", "once:new")
    journal.complete_scan("Dutch", "ongoing", changed=0, deferred=0)
    journal.request_once("Dutch", "once:new")
    journal.set("Dutch", "enabled", False)
    journal.request_once("Dutch", "once:new")
    assert journal.get("Dutch", "enabled") is False
