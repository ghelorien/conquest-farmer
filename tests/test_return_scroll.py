from dataclasses import replace
from types import SimpleNamespace as NS
import pytest
from conquest import return_scroll as r
from conquest.memory_inventory import InventorySnapshot, Item


def snapshots():
    scroll = Item(1, r.TYPE, 2, 5, 0, 0)
    potion = Item(2, 1000020, 1, 1, 1, 0)
    bag = InventorySnapshot(1, 1, (scroll, potion), None, 1000, 40)
    after = replace(bag, items=(replace(scroll, amount=1), potion))
    source = NS(
        map_id=1002,
        position=(110, 345),
        object_address=123,
        current_hp=500,
        dead_candidate=False,
    )
    arrival = NS(
        map_id=1002,
        position=(430, 380),
        object_address=123,
        current_hp=500,
        dead_candidate=False,
    )
    return scroll, bag, after, source, arrival


def test_return_requires_consumption_same_character_and_living_town_arrival():
    item, before, after, source, arrival = snapshots()
    assert r.receipt(before, item, after, source, arrival)
    assert not r.receipt(before, item, before, source, arrival)
    assert not r.receipt(before, item, after, source, source)
    for changes in [
        dict(dead_candidate=True),
        dict(object_address=456),
        dict(map_id=1011),
    ]:
        assert not r.receipt(
            before, item, after, source, NS(**{**vars(arrival), **changes})
        )
    assert not r.receipt(before, item, replace(after, silver=999), source, arrival)
    assert not r.receipt(
        before, item, replace(after, items=after.items[:1]), source, arrival
    )


def test_last_scroll_disappears_but_inventory_slot_compaction_is_allowed():
    item, before, after, source, arrival = snapshots()
    item = replace(item, amount=1)
    before = replace(before, items=(item, before.items[1]))
    after = replace(after, items=(replace(before.items[1], slot=0),))
    assert r.receipt(before, item, after, source, arrival)


def test_a_scroll_reads_outside_town_or_where_twin_city_is_a_scroll_away():
    # ArcherGod's building (1004) and Phoenix Castle (1011) have no saved
    # Conductress trip back: the scroll is the way to Twin City.
    assert r.may_read(NS(map_id=1004, position=(37, 55)))
    assert r.may_read(NS(map_id=1011, position=(236, 263)))
    assert r.may_read(NS(map_id=1002, position=(110, 345)))
    assert not r.may_read(NS(map_id=1002, position=(430, 380)))  # in town
    assert not r.may_read(NS(map_id=1036, position=(100, 100)))  # Market


def test_phoenix_to_twin_city_buys_a_scroll_there_when_none_is_carried(monkeypatch):
    # Suicide reached level 26 at 17:28 and the brackets took it to Phoenix
    # Castle; Scatter farming then wants the Poltergeists back in Twin City.
    from conquest import world_travel

    state = {"map": 1011, "scrolls": 0, "silver": 3117, "calls": []}

    def town(action, **kw):
        state["calls"].append(action)
        if action == "supplies":
            items = [{"type_id": r.TYPE, "amount": state["scrolls"]}] if state["scrolls"] else []
            return {"items": items, "silver": state["silver"]}
        if action == "shop":
            return {"products": [{"type_id": r.TYPE, "price": 200}]}
        if action == "buy":
            assert state["map"] == 1011 and kw == {"vendor_type": 3, "type_id": r.TYPE}
            state.update(scrolls=1, silver=state["silver"] - 200)
            return {"bought": r.TYPE, "price": 200}
        return {}

    def read_scroll(loop):
        assert state["scrolls"] == 1
        state.update(scrolls=0, map=1002)
        state["calls"].append("scroll")
        return True

    monkeypatch.setattr(r, "return_to_town", read_scroll)
    monkeypatch.setattr(
        "conquest.city_travel.city_for",
        lambda map_id: {"services": {"pharmacist": [180, 245]}},
    )
    monkeypatch.setattr(world_travel, "read_terrain", lambda root, map_id: NS(map_id=map_id))
    loop = NS(
        town=town,
        travel=lambda target, **kw: state["calls"].append(f"travel:{list(target)}"),
        record=lambda *a, **k: None,
        living=lambda: {"embedded_controls": {"life": {"map_id": state["map"]}}},
    )
    world_travel.travel_to_map(loop, 1002)
    assert state["map"] == 1002 and state["silver"] == 2917
    assert "travel:[180, 245]" in state["calls"]
    assert state["calls"].index("buy") < state["calls"].index("scroll")


def test_unqualified_scroll_route_never_uses_input():
    r.write_json(r.POLICY, {"enabled": True, "qualified": False})
    assert not r.return_to_town(NS(living=lambda: pytest.fail("Not qualified")))


def test_stock_only_buys_two_at_verified_price_and_preserves_inventory_space():
    r.write_json(r.POLICY, {"enabled": True, "qualified": False})
    bag = {"silver": 1400, "items": [], "capacity": 40}
    buys = []

    def town(action, **kw):
        if action == "shop":
            return {"products": [{"type_id": r.TYPE, "price": 200}]}
        if action == "supplies":
            return bag
        assert action == "buy" and kw == {"vendor_type": 3, "type_id": r.TYPE}
        buys.append(kw)
        bag["items"].append({"type_id": r.TYPE, "amount": 1})
        bag["silver"] -= 200
        return {"bought": r.TYPE, "amount": 1, "price": 200}

    loop = NS(
        route=NS(restock_map_id=1002, supplies=NS(minimum_free_slots=4)),
        town=town,
        record=lambda *a, **k: None,
    )
    r.stock(loop)
    r.stock(loop)
    assert len(buys) == 2 and bag["silver"] == 1000
    # Short of silver the spare waits: one scroll, the rest stays for arrows.
    bag.update(silver=900, items=[])
    r.stock(loop)
    assert len(buys) == 3 and bag["silver"] == 700
    assert sum(i["amount"] for i in bag["items"]) == 1


# Toxic left town at 15:25 with 134 silver and no scroll (potions came first
# and stock needs 400) and died walking home from the Poltergeists at 15:35.
def test_the_first_scroll_comes_before_potions_whenever_200_silver_is_carried():
    r.write_json(r.POLICY, {"enabled": True, "qualified": True})
    bag = {"silver": 250, "items": [], "capacity": 40}
    buys = []

    def town(action, **kw):
        if action == "shop":
            return {"products": [{"type_id": r.TYPE, "price": 200}]}
        if action == "supplies":
            return bag
        assert action == "buy" and kw == {"vendor_type": 3, "type_id": r.TYPE}
        buys.append(kw)
        bag["items"].append({"type_id": r.TYPE, "amount": 1})
        bag["silver"] -= 200
        return {"bought": r.TYPE, "amount": 1, "price": 200}

    loop = NS(
        route=NS(restock_map_id=1002, supplies=NS(minimum_free_slots=4)),
        town=town,
        record=lambda *a, **k: None,
    )
    assert r.secure_one(loop) is True and bag["silver"] == 50
    # One carried scroll is enough before potions; stock tops up later.
    assert r.secure_one(loop) is False and len(buys) == 1
    bag.update(items=[], silver=199)
    assert r.secure_one(loop) is False and len(buys) == 1
    # A short quiver's arrow pack stays affordable (Suicide 15:20: 385 silver,
    # 53 arrows): the scroll waits for silver beyond the pack.
    bag.update(items=[], silver=385)
    assert r.secure_one(loop, keep=200) is False and len(buys) == 1
    bag["silver"] = 400
    assert r.secure_one(loop, keep=200) is True and bag["silver"] == 200
    r.write_json(r.POLICY, {"enabled": False})
    bag.update(items=[], silver=5000)
    assert r.secure_one(loop) is False and len(buys) == 2


# Return-scroll fallback and policy location (failure modes written first):
# 1. A scroll use that fails or cannot be verified aborts the restock, so the
#    route stops in the field instead of walking to town.
# 2. The failed attempt leaves the Inventory panel open for the walk.
# 3. The policy is read from the immutable release: recording qualification
#    there changes a manifest file and the next route launch is refused.
def test_failed_scroll_use_walks_to_town_instead_of_stopping_the_restock():
    r.write_json(r.POLICY, {"enabled": True, "qualified": True})
    calls, records = [], []

    def town(action, **kw):
        calls.append((action, kw.get("window")))
        if action == "supplies":
            return {"items": [{"type_id": r.TYPE, "amount": 2}]}
        if action == "return-scroll":
            raise ValueError("Town scroll transfer unverified; no repeat scroll issued")
        return {"closed": kw.get("window")}

    loop = NS(
        living=lambda: {
            "embedded_controls": {"life": {"map_id": 1002, "position": [700, 500]}}
        },
        town=town,
        record=lambda event, **fields: records.append(event),
    )
    assert r.return_to_town(loop) is False
    assert ("close", "Inventory") in calls[calls.index(("return-scroll", None)) :]
    assert records == ["return_scroll_failed"]


# Live 2026-09-27 11:07: the gear trip's scroll was refused with "Player or
# inventory changed before scroll input" right after farming stopped (a jump or
# pickup was still landing) and the farmer walked 591 tiles for two minutes.
# 4. The scroll is attempted before the farmer stands still.
# 5. A pre-input refusal (no scroll clicked) falls back to walking at once.
def test_scroll_waits_for_a_settled_farmer_and_retries_a_pre_input_refusal():
    r.write_json(r.POLICY, {"enabled": True, "qualified": True})
    positions = iter([[700, 500], [706, 506], [712, 512]] + [[712, 512]] * 200)
    attempts, records, reads = [], [], []

    def living():
        position = next(positions)
        reads.append(position)
        return {"embedded_controls": {"life": {"map_id": 1002, "position": position}}}

    def town(action, **kw):
        if action == "supplies":
            return {"items": [{"type_id": r.TYPE, "amount": 2}], "silver": 100}
        if action == "return-scroll":
            attempts.append(reads[-1])
            if len(attempts) == 1:
                raise ValueError("Player or inventory changed before scroll input")
            return {"state": "verified"}
        return {}

    loop = NS(
        living=living,
        town=town,
        record=lambda event, **fields: records.append(event),
    )
    assert r.return_to_town(loop) is True
    assert len(attempts) == 2 and all(p == [712, 512] for p in attempts)
    assert records == ["return_scroll_verified"]


def test_scroll_policy_is_character_state_not_release_content():
    assert r.POLICY_NAME.startswith(".runtime/")


def test_ambiguous_scroll_submission_blocks_a_second_click(monkeypatch):
    r.write_json(r.STATUS, {"state": "submitted"})
    _, _, _, source, _ = snapshots()
    trade = NS(life=lambda **kw: source, click=lambda *a: pytest.fail("No retry"))
    with pytest.raises(ValueError, match="no repeat"):
        r.use(trade)


def test_scroll_use_verifies_receipt_before_enabling_automatic_returns(monkeypatch):
    from dataclasses import dataclass

    @dataclass
    class Life:
        map_id: int = 1002
        position: tuple = (110, 345)
        object_address: int = 123
        current_hp: int = 500
        dead_candidate: bool = False

    item, before, after, _, _ = snapshots()
    state = {"used": False}
    clicks = []
    source = Life()
    arrival = replace(source, position=(430, 380))
    grid = NS(size=(407.0, 175.0), scroll=(0.0, 0.0), position=(100.0, 100.0))

    def read(name):
        if name in ("Shop", "Warehouse"):
            raise ValueError(name + " not active")
        return grid

    def click(point, button):
        clicks.append((point, button))
        state["used"] = True

    def verified(read, accept, message, **kw):
        result = read()
        assert accept(result), message
        return result

    trade = NS(
        life=lambda **kw: arrival if state["used"] else source,
        inventory=NS(read=lambda: after if state["used"] else before),
        shop=NS(gui=NS(read=read)),
        click=click,
        verified_read=verified,
    )
    result = r.use(trade)
    assert result["state"] == "verified" and len(clicks) == 1
    assert clicks[0] == ((120, 120), "right")
    assert r.read_json(r.POLICY)["qualified"] is True
    assert r.read_json(r.STATUS)["state"] == "verified"
