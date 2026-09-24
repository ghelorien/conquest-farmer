from types import SimpleNamespace as NS
import pytest
from conquest import storage_overflow as o, banking, storage_halt as halt
from conquest.discord_notify import read_json, write_json


def item(uid, kind=113348):
    return dict(uid=uid, type_id=kind, amount=1, limit=1, slot=uid, plus=0)


@pytest.fixture
def scenario(monkeypatch):
    local = [item(i, 1088001) for i in range(9)] + [item(20)]
    bag = [item(30), item(31)]
    market = []
    events = []
    state = {"map": 1011, "wallet": 200, "capacity": 4}
    plan = {
        "outbound": {"verified": True, "destination_map": 1036, "fare": 100},
        "return": {"verified": True, "destination_map": 1011, "fare": 0},
    }
    write_json(
        o.POLICY,
        {
            "overflow_enabled": True,
            "origins": {"1011": plan},
            "market_warehouse": {"position": [182, 180]},
        },
    )

    def trip(loop, leg, *, before_submit=None):
        if before_submit:
            before_submit()
        state["map"] = leg["destination_map"]
        state["wallet"] -= leg["fare"]
        events.append(("trip", state["map"]))

    monkeypatch.setattr(o, "trip", trip)
    monkeypatch.setattr(
        "conquest.navigation.read_terrain", lambda *a: NS(map_id=state["map"])
    )
    monkeypatch.setattr(
        banking, "open_warehouse", lambda loop: events.append(("open", state["map"]))
    )
    monkeypatch.setattr(
        banking, "close_warehouse", lambda loop: events.append(("close", state["map"]))
    )

    def transfer(loop, direction, amount):
        state["wallet"] += amount
        events.append(("withdraw_silver", amount))

    monkeypatch.setattr(banking, "transfer", transfer)

    def town(action, **fields):
        if action == "service-close-panel":
            return {}
        if action == "vendor-status":
            return {"reachable": True}
        if action == "supplies":
            return {"items": list(bag), "silver": state["wallet"]}
        if action == "warehouse-money":
            return {"stored_silver": 1000}
        if action == "warehouse-items":
            return {"items": list(market), "capacity": state["capacity"]}
        if action == "warehouse-deposit":
            value = next(v for v in bag if v["uid"] == fields["uid"])
            bag.remove(value)
            market.append(value)
            events.append(("deposit", fields["uid"]))
            return {"stored": fields["uid"], "verified_in_warehouse": True}
        pytest.fail(action)

    loop = NS(
        town=town,
        living=lambda: {
            "embedded_controls": {
                "life": {"map_id": state["map"], "position": [190, 189]}
            }
        },
        record=lambda event, **kw: events.append((event, kw)),
        travel=lambda *a, **kw: None,
    )
    return loop, {"items": local, "capacity": 10}, bag, market, state, events


def test_only_carried_overflow_moves_and_transport_reserve_survives(scenario):
    loop, local, bag, market, state, events = scenario
    assert o.handle(loop, local)
    assert (
        len(local["items"]) == 10 and not bag and [v["uid"] for v in market] == [30, 31]
    )
    assert state["map"] == 1011 and state["wallet"] == 200
    assert read_json(o.JOURNAL)["phase"] == "completed"
    assert ("withdraw_silver", 100) in events


def test_ten_meteors_keep_the_packing_branch_and_no_overflow(scenario):
    loop, local, bag, market, state, events = scenario
    bag.append(item(32, 1088001))
    assert not o.handle(loop, local)
    assert not events and state["map"] == 1011


@pytest.mark.parametrize("capacity,expected", [(0, 0), (1, 1), (2, 2)])
def test_full_market_stops_even_when_last_deposit_fills_it(
    scenario, monkeypatch, capacity, expected
):
    loop, local, bag, market, state, events = scenario
    state["capacity"] = capacity

    class Stopped(Exception):
        pass

    def stop(loop, stored, pending):
        assert len(stored["items"]) == capacity
        raise Stopped()

    monkeypatch.setattr(halt, "request_stop", stop)
    with pytest.raises(Stopped):
        o.handle(loop, local)
    assert len(market) == expected and len(bag) == 2 - expected
    assert read_json(o.JOURNAL)["phase"] == "full" and state["map"] == 1036
    assert ("trip", 1011) not in events


def test_resume_after_verified_deposit_does_not_repeat_uid(scenario):
    loop, local, bag, market, state, events = scenario
    plan = read_json(o.POLICY)["origins"]["1011"]
    market.append(bag.pop(0))
    state["map"] = 1036
    write_json(
        o.JOURNAL, {"phase": "market", "origin": 1011, "route": plan, "receipts": []}
    )
    assert o.resume(loop)
    assert [v["uid"] for v in market] == [30, 31]
    assert [e for e in events if e[0] == "deposit"] == [("deposit", 31)]


def test_uncertain_departure_never_pays_again(scenario):
    loop, local, bag, market, state, events = scenario
    write_json(
        o.JOURNAL,
        {
            "phase": "departing",
            "origin": 1011,
            "route": {},
            "departure_attempted": True,
        },
    )
    with pytest.raises(ValueError, match="no repeat fare"):
        o.resume(loop)
    assert not events


def test_already_full_market_does_not_attempt_any_deposit(scenario, monkeypatch):
    loop, local, bag, market, state, events = scenario
    market.extend(item(i) for i in range(state["capacity"]))

    def stop(*args):
        raise RuntimeError("full storage")

    monkeypatch.setattr(halt, "request_stop", stop)
    with pytest.raises(RuntimeError, match="full storage"):
        o.handle(loop, local)
    assert not any(e[0] == "deposit" for e in events) and len(bag) == 2
