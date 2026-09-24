"""A complete monetary input sequence owns one lease and is never replayed."""

from contextlib import contextmanager
from dataclasses import replace
import threading
from types import SimpleNamespace as NS

import pytest

from conquest import overnight, town_trade, warehouse_money
from conquest.capture import CaptureUnavailable
from conquest.memory_inventory import InventorySnapshot
from conquest.merchants import coordination
from conquest.merchants.coordination import InputAcquisitionBusy, InputCoordinator
from conquest.overnight import OvernightLoop
from conquest.town_trade import TownObservationUnavailable, TownTrade
from test_town_input_acquisition import held_by_peer, release_peer


@pytest.fixture
def bank(tmp_path, monkeypatch):
    owner = InputCoordinator(path=tmp_path / "input.lock")
    monkeypatch.setattr(coordination, "_coordinator", owner)
    state = NS(
        owner=owner,
        inventory=InventorySnapshot(1.0, 1.0, (), None, 1000, 40),
        stored=500,
        amount="",
        clicks=[],
        typed=[],
        reads=[],
        requests=[],
        events=[],
        hook=lambda boundary: None,
        after_wait=lambda: None,
        credit=True,
    )
    points = {"amount": (10, 20), "deposit": (30, 20), "withdraw": (50, 20)}
    trade = object.__new__(TownTrade)
    trade.observer = NS(adapter=None, operations=NS(target=None))

    def read(name, value):
        state.reads.append((name, owner.owner))
        state.hook(name)
        return value

    trade.life = lambda **kwargs: read("life", None)
    trade.vendor = lambda kind: read("vendor", 123)
    trade.inventory = NS(read=lambda: read("inventory", state.inventory))
    monkeypatch.setattr(
        warehouse_money,
        "WarehouseMoneyReader",
        NS(
            for_session=lambda _: NS(
                read=lambda: read("bank", NS(silver=state.stored, amount=state.amount))
            )
        ),
    )
    monkeypatch.setattr(
        warehouse_money, "money_points", lambda *args: read("geometry", points)
    )
    from conquest import viewport

    monkeypatch.setattr(viewport, "size_for", lambda _: (800, 600))

    @coordination.coordinated_input
    def click(target, x, y, *args, **kwargs):
        boundary = next(key for key, value in points.items() if value == (x, y))
        state.clicks.append(boundary)
        state.hook(boundary)
        if boundary != "amount" and state.credit:
            delta = 100 if boundary == "deposit" else -100
            state.inventory = replace(
                state.inventory, silver=state.inventory.silver - delta
            )
            state.stored += delta
        state.hook(boundary + "_after")

    @coordination.coordinated_input
    def type_amount(target, amount):
        state.typed.append(amount)
        state.hook("typing")
        state.amount = str(amount)
        state.hook("typing_after")

    def verified(read, accept, failure, **kwargs):
        result = read()
        if not accept(result):
            raise ValueError(failure)
        return result

    monkeypatch.setattr(town_trade, "foreground_click", click)
    monkeypatch.setattr(warehouse_money, "type_amount", type_amount)
    trade.verified_read = verified
    loop = object.__new__(OvernightLoop)
    loop.info = "unused"
    loop.living = lambda: None
    loop.record = lambda event, **fields: state.events.append((event, fields))

    def request(info, operation, body):
        assert operation == "town"
        state.requests.append(dict(body))
        return trade({key: value for key, value in body.items() if key != "expires_at"})

    monkeypatch.setattr(overnight, "request", request)
    monkeypatch.setattr(overnight.time, "sleep", lambda _: state.after_wait())
    state.trade, state.loop = trade, loop
    return state


def transfer(bank, direction="deposit"):
    return bank.loop.town("warehouse-money-" + direction, amount=100)


def assert_released(bank):
    assert bank.owner.owner is None and bank.owner.thread is None
    assert not getattr(coordination._scope, "active", False)


def test_contention_before_entry_retries_with_fresh_money_evidence(bank):
    with held_by_peer(bank.owner) as release:

        def after_wait():
            assert bank.clicks == bank.typed == bank.reads == []
            bank.inventory = replace(bank.inventory, silver=1200)
            bank.stored = 700
            release_peer(release, bank.owner)

        bank.after_wait = after_wait
        receipt = transfer(bank)
    assert receipt == {
        "direction": "deposit",
        "amount": 100,
        "silver": 1100,
        "stored_silver": 800,
        "verified": True,
    }
    assert len(bank.requests) == 2 and bank.clicks == ["amount", "deposit"]
    assert sum(event == "town_observation_retry" for event, _ in bank.events) == 1
    assert_released(bank)


@pytest.mark.parametrize(
    "direction,wallet,stored", [("deposit", 900, 600), ("withdraw", 1100, 400)]
)
def test_one_shared_lease_covers_prechecks_entry_submission_and_receipt(
    bank, direction, wallet, stored
):
    blocked = []

    def observe(boundary):
        assert bank.owner.owner == "Farmer"
        assert bank.owner.thread == threading.get_ident()
        assert bank.owner.purpose == "warehouse_money_transfer"
        assert coordination._scope.active

        def peer():
            # The mutex stays unavailable between the separately coordinated
            # click/key calls. A competing input body must never be entered.
            acquired = bank.owner.lock.acquire(blocking=False)
            if acquired:
                bank.owner.lock.release()
            blocked.append(not acquired)
            with pytest.raises(CaptureUnavailable):
                with coordination.input_scope():
                    pytest.fail("Peer entered during monetary transaction")

        thread = threading.Thread(target=peer)
        thread.start()
        thread.join(5)
        assert not thread.is_alive()

    bank.hook = observe
    receipt = transfer(bank, direction)
    assert (
        receipt["silver"] == wallet
        and receipt["stored_silver"] == stored
        and receipt["verified"]
    )
    assert bank.clicks == ["amount", direction] and bank.typed == [100]
    assert blocked and all(blocked)
    assert all(owner == "Farmer" for _, owner in bank.reads)
    assert_released(bank)


@pytest.mark.parametrize("boundary", ["amount_after", "typing_after"])
@pytest.mark.parametrize("denial", ["stop", "mouse", "manual_session", "fence"])
def test_manual_or_control_denial_between_steps_aborts_without_submission_or_retry(
    bank, boundary, denial
):
    def intervene(current):
        if current != boundary:
            return
        if denial == "stop":
            bank.owner.stop()
        elif denial == "mouse":
            bank.owner.manual_active = lambda: True
        elif denial == "manual_session":
            bank.owner.manual_sessions["Farmer"] = {"holds_automation": True}
        else:

            @contextmanager
            def invalidated():
                raise CaptureUnavailable("Control revision expired")
                yield

            bank.owner.fence = NS(input_action=invalidated)

    bank.hook = intervene
    with pytest.raises(CaptureUnavailable) as caught:
        transfer(bank)
    assert not isinstance(caught.value, TownObservationUnavailable)
    assert (
        bank.clicks == ["amount"]
        and bank.inventory.silver == 1000
        and bank.stored == 500
    )
    assert len(bank.requests) == 1
    assert_released(bank)


@pytest.mark.parametrize("boundary", ["amount_after", "typing_after", "deposit_after"])
@pytest.mark.parametrize(
    "error_type", [InputAcquisitionBusy, TownObservationUnavailable, CaptureUnavailable]
)
def test_inner_retry_shaped_errors_never_replay_the_whole_transfer(
    bank, boundary, error_type
):
    def fail(current):
        if current == boundary:
            raise error_type("Waiting for the current input action")

    bank.hook = fail
    with pytest.raises(CaptureUnavailable) as caught:
        transfer(bank)
    assert type(caught.value) is CaptureUnavailable
    assert len(bank.requests) == 1
    assert bank.clicks == (
        ["amount", "deposit"] if boundary == "deposit_after" else ["amount"]
    )
    assert bank.inventory.silver == (900 if boundary == "deposit_after" else 1000)
    assert not any(event == "town_observation_retry" for event, _ in bank.events)
    assert_released(bank)


def test_missing_bilateral_receipt_does_not_repeat_submission(bank):
    bank.credit = False
    with pytest.raises(ValueError, match="transfer unverified; no repeat input"):
        transfer(bank)
    assert bank.clicks == ["amount", "deposit"] and bank.typed == [100]
    assert len(bank.requests) == 1
    assert_released(bank)


def test_stop_after_submission_allows_only_read_only_receipt_reconciliation(bank):
    bank.hook = lambda boundary: (
        bank.owner.stop() if boundary == "deposit_after" else None
    )
    assert transfer(bank)["verified"] is True
    assert bank.owner.stopped and bank.clicks == ["amount", "deposit"]
    assert len(bank.requests) == 1
    assert_released(bank)
