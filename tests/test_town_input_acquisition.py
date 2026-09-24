"""A contended input mutex may delay a new action, never replay a submitted one."""

from contextlib import contextmanager
from dataclasses import asdict, replace
import threading
from types import SimpleNamespace

import pytest

from conquest.capture import CaptureUnavailable
from conquest.memory_inventory import InventorySnapshot, Item
from conquest.memory_shop import GuiWindow, ShopProduct, ShopSnapshot
from conquest.merchants import coordination
from conquest.merchants.coordination import InputAcquisitionBusy, InputCoordinator
from conquest.overnight import OvernightLoop
from conquest.town_trade import TownObservationUnavailable, TownTrade


@contextmanager
def held_by_peer(coordinator):
    ready, release = threading.Event(), threading.Event()

    def hold():
        with coordinator.lock:
            ready.set()
            assert release.wait(5), "Test did not release input mutex"

    peer = threading.Thread(target=hold)
    peer.start()
    assert ready.wait(5)
    try:
        yield release
    finally:
        release.set()
        peer.join(5)
        assert not peer.is_alive()


@pytest.fixture
def coordinator(tmp_path, monkeypatch):
    owner = InputCoordinator(path=tmp_path / "input.lock")
    monkeypatch.setattr(coordination, "_coordinator", owner)
    return owner


def test_typed_acquisition_failure_precedes_foreground_body_file_and_owner(coordinator):
    from conquest.foreground import foreground_click

    target = SimpleNamespace(snapshot=lambda: pytest.fail("Foreground body entered"))
    with held_by_peer(coordinator):
        with pytest.raises(InputAcquisitionBusy, match="current input action"):
            foreground_click(target, 1, 1, (800, 600))
        assert coordinator.owner is None and coordinator.thread is None
        assert coordinator.purpose is None
        assert not getattr(coordination._scope, "active", False)
        assert not coordinator.path.exists()


@pytest.mark.parametrize("guard", ["stop", "mouse", "manual_session", "fence"])
def test_control_denials_are_not_retryable_acquisition_failures(coordinator, guard):
    if guard == "stop":
        coordinator.stop()
    elif guard == "mouse":
        coordinator.manual_active = lambda: True
    elif guard == "manual_session":
        coordinator.manual_sessions["Farmer"] = {"holds_automation": True}
    else:

        @contextmanager
        def stopped():
            raise CaptureUnavailable("Control revision expired")
            yield

        coordinator.fence = SimpleNamespace(input_action=stopped)
    with held_by_peer(coordinator):
        with pytest.raises(CaptureUnavailable) as caught:
            with coordination.input_scope():
                pytest.fail("Control-denied input entered")
    assert not isinstance(caught.value, InputAcquisitionBusy)
    assert not coordinator.path.exists()


def test_os_lease_failure_is_not_the_typed_local_mutex_denial(coordinator, monkeypatch):
    import msvcrt

    def unavailable(*args):
        raise OSError("Other process holds lease")

    monkeypatch.setattr(msvcrt, "locking", unavailable)
    with pytest.raises(CaptureUnavailable, match="Another character owns") as caught:
        with coordination.input_scope():
            pytest.fail("OS lease-denied input entered")
    assert not isinstance(caught.value, InputAcquisitionBusy)
    assert coordinator.owner is None and coordinator.thread is None


@pytest.fixture
def town(coordinator, monkeypatch):
    from conquest import overnight, savings, town_trade, viewport

    product = ShopProduct(0, 900, 1000020, "Painkiller", 100, 100)
    shop = ShopSnapshot(
        123,
        (product,),
        GuiWindow(1, "Shop", (0.0, 0.0), (288.0, 268.0), (0.0, 0.0)),
        GuiWindow(2, "Shop/Items", (20.0, 38.0), (248.0, 200.0), (0.0, 0.0)),
    )
    state = SimpleNamespace(
        inventory=InventorySnapshot(1.0, 1.0, (), None, 1000, 40),
        shop=shop,
        vendor=SimpleNamespace(entity_id=123),
        invalid=None,
        clicks=[],
        reads=[],
        requests=[],
        events=[],
        living=0,
        after_wait=lambda: None,
        click_error=None,
    )
    trade = object.__new__(TownTrade)
    trade.observer = SimpleNamespace(operations=SimpleNamespace(target=object()))

    def life(*args, **kwargs):
        state.reads.append("life")
        if state.invalid == "life":
            raise ValueError("Town action requires a living character on the town map")

    def vendor(kind):
        life()
        state.reads.append("vendor")
        if state.invalid == "vendor":
            raise ValueError("Vendor is not in the current scene")
        return state.vendor

    def read_shop(uid):
        state.reads.append("shop")
        if state.invalid == "shop":
            raise ValueError("Open shop belongs to a different vendor ID")
        return state.shop

    def inventory():
        state.reads.append(
            ("inventory", state.inventory.silver, len(state.inventory.items))
        )
        return state.inventory

    @coordination.coordinated_input
    def click(*args, **kwargs):
        state.clicks.append((state.inventory.silver, len(state.inventory.items)))
        if state.click_error:
            raise state.click_error
        item = Item(
            100 + len(state.inventory.items), 1000020, 1, 1, len(state.inventory.items)
        )
        state.inventory = replace(
            state.inventory,
            items=(*state.inventory.items, item),
            silver=state.inventory.silver - 100,
        )

    trade.life, trade.vendor = life, vendor
    trade.shop = SimpleNamespace(read=read_shop)
    trade.inventory = SimpleNamespace(read=inventory)
    monkeypatch.setattr(town_trade, "foreground_click", click)
    monkeypatch.setattr(viewport, "size_for", lambda _: (800, 600))
    monkeypatch.setattr(savings, "savings_plan", lambda: None)
    loop = object.__new__(OvernightLoop)
    from conquest.routes import RouteLibrary

    loop.route = RouteLibrary().load("turtledove")
    loop.info = "unused"
    loop.record = lambda event, **fields: state.events.append((event, fields))

    def living():
        state.living += 1

    def request(info, operation, body):
        assert operation == "town"
        state.requests.append(dict(body))
        if body["action"] == "supplies":
            return asdict(state.inventory)
        return trade({k: v for k, v in body.items() if k != "expires_at"})

    loop.living = living
    monkeypatch.setattr(overnight, "request", request)
    monkeypatch.setattr(overnight.time, "sleep", lambda _: state.after_wait())
    state.trade, state.loop = trade, loop
    return state


def release_peer(release, coordinator):
    release.set()
    # Wait for the peer to relinquish the real mutex, without timing guesses.
    with coordinator.lock:
        pass


def test_sequential_verified_purchases_wait_then_buy_only_remaining_count(
    town, coordinator
):
    assert town.loop.buy_supply(3, 1000020)
    first = next(
        fields["receipt"] for event, fields in town.events if event == "purchase"
    )
    assert first == {"bought": 1000020, "amount": 1, "price": 100, "silver": 900}
    with held_by_peer(coordinator) as release:

        def after_wait():
            assert town.clicks == [(1000, 0)]
            release_peer(release, coordinator)

        town.after_wait = after_wait
        assert town.loop.buy_supply(3, 1000020)
    while town.inventory.count(1000020) < 5:
        assert town.loop.buy_supply(3, 1000020)
    assert town.inventory.count(1000020) == 5 and town.inventory.silver == 500
    assert town.clicks == [(1000, 0), (900, 1), (800, 2), (700, 3), (600, 4)]
    buys = [body for body in town.requests if body["action"] == "buy"]
    assert len(buys) == 6
    assert len([event for event, _ in town.events if event == "purchase"]) == 5
    assert (
        len([event for event, _ in town.events if event == "town_observation_retry"])
        == 1
    )
    assert town.living == len(town.requests)
    assert town.reads.count(("inventory", 900, 1)) >= 3  # Receipt + both attempts.
    assert coordinator.owner is None


@pytest.mark.parametrize(
    "mutation,expected",
    [
        ("vendor", "Vendor is not"),
        ("shop", "different vendor"),
        ("product", "not sold"),
        ("inventory", "inventory room"),
        ("silver", "Insufficient funds"),
        ("life", "living character"),
        ("stop", "Automation stopped"),
        ("mouse", "manual input active"),
    ],
)
def test_retry_reobserves_current_safety_authority_before_any_click(
    town, coordinator, mutation, expected
):
    with held_by_peer(coordinator) as release:

        def after_wait():
            assert town.clicks == []
            assert ("inventory", 1000, 0) in town.reads
            town.reads.clear()
            if mutation in ("vendor", "shop", "life"):
                town.invalid = mutation
            elif mutation == "product":
                town.shop = replace(town.shop, products=())
            elif mutation == "inventory":
                town.inventory = replace(town.inventory, capacity=0)
            elif mutation == "silver":
                town.inventory = replace(town.inventory, silver=0)
            elif mutation == "stop":
                coordinator.stop()
            else:
                coordinator.manual_active = lambda: True
            release_peer(release, coordinator)

        town.after_wait = after_wait
        with pytest.raises(ValueError, match=expected) as caught:
            town.loop.town("buy", vendor_type=3, type_id=1000020)
    assert not isinstance(caught.value, TownObservationUnavailable)
    assert town.clicks == []
    assert len(town.requests) == 2 and town.living == 2
    assert town.reads[0] == "life"


@pytest.mark.parametrize(
    "message",
    [
        "Waiting for the current input action",
        "Focus changed during click",
        "SendInput(mouse-down) failed",
        "Purchase was not verified; no repeat purchase issued",
    ],
)
def test_generic_or_postsubmission_failure_never_retries(town, message):
    # Deliberately raised from the input body, including identical message text:
    # only the typed, pre-entry mutex denial is newly retryable.
    town.click_error = CaptureUnavailable(message)
    with pytest.raises(CaptureUnavailable) as caught:
        town.loop.town("buy", vendor_type=3, type_id=1000020)
    assert caught.value is town.click_error
    assert not isinstance(caught.value, TownObservationUnavailable)
    assert len(town.requests) == 1 and town.clicks == [(1000, 0)]
    assert not any(event == "town_observation_retry" for event, _ in town.events)


def test_purchase_receipt_timeout_after_click_never_repeats(town):
    original = town.trade.verified_read
    town.trade.verified_read = lambda read, accept, failure: original(
        read, accept, failure, timeout=0
    )
    with pytest.raises(ValueError, match="Purchase was not verified") as caught:
        town.loop.town("buy", vendor_type=3, type_id=1000020)
    assert not isinstance(caught.value, TownObservationUnavailable)
    assert len(town.requests) == 1 and town.clicks == [(1000, 0)]
    assert town.inventory.count(1000020) == 1 and town.inventory.silver == 900
    assert not any(event == "town_observation_retry" for event, _ in town.events)
