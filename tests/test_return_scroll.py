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


def test_phoenix_to_twin_city_reads_a_carried_scroll_else_walks_the_portal(monkeypatch):
    # Suicide reached level 26 at 17:28 and the brackets took it to Phoenix
    # Castle; Scatter farming then wants the Poltergeists back in Twin City.
    # Phoenix's Pharmacist sells CastleGate, not TwinCityGate (live 17:43).
    from conquest import world_travel

    state = {"map": 1011, "scroll": True, "calls": []}

    def read_scroll(loop):
        if not state["scroll"]:
            return False
        state.update(scroll=False, map=1002)
        state["calls"].append("scroll")
        return True

    def walk_portal(loop, portal_id, expected):
        state["calls"].append(f"portal:{portal_id}")
        state["map"] = expected

    edge = dict(
        source_map=1011,
        destination_map=1002,
        portal_id=0,
        portal_position=[5, 376],
        source_terrain_sha256="same",
        destination_terrain_sha256="same",
    )
    terrain = NS(map_id=1011, source_sha256="same", portals=((5, 376, 0),))
    monkeypatch.setattr(r, "return_to_town", read_scroll)
    monkeypatch.setattr(world_travel, "connection_path", lambda *a: [edge])
    monkeypatch.setattr(world_travel, "read_terrain", lambda root, map_id: terrain)
    monkeypatch.setattr(world_travel, "cross_portal", walk_portal)
    monkeypatch.setattr("conquest.city_travel.city_for", lambda map_id: {})
    monkeypatch.setattr("conquest.city_travel.ensure_city_visit", lambda *a, **k: None)
    monkeypatch.setattr("conquest.conductress.take_saved_trip", lambda loop, d: False)
    loop = NS(living=lambda: {"embedded_controls": {"life": {"map_id": state["map"]}}})
    world_travel.travel_to_map(loop, 1002)
    assert state["calls"] == ["scroll"]
    state.update(map=1011, calls=[])
    world_travel.travel_to_map(loop, 1002)  # no scroll left
    assert state["calls"] == ["portal:0"] and state["map"] == 1002


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


def test_an_ape_city_restock_stocks_two_ape_city_gates():
    # overnight.gate_home_from_afar reads one from the far GiantApe and
    # ThunderApe fields; Ape City's Pharmacist sells ApeCityGate (1060022).
    from conquest.overnight import pharmacist_needed

    r.write_json(r.POLICY, {"enabled": True, "qualified": True})
    ape_gate = r.GATES[1020]
    bag = {"silver": 5000, "items": [], "capacity": 40}
    buys = []

    def town(action, **kw):
        if action == "shop":
            return {"products": [{"type_id": ape_gate, "price": 200}, {"type_id": r.TYPE, "price": 200}]}
        if action == "supplies":
            return bag
        assert action == "buy" and kw == {"vendor_type": 3, "type_id": ape_gate}
        buys.append(kw)
        bag["items"].append({"type_id": ape_gate, "amount": 1})
        bag["silver"] -= 200
        return {"bought": ape_gate, "amount": 1, "price": 200}

    route = NS(
        restock_map_id=1020,
        supplies=NS(
            minimum_free_slots=4,
            healing_restock_to=0,
            healing_type=1000030,
            arrow_type=1050001,
            arrows_return_below=0,
            arrows_restock_to=1,
        ),
    )
    loop = NS(route=route, town=town, record=lambda *a, **k: None)
    snapshot = {"items": [], "silver": 5000, "equipped_ammo": None, "capacity": 40}
    assert pharmacist_needed(snapshot, route, scroll_enabled=True)
    r.stock(loop)
    assert len(buys) == 2 and bag["silver"] == 4600
    assert not pharmacist_needed(dict(snapshot, items=bag["items"]), route, scroll_enabled=True)


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


@pytest.mark.parametrize("consumed, state", [(False, "unused"), (True, "submitted")])
def test_a_death_before_the_gate_takes_effect_leaves_no_block(consumed, state):
    # Laptop1 2026-10-01: a PK flight reads the gate under fire. Killed
    # before it took effect, the scroll is still carried (a potion drunk
    # meanwhile changes nothing): the next read must not be blocked.
    from dataclasses import make_dataclass

    item, before, after, field, _ = snapshots()
    Life = make_dataclass("Life", list(vars(field)))
    source = Life(**vars(field))
    grid = NS(size=(407.0, 175.0), scroll=(0.0, 0.0), position=(100.0, 100.0))
    state_box = {"clicked": False}

    def read(name):
        if name in ("Shop", "Warehouse"):
            raise ValueError(name + " not active")
        return grid

    def verified(read, accept, message, **kw):
        assert not accept(read())
        raise ValueError(message)

    drank = replace(before, items=before.items[:1])  # the potion is gone
    dead = replace(source, dead_candidate=True, current_hp=0)
    trade = NS(
        life=lambda **kw: dead if state_box["clicked"] else source,
        inventory=NS(
            read=lambda: (after if consumed else drank) if state_box["clicked"] else before
        ),
        shop=NS(gui=NS(read=read)),
        click=lambda point, button: state_box.update(clicked=True),
        verified_read=verified,
    )
    with pytest.raises(ValueError, match="unverified"):
        r.use(trade)
    assert r.read_json(r.STATUS)["state"] == state


def test_a_castlegate_landing_just_south_of_phoenix_town_is_received():
    # Suicide 2026-09-30 23:10:49: the CastleGate landed at ~(194, 264), six
    # tiles south of Phoenix's region.json town box (y <= 258). The receipt
    # failed and the "submitted" status blocked every later scroll.
    castle = Item(1, r.GATES[1011], 1, 1, 0, 0)
    potion = Item(2, 1000020, 1, 1, 1, 0)
    before = InventorySnapshot(1, 1, (castle, potion), None, 800, 40)
    after = replace(before, items=(replace(potion, slot=0),))
    source = NS(map_id=1002, position=(466, 333), object_address=7, current_hp=900,
                dead_candidate=False)
    landing = NS(map_id=1011, position=(194, 264), object_address=7, current_hp=900,
                 dead_candidate=False)
    assert r.receipt(before, castle, after, source, landing, 1011)
    # Far from town is still no receipt.
    far = NS(**{**vars(landing), "position": (194, 300)})
    assert not r.receipt(before, castle, after, source, far, 1011)


@pytest.mark.parametrize(
    "map_id, position, readable",
    [
        (1011, (202, 265), False),  # 7 tiles south of Phoenix's box (05:10:03)
        (1011, (194, 264), False),  # the CastleGate's own landing
        (1011, (220, 290), False),  # 26 tiles from the landing
        (1011, (230, 300), True),  # 36: received even with the landing's spread
        (1011, (150, 150), False),  # inside the town box
        (1011, (420, 450), True),  # the Bandit box
        (1002, (202, 265), True),  # another map: a gate always moves the farmer
        (1036, (100, 100), False),  # the Market
    ],
)
def test_no_gate_is_read_where_its_receipt_cannot_verify_it(map_id, position, readable):
    # Suicide 2026-10-01 05:10:03: a CastleGate read from (202, 265) landed 8
    # tiles away. The receipt needs a 32-tile move on the same map, so it
    # refused, and the "submitted" status blocked every later scroll.
    assert r.gate_readable(NS(map_id=map_id, position=position), 1011) is readable
