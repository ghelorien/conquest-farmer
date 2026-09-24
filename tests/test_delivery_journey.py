import copy
import time
from types import SimpleNamespace as NS
import pytest
from conquest.discord_notify import read_json, write_json
from conquest.merchants import delivery_journey as journey, delivery_route
from conquest import banking, meteor_banking


def item(uid, kind=130009):
    return dict(uid=uid, type_id=kind, amount=1, limit=1, plus=0, slot=0)


@pytest.fixture
def trip(monkeypatch):
    world = {"map": 1011, "silver": 50, "stored_silver": 1000}
    bag = [item(1), item(2, 2000031), item(3, 1050002)]
    bank = []
    merchant = []
    events = []
    route = {
        "outbound": {
            "verified": True,
            "source_map": 1011,
            "destination_map": 1036,
            "fare": 100,
        },
        "return": {
            "verified": True,
            "source_map": 1036,
            "destination_map": 1011,
            "fare": 30,
        },
    }
    write_json(delivery_route.POLICY, {"enabled": True, "parity_verified": True})
    write_json(meteor_banking.POLICY, {"origins": {"1011": route}})

    def town(action, **fields):
        if action == "supplies":
            return {
                "silver": world["silver"],
                "items": copy.deepcopy(bag),
                "capacity": 40,
            }
        if action == "warehouse-money":
            return {"silver": world["silver"], "stored_silver": world["stored_silver"]}
        if action == "warehouse-items":
            return {
                "items": detailed(bank) if fields.get("rich") else copy.deepcopy(bank),
                "capacity": 40,
            }
        if action == "warehouse-deposit":
            assert read_json(journey.JOURNAL)["deposit_pending"]["uid"] == fields["uid"]
            row = next(i for i in bag if i["uid"] == fields["uid"])
            bag.remove(row)
            bank.append(row)
            events.append(("deposit", row["uid"]))
            return {
                "uid": row["uid"],
                "type_id": row["type_id"],
                "verified_in_warehouse": True,
            }
        pytest.fail(action)

    loop = NS(
        town=town,
        living=lambda: {"embedded_controls": {"life": {"map_id": world["map"]}}},
        record=lambda event, **fields: events.append((event, fields)),
    )

    def travel(loop, plan, *, before_submit=None):
        assert world["map"] == plan["source_map"]
        before_submit()
        events.append(("trip", plan["destination_map"]))
        world.update(map=plan["destination_map"], silver=world["silver"] - plan["fare"])

    def transfer(loop, direction, amount):
        assert direction == "withdraw"
        events.append(("withdraw", amount))
        world["silver"] += amount
        world["stored_silver"] -= amount

    monkeypatch.setattr(meteor_banking, "trip", travel)
    monkeypatch.setattr(
        meteor_banking,
        "approach_market_warehouse",
        lambda *a: events.append(("bank_approach", world["map"])),
    )
    monkeypatch.setattr(
        banking,
        "open_warehouse",
        lambda loop: events.append(("bank_open", world["map"])),
    )
    monkeypatch.setattr(
        banking,
        "close_warehouse",
        lambda loop: events.append(("bank_close", world["map"])),
    )
    monkeypatch.setattr(banking, "transport_reserve", lambda: 200)
    monkeypatch.setattr(banking, "transfer", transfer)
    monkeypatch.setattr(
        "conquest.navigation.read_terrain", lambda root, map_id: NS(map_id=map_id)
    )

    def deliver(loop, *, send):
        if not read_json(delivery_route.POLICY).get("enabled"):
            return
        for row in list(bag):
            if row["type_id"] == 130009:
                bag.remove(row)
                merchant.append(row)

    monkeypatch.setattr(delivery_route, "market_storage", deliver)

    def detailed(rows):
        return [
            {**i, "quantity": i["amount"], "gem1": 0, "gem2": 0, "bound": False}
            for i in rows
        ]

    def snap(name, uid, rows, map_id):
        return dict(
            character=name,
            character_uid=uid,
            identity={"pid": uid},
            server="America",
            timestamp=time.time(),
            hp=100,
            map_id=map_id,
            silver=100,
            capacity=40,
            inventory=detailed(rows),
            booth=[],
            booth_open=True,
        )

    def send(body):
        if body["action"] == "delivery-readiness":
            return {"qualified": True}
        if body["action"] == "status":
            return {
                "characters": {
                    "Dutch": {
                        "ready": True,
                        "snapshot": snap("Dutch", 2, merchant, 1036),
                    }
                }
            }
        if body["action"] == "delivery-source":
            return {"farmer": snap("Parasite", 1, bag, world["map"])}
        pytest.fail(body)

    return NS(
        loop=loop,
        world=world,
        bag=bag,
        bank=bank,
        merchant=merchant,
        events=events,
        send=send,
        travel=travel,
    )


def test_required_town_delivery_round_trip_keeps_supplies_and_fares(trip):
    assert journey.start(trip.loop, send=trip.send)
    assert trip.world["map"] == 1011 and trip.world["silver"] == 200
    assert [i["uid"] for i in trip.merchant] == [1]
    assert [i["uid"] for i in trip.bank] == [2]
    assert [i["uid"] for i in trip.bag] == [3]
    assert ("withdraw", 280) in trip.events
    assert read_json(journey.JOURNAL)["phase"] == "completed"
    assert trip.loop.overflow_bank_changed
    before = list(trip.events)
    assert not journey.start(
        trip.loop, send=trip.send
    )  # No second trip for ammunition or empty loot.
    assert trip.events == before


def test_last_fallback_slot_can_fill_when_merchant_still_has_capacity(
    trip, monkeypatch
):
    from test_delivery_route import snapshot

    original = trip.loop.town

    def town(action, **fields):
        result = original(action, **fields)
        if action == "warehouse-items":
            result["capacity"] = 1
        return result

    trip.loop.town = town

    def send(body):
        if body["action"] == "delivery-pair":
            return {
                "farmer": snapshot("Parasite", 1, [], (10, 10)),
                "merchant": snapshot(body["character"], 2, [], (20, 10)),
            }
        return trip.send(body)

    # Use the real capacity classifier and the same checked travel contract.
    trip.loop.terrain = NS(travel_path=lambda a, b: [a, b])
    # resume replaces terrain on arrival; furnish its fixture with path support.
    monkeypatch.setattr(
        "conquest.navigation.read_terrain", lambda *a: trip.loop.terrain
    )
    assert journey.start(trip.loop, send=send)
    assert trip.world["map"] == 1011 and len(trip.bank) == 1
    assert [i["uid"] for i in trip.bag] == [3]


@pytest.mark.parametrize(
    "reason",
    ["disabled", "unknown_binding", "unavailable", "unqualified_route", "no_funds"],
)
def test_failed_preflight_leaves_origin_warehouse_fallback_untouched(trip, reason):
    send = trip.send
    if reason == "disabled":
        write_json(delivery_route.POLICY, {"enabled": False})
    if reason == "unqualified_route":
        write_json(meteor_banking.POLICY, {})
    if reason == "no_funds":
        trip.world["stored_silver"] = 0

    def blocked(body):
        value = send(body)
        if reason == "unknown_binding" and body["action"] == "delivery-source":
            for row in value["farmer"]["inventory"]:
                row.pop("bound")
        if reason == "unavailable" and body["action"] == "status":
            value = {"characters": {}}
        return value

    assert not journey.start(trip.loop, send=blocked)
    assert trip.events == [] and trip.world["map"] == 1011
    assert not journey.pending()


def test_disconnect_after_fare_arrival_reconciles_without_repaying(trip, monkeypatch):
    def lost(loop, plan, **kw):
        trip.travel(loop, plan, **kw)
        if plan["destination_map"] == 1036:
            raise OSError("response lost after arrival")

    monkeypatch.setattr(meteor_banking, "trip", lost)
    with pytest.raises(OSError):
        journey.start(trip.loop, send=trip.send)
    assert journey.pending() and trip.world["map"] == 1036
    write_json(delivery_route.POLICY, {"enabled": False})
    assert journey.resume(trip.loop, send=trip.send)
    assert (
        trip.events.count(("trip", 1036)) == 1
        and trip.events.count(("trip", 1011)) == 1
    )


def test_uncertain_fare_while_still_in_origin_cannot_repeat(trip, monkeypatch):
    def uncertain(loop, plan, *, before_submit):
        before_submit()
        raise OSError("uncertain payment")

    monkeypatch.setattr(meteor_banking, "trip", uncertain)
    with pytest.raises(OSError):
        journey.start(trip.loop, send=trip.send)
    with pytest.raises(ValueError, match="no repeat fare"):
        journey.resume(trip.loop, send=trip.send)
    assert not any(e == "trip" for e, _ in trip.events)


def test_lost_deposit_receipt_reconciles_exact_bank_uid_before_return(
    trip, monkeypatch
):
    original = trip.loop.town

    def lost(action, **fields):
        value = original(action, **fields)
        if action == "warehouse-deposit":
            raise OSError("lost deposit response")
        return value

    trip.loop.town = lost
    with pytest.raises(OSError):
        journey.start(trip.loop, send=trip.send)
    assert read_json(journey.JOURNAL)["deposit_pending"]["uid"] == 2
    trip.loop.town = original
    assert journey.resume(trip.loop, send=trip.send)
    assert trip.events.count(("deposit", 2)) == 1 and trip.world["map"] == 1011


def test_uncertain_trade_never_starts_warehouse_input(trip, monkeypatch):
    def uncertain(*a, **kw):
        raise ValueError("trade uncertain")

    monkeypatch.setattr(delivery_route, "market_storage", uncertain)
    with pytest.raises(ValueError, match="trade uncertain"):
        journey.start(trip.loop, send=trip.send)
    assert not any(e in ("bank_approach", "deposit") for e, _ in trip.events)
    assert trip.world["map"] == 1036 and journey.pending()


def scroll_trip(trip, monkeypatch, *, lost=False):
    """Memory-only stand-in for the new journaled native withdrawal boundary."""
    trip.bag[:] = [item(3, 1050002)]
    trip.bank[:] = [item(99, 720027)]
    original = trip.loop.town

    def town(action, **fields):
        if action == "warehouse-deposit" and read_json(journey.JOURNAL).get(
            "loose_meteor_pending"
        ):
            row = next(i for i in trip.bag if i["uid"] == fields["uid"])
            assert row["type_id"] == 1088001
            trip.bag.remove(row)
            trip.bank.append(row)
            trip.events.append(("loose_deposit", row["uid"]))
            return {
                "uid": row["uid"],
                "type_id": 1088001,
                "verified_in_warehouse": True,
            }
        if action in ("warehouse-withdraw-scroll", "warehouse-reconcile-scroll"):
            state = read_json(journey.JOURNAL)
            assert state["scroll_withdrawal"] == fields
            assert not any(row["type_id"] == 1088001 for row in trip.bag)
            if action == "warehouse-withdraw-scroll":
                row = next(i for i in trip.bank if i["uid"] == fields["uid"])
                trip.bank.remove(row)
                trip.bag.append(row)
                trip.events.append(("scroll_withdraw", row["uid"]))
                if lost:
                    raise OSError("lost scroll withdrawal acknowledgement")
            else:
                trip.events.append(("scroll_reconcile", fields["uid"]))
            source = trip.send({"action": "delivery-source"})["farmer"]
            core = {
                k: next(i for i in source["inventory"] if i["uid"] == fields["uid"])[k]
                for k in ("uid", "type_id", "plus", "gem1", "gem2", "quantity", "bound")
            }
            return {
                **fields,
                "phase": "withdrawn",
                "receipt": {
                    "item": core,
                    "after": {"source": source},
                    "verified_at": time.time(),
                    "verified_in_inventory": True,
                    "verified_absent_from_warehouse": True,
                },
            }
        return original(action, **fields)

    trip.loop.town = town

    def deliver(loop, *, send, items=None, on_admitted=None):
        receipts = []
        for row in list(trip.bag):
            if row["type_id"] == 720027:
                if items is not None:
                    assert [i["uid"] for i in items] == [row["uid"]]
                rich = next(
                    i
                    for i in trip.send({"action": "delivery-source"})["farmer"][
                        "inventory"
                    ]
                    if i["uid"] == row["uid"]
                )
                active = {
                    "request_id": "journey-test-request",
                    "items": [rich],
                    "merchant": "Dutch",
                    "started_at": time.time(),
                }
                if on_admitted is not None:
                    on_admitted(active)
                trip.bag.remove(row)
                trip.merchant.append(row)
                trip.events.append(("scroll_delivery", row["uid"]))
                receipts.append(
                    {
                        **active,
                        "outcome": "transferred",
                        "proof_digest": "bilateral-proof",
                        "verified_at": time.time(),
                    }
                )
        if receipts:
            write_json(delivery_route.STATE, {"receipts": receipts})
        return receipts

    monkeypatch.setattr(delivery_route, "market_storage", deliver)


def test_exact_stored_scroll_starts_from_empty_eligible_bag(trip, monkeypatch):
    scroll_trip(trip, monkeypatch)
    assert journey.start(trip.loop, send=trip.send, stored_scroll_uid=99)
    assert [row["uid"] for row in trip.merchant] == [99]
    assert [row["uid"] for row in trip.bag] == [3]
    assert trip.world["map"] == 1011
    state = read_json(journey.JOURNAL)
    assert state["phase"] == "completed" and state["scroll_preparation_done"]
    assert state["scroll_withdrawal_receipt"]["verified_absent_from_warehouse"]
    assert trip.events.count(("scroll_withdraw", 99)) == 1


def test_already_in_market_entry_needs_neither_carried_loot_nor_money_action(
    trip, monkeypatch
):
    scroll_trip(trip, monkeypatch)
    trip.world["map"] = 1036
    assert journey.start_market(trip.loop, 99, send=trip.send)
    assert [row["uid"] for row in trip.merchant] == [99]
    assert trip.world["map"] == 1036
    assert not any(event[0] in ("trip", "withdraw") for event in trip.events)
    assert read_json(journey.JOURNAL)["phase"] == "completed"


def test_loose_meteor_leftovers_are_banked_before_exact_scroll_retrieval(
    trip, monkeypatch
):
    scroll_trip(trip, monkeypatch)
    trip.world["map"] = 1036
    trip.bag.append(item(77, 1088001))
    assert journey.start_market(trip.loop, 99, send=trip.send)
    assert trip.events.index(("loose_deposit", 77)) < trip.events.index(
        ("scroll_withdraw", 99)
    )
    assert [row["uid"] for row in trip.merchant] == [99]
    assert [row["uid"] for row in trip.bank] == [77]


def test_market_entry_cannot_start_any_delivery_before_banking_loose_leftovers(
    trip, monkeypatch
):
    scroll_trip(trip, monkeypatch)
    trip.world["map"] = 1036
    trip.bag.append(item(77, 1088001))
    deliver = delivery_route.market_storage

    def checked(loop, *, send, items=None, on_admitted=None):
        assert ("loose_deposit", 77) in trip.events
        assert ("scroll_withdraw", 99) in trip.events
        return deliver(loop, send=send, items=items, on_admitted=on_admitted)

    monkeypatch.setattr(delivery_route, "market_storage", checked)
    assert journey.start_market(trip.loop, 99, send=trip.send)


def test_requested_scroll_cannot_resume_a_different_journey(trip):
    write_json(journey.JOURNAL, {"phase": "prepared", "stored_scroll_uid": 100})
    with pytest.raises(ValueError, match="different merchant journey"):
        journey.start(trip.loop, send=trip.send, stored_scroll_uid=99)
    assert trip.events == []


def test_lost_scroll_ack_reconciles_before_movement_and_never_withdraws_twice(
    trip, monkeypatch
):
    scroll_trip(trip, monkeypatch, lost=True)
    trip.world["map"] = 1036
    with pytest.raises(OSError, match="lost scroll"):
        journey.start_market(trip.loop, 99, send=trip.send)
    assert journey.pending()
    count = len(trip.events)
    assert journey.start_market(trip.loop, 99, send=trip.send)
    assert trip.events[count] == ("scroll_reconcile", 99)
    assert trip.events.count(("scroll_withdraw", 99)) == 1
    assert trip.events.count(("scroll_delivery", 99)) == 1


def test_missing_requested_scroll_does_not_substitute_another_uid_or_loose_meteor(
    trip, monkeypatch
):
    scroll_trip(trip, monkeypatch)
    trip.world["map"] = 1036
    with pytest.raises(ValueError, match="not freshly verified"):
        journey.start_market(trip.loop, 100, send=trip.send)
    assert not any(event[0] == "scroll_withdraw" for event in trip.events)


def test_unsettled_loose_meteor_deposit_reconciles_before_any_movement(
    trip, monkeypatch
):
    scroll_trip(trip, monkeypatch)
    trip.world["map"] = 1036
    write_json(
        journey.JOURNAL,
        {
            "phase": "market",
            "origin": 1036,
            "market_only": True,
            "route": {},
            "stored_scroll_uid": 99,
            "receipts": [],
            "loose_meteor_pending": item(77, 1088001),
        },
    )
    with pytest.raises(ValueError, match="deposit is uncertain"):
        journey.resume(trip.loop, send=trip.send)
    assert trip.events == []


def test_delivery_after_scroll_retrieval_reconciles_before_warehouse_fallback(
    trip, monkeypatch
):
    scroll_trip(trip, monkeypatch)
    trip.world["map"] = 1036
    write_json(
        journey.JOURNAL,
        {
            "phase": "market",
            "origin": 1036,
            "market_only": True,
            "route": {},
            "stored_scroll_uid": 99,
            "receipts": [],
            "scroll_preparation_done": True,
            "scroll_withdrawal": {"uid": 99, "operation_id": "withdrawn"},
        },
    )

    def uncertain(*args, **kwargs):
        raise ValueError("trade uncertain")

    write_json(delivery_route.STATE, {"active": {"request_id": "pending-trade"}})
    monkeypatch.setattr(delivery_route, "settle", uncertain)
    with pytest.raises(ValueError, match="trade uncertain"):
        journey.resume(trip.loop, send=trip.send)
    assert trip.events == []


@pytest.mark.parametrize("prior", ["missing_admission", "no_transfer"])
def test_only_proven_no_input_admission_gets_a_new_operation_id(
    trip, monkeypatch, prior
):
    scroll_trip(trip, monkeypatch)
    trip.world["map"] = 1036
    old = {"uid": 99, "operation_id": "old-operation"}
    write_json(
        journey.JOURNAL,
        {
            "phase": "market",
            "origin": 1036,
            "market_only": True,
            "route": {},
            "stored_scroll_uid": 99,
            "receipts": [],
            "scroll_withdrawal": old,
        },
    )
    original = trip.loop.town

    def town(action, **fields):
        if action == "warehouse-reconcile-scroll" and fields == old:
            trip.events.append(("read_only_no_input", prior))
            return {
                **old,
                "phase": "no_transfer",
                "no_input_proven": True,
                "receipt": {
                    "input_attempted": False,
                    "admission_closed": prior == "missing_admission",
                },
            }
        assert fields.get("operation_id") != "old-operation"
        return original(action, **fields)

    trip.loop.town = town
    assert journey.start_market(trip.loop, 99, send=trip.send)
    state = read_json(journey.JOURNAL)
    assert state["scroll_withdrawal"]["operation_id"] != "old-operation"
    assert state["scroll_admissions"][0]["operation"] == old
    assert trip.events[0][0] == "read_only_no_input"
    assert trip.events.count(("scroll_withdraw", 99)) == 1


@pytest.mark.parametrize(
    "phase", ["input_maybe_sent", "reconciling", "blocked", "no_transfer"]
)
def test_ambiguous_or_unproven_operation_cannot_readmit(trip, monkeypatch, phase):
    scroll_trip(trip, monkeypatch)
    trip.world["map"] = 1036
    old = {"uid": 99, "operation_id": "old-operation"}
    write_json(
        journey.JOURNAL,
        {
            "phase": "market",
            "origin": 1036,
            "market_only": True,
            "route": {},
            "stored_scroll_uid": 99,
            "receipts": [],
            "scroll_withdrawal": old,
        },
    )

    def town(action, **fields):
        assert action == "warehouse-reconcile-scroll" and fields == old
        return {**old, "phase": phase, "no_input_proven": False, "receipt": {}}

    trip.loop.town = town
    with pytest.raises(ValueError, match="read-only reconciliation"):
        journey.resume(trip.loop, send=trip.send)
    assert trip.events == [] and journey.pending()


def test_carried_eligible_item_is_delivered_after_loose_bank_when_no_scroll_exists(
    trip, monkeypatch
):
    scroll_trip(trip, monkeypatch)
    trip.world["map"] = 1036
    trip.bag[:] = [item(1), item(77, 1088001), item(3, 1050002)]
    trip.bank[:] = []
    write_json(
        journey.JOURNAL,
        {
            "phase": "market",
            "origin": 1036,
            "market_only": True,
            "route": {},
            "receipts": [],
        },
    )

    def deliver(loop, *, send):
        assert ("loose_deposit", 77) in trip.events
        gear = next(row for row in trip.bag if row["uid"] == 1)
        trip.bag.remove(gear)
        trip.merchant.append(gear)
        trip.events.append(("gear_delivery", 1))

    monkeypatch.setattr(delivery_route, "market_storage", deliver)
    assert journey.resume(trip.loop, send=trip.send)
    assert [row["uid"] for row in trip.merchant] == [1]
    assert [row["uid"] for row in trip.bank] == [77]


def test_requested_market_scroll_stays_pending_if_readiness_disappears(
    trip, monkeypatch
):
    scroll_trip(trip, monkeypatch)
    trip.world["map"] = 1036
    reads = []

    def send(body):
        if body["action"] == "delivery-readiness":
            reads.append(True)
            return {"qualified": len(reads) == 1}
        return trip.send(body)

    with pytest.raises(ValueError, match="preparation is deferred"):
        journey.start_market(trip.loop, 99, send=send)
    assert read_json(journey.JOURNAL)["phase"] == "market"
    assert trip.events == []


@pytest.mark.parametrize("changed", ["missing_uid", "identity"])
def test_current_scroll_ownership_is_revalidated_before_closing_or_trading(
    trip, monkeypatch, changed
):
    scroll_trip(trip, monkeypatch)
    trip.world["map"] = 1036

    def send(body):
        value = trip.send(body)
        if (
            body["action"] == "delivery-source"
            and ("scroll_withdraw", 99) in trip.events
        ):
            if changed == "identity":
                value["farmer"]["identity"] = {"pid": 99}
            else:
                value["farmer"]["inventory"] = [
                    i for i in value["farmer"]["inventory"] if i["uid"] != 99
                ]
        return value

    with pytest.raises(ValueError, match="identity|no exact"):
        journey.start_market(trip.loop, 99, send=send)
    assert not any(e[0] in ("bank_close", "scroll_delivery") for e in trip.events)
    assert journey.pending()


def test_crash_after_preparation_save_closes_warehouse_before_delivery_on_resume(
    trip, monkeypatch
):
    scroll_trip(trip, monkeypatch)
    trip.world["map"] = 1036

    def crash(loop):
        raise OSError("crashed before warehouse close")

    monkeypatch.setattr(banking, "close_warehouse", crash)
    with pytest.raises(OSError, match="before warehouse close"):
        journey.start_market(trip.loop, 99, send=trip.send)
    saved = read_json(journey.JOURNAL)
    assert saved["scroll_preparation_done"] and not saved["scroll_warehouse_closed"]
    monkeypatch.setattr(
        banking,
        "close_warehouse",
        lambda loop: trip.events.append(("recovered_close", 1036)),
    )
    assert journey.resume(trip.loop, send=trip.send)
    assert trip.events.index(("recovered_close", 1036)) < trip.events.index(
        ("scroll_delivery", 99)
    )
    assert trip.events.count(("scroll_withdraw", 99)) == 1


def test_trade_reconciliation_precedes_close_after_preparation_crash(trip, monkeypatch):
    scroll_trip(trip, monkeypatch)
    trip.world["map"] = 1036

    def crash(loop):
        raise OSError("before close")

    monkeypatch.setattr(banking, "close_warehouse", crash)
    with pytest.raises(OSError):
        journey.start_market(trip.loop, 99, send=trip.send)
    write_json(delivery_route.STATE, {"active": {"request_id": "uncertain"}})

    def settle(*args):
        trip.events.append(("read_only_trade", None))
        raise ValueError("trade uncertain")

    monkeypatch.setattr(delivery_route, "settle", settle)
    monkeypatch.setattr(
        banking,
        "close_warehouse",
        lambda loop: pytest.fail("closed before trade settlement"),
    )
    with pytest.raises(ValueError, match="trade uncertain"):
        journey.resume(trip.loop, send=trip.send)
    assert trip.events[-1][0] == "read_only_trade"


def test_undelivered_exact_scroll_has_explicit_verified_rebanked_disposition(
    trip, monkeypatch
):
    scroll_trip(trip, monkeypatch)
    trip.world["map"] = 1036
    monkeypatch.setattr(delivery_route, "market_storage", lambda *a, **kw: [])
    assert journey.start_market(trip.loop, 99, send=trip.send)
    state = read_json(journey.JOURNAL)
    assert state["scroll_disposition"]["outcome"] == "deferred_rebanked"
    assert state["scroll_disposition"]["item"]["uid"] == 99
    assert [row["uid"] for row in trip.bank] == [99] and trip.merchant == []


@pytest.mark.parametrize("boundary", ["deposit_cleared", "disposition_saved"])
def test_interrupted_exact_scroll_rebank_reobserves_rich_warehouse_before_completion(
    trip, monkeypatch, boundary
):
    scroll_trip(trip, monkeypatch)
    trip.world["map"] = 1036
    monkeypatch.setattr(delivery_route, "market_storage", lambda *a, **kw: [])
    original_save = journey.save

    def crash(state, **fields):
        should_crash = (
            (
                boundary == "deposit_cleared"
                and fields.get("deposit_pending", "absent") is None
                and (state.get("deposit_pending") or {}).get("uid") == 99
            )
            or boundary == "disposition_saved"
            and fields.get("scroll_disposition", {}).get("outcome")
            == "deferred_rebanked"
        )
        original_save(state, **fields)
        if should_crash:
            raise OSError("crashed after verified fallback checkpoint")

    monkeypatch.setattr(journey, "save", crash)
    with pytest.raises(OSError, match="fallback checkpoint"):
        journey.start_market(trip.loop, 99, send=trip.send)
    saved = read_json(journey.JOURNAL)
    assert saved["deposit_pending"] is None and not any(
        i["uid"] == 99 for i in trip.bag
    )
    assert bool(saved.get("scroll_disposition")) == (boundary == "disposition_saved")
    monkeypatch.setattr(journey, "save", original_save)
    monkeypatch.setattr(
        delivery_route,
        "market_storage",
        lambda *a, **kw: pytest.fail("rebank recovery started merchant work"),
    )
    original_town = trip.loop.town
    reads = []

    def town(action, **fields):
        assert action not in ("warehouse-withdraw-scroll", "warehouse-reconcile-scroll")
        if action == "warehouse-items" and fields.get("rich"):
            reads.append(True)
        return original_town(action, **fields)

    trip.loop.town = town
    assert journey.resume(trip.loop, send=trip.send)
    assert reads and read_json(journey.JOURNAL)["phase"] == "completed"
    assert (
        read_json(journey.JOURNAL)["scroll_disposition"]["outcome"]
        == "deferred_rebanked"
    )
    assert trip.events.count(("scroll_withdraw", 99)) == 1


def test_stale_historical_bilateral_receipt_cannot_complete_current_withdrawal(
    trip, monkeypatch
):
    scroll_trip(trip, monkeypatch)
    trip.world["map"] = 1036

    def crash(loop):
        raise OSError("before close")

    monkeypatch.setattr(banking, "close_warehouse", crash)
    with pytest.raises(OSError):
        journey.start_market(trip.loop, 99, send=trip.send)
    state = read_json(journey.JOURNAL)
    selected = state["scroll_withdrawal_receipt"]["item"]
    write_json(
        delivery_route.STATE,
        {
            "receipts": [
                {
                    "request_id": "historical-other-journey",
                    "items": [selected],
                    "outcome": "transferred",
                    "proof_digest": "old-valid-proof",
                    "merchant": "Dutch",
                    "started_at": state["started_at"] - 100,
                    "verified_at": time.time(),
                }
            ]
        },
    )
    trip.bag[:] = [row for row in trip.bag if row["uid"] != 99]
    monkeypatch.setattr(
        banking,
        "close_warehouse",
        lambda loop: pytest.fail("closed with unresolved ownership"),
    )
    with pytest.raises(ValueError, match="no exact carried"):
        journey.resume(trip.loop, send=trip.send)
    assert journey.pending() and not read_json(journey.JOURNAL).get(
        "scroll_disposition"
    )


def test_current_journey_admission_recovers_bilateral_receipt_saved_before_return(
    trip, monkeypatch
):
    scroll_trip(trip, monkeypatch)
    trip.world["map"] = 1036
    original = delivery_route.market_storage

    def lost(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError("lost result after bilateral receipt")

    monkeypatch.setattr(delivery_route, "market_storage", lost)
    with pytest.raises(OSError, match="bilateral receipt"):
        journey.start_market(trip.loop, 99, send=trip.send)
    state = read_json(journey.JOURNAL)
    assert state["scroll_delivery_requests"] and not state.get(
        "scroll_delivery_receipts"
    )
    monkeypatch.setattr(
        delivery_route,
        "market_storage",
        lambda *a, **kw: pytest.fail("already transferred"),
    )
    assert journey.resume(trip.loop, send=trip.send)
    state = read_json(journey.JOURNAL)
    assert state["scroll_disposition"]["outcome"] == "delivered"
    assert (
        state["scroll_disposition"]["receipt"]["request_id"] == "journey-test-request"
    )
    assert trip.events.count(("scroll_delivery", 99)) == 1


def test_saved_rebanked_disposition_cannot_replace_fresh_missing_warehouse_evidence(
    trip, monkeypatch
):
    scroll_trip(trip, monkeypatch)
    trip.world["map"] = 1036
    monkeypatch.setattr(delivery_route, "market_storage", lambda *a, **kw: [])
    assert journey.start_market(trip.loop, 99, send=trip.send)
    state = read_json(journey.JOURNAL)
    assert state["scroll_disposition"]["outcome"] == "deferred_rebanked"
    state["phase"] = "market"
    write_json(journey.JOURNAL, state)
    trip.bank.clear()
    monkeypatch.setattr(
        delivery_route,
        "market_storage",
        lambda *a, **kw: pytest.fail("merchant work during bank recheck"),
    )
    with pytest.raises(ValueError, match="no exact carried"):
        journey.resume(trip.loop, send=trip.send)
    assert journey.pending() and trip.events.count(("scroll_withdraw", 99)) == 1
