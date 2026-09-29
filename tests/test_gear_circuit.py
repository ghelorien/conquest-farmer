"""Twin City gear shopping from Ape City, whose shops sell no archer gear.

2026-09-29: on the Macaque hold every equipment review in Ape City said "This
shop does not stock this slot". Toxic reached 49 on a HornBow (45) with 75k
silver banked; Suicide reached 52 on level 37-45 gear with ~2.5k. Alex: "if
you can afford it, you can change all the items that are below unique grade
that you are currently wearing. If not you cannot change them".
"""

from types import SimpleNamespace as NS

import pytest

from conquest import gear_circuit as g
from conquest import return_scroll as r
from conquest.overnight import OvernightStopped

APE_TOWN = [566, 565]
TC_TOWN = [429, 378]


def worn(level=50, bow=500075, ring=150075, boots=160075, necklace=120065):
    """Toxic's set on 2026-09-29: HornBow, AmethystRing, EagleBoots, JadeNecklace."""

    def item(type_id, name, item_level, **stats):
        return {
            "uid": type_id,
            "type_id": type_id,
            "name": name,
            "level": item_level,
            "profession": 40,
            "sex": 0,
            "plus": 0,
            "gem1": 0,
            "gem2": 0,
            **{"attack_min": 0, "attack_max": 0, "defense": 0, "dodge": 0, **stats},
        }

    return {
        "level": level,
        "equipment": {
            "bow": item(bow, "HornBow", 45, attack_min=166, attack_max=204),
            "ring": item(ring, "AmethystRing", 40, attack_min=27, attack_max=58),
            "boots": item(boots, "EagleBoots", 40, dodge=9),
            "necklace": item(necklace, "JadeNecklace", 37, defense=10),
        },
    }


def test_level_50_plans_the_bow_ring_boots_and_necklace_when_affordable():
    names = [p["name"] for p in g.planned(worn(), 78_000, 1020)]
    assert names == ["QinBow", "IvoryRing", "LightBoots", "CrystalNecklace"]


def test_a_short_wallet_buys_nothing_alex_upgrades_by_hand():
    # Suicide: ~2.5k silver. Nothing fits above the floor kept for arrows.
    assert g.planned(worn(level=52), 2_500, 1020) == []
    # Only what fits above the floor, best slot first (the bow).
    assert [p["name"] for p in g.planned(worn(), g.KEEP_SILVER + 10_000, 1020)] == [
        "QinBow"
    ]


def test_unique_or_better_worn_gear_is_never_replaced():
    # 500077: a Unique HornBow (type % 10 == 7) stays on.
    names = [p["name"] for p in g.planned(worn(bow=500077), 78_000, 1020)]
    assert "QinBow" not in names and "IvoryRing" in names


def test_twin_city_restock_needs_no_trip():
    assert g.planned(worn(), 78_000, 1002) == []


def test_a_lone_necklace_at_49_waits_for_the_level_50_bow():
    plan = g.planned(worn(level=49), 78_000, 1020)
    assert [p["name"] for p in plan] == ["CrystalNecklace"]
    assert not g.worth_trip(plan)
    assert g.worth_trip(g.planned(worn(), 78_000, 1020))
    farmer = Farmer()
    farmer.town = lambda action, **kw: (
        worn(level=49) if action == "gear" else Farmer.town(farmer, action, **kw)
    )
    assert g.run(farmer) is False and farmer.calls == []


class Farmer:
    """A farmer in Ape City town after its restock, all silver withdrawn."""

    def __init__(self, *, twin_gates=1, fail=None):
        self.map_id, self.position = 1020, [550, 547]
        self.items = [{"type_id": r.TYPE, "amount": 1}] * twin_gates
        self.silver = 78_000
        self.calls = []
        self.events = []
        self.fail = fail
        self.phase = "restocking"
        self.route = NS(restock_map_id=1020, supplies=NS(minimum_free_slots=4))

    def living(self):
        return {"embedded_controls": {"life": {"map_id": self.map_id, "position": self.position}}}

    def record(self, event, **fields):
        self.events.append(event)

    def travel(self, point, **kw):
        self.calls.append(("travel", tuple(point)))
        if self.fail == "travel" and self.map_id == 1002:
            raise ValueError("Travel was not verified")
        if self.fail == "stop" and self.map_id == 1002:
            raise OvernightStopped("Farming Off")
        self.position = list(point)

    def town(self, action, **kw):
        if action == "gear":
            return worn()
        if action == "supplies":
            return {"silver": self.silver, "items": list(self.items), "capacity": 40}
        if action == "shop":
            gate = r.GATES[self.map_id]
            return {"products": [{"type_id": gate, "price": 200}]}
        self.calls.append((action, kw.get("type_id") or kw.get("vendor_type") or kw.get("window")))
        if action == "buy":
            self.items.append({"type_id": kw["type_id"], "amount": 1})
            self.silver -= 200
            return {"bought": kw["type_id"], "amount": 1, "price": 200}
        if action == "gate-scroll":
            kind = kw["type_id"]
            self.items.remove(next(i for i in self.items if i["type_id"] == kind))
            destination = next(m for m, t in r.GATES.items() if t == kind)
            self.map_id = destination
            self.position = list(TC_TOWN if destination == 1002 else APE_TOWN)
            return {"state": "verified", "map_id": destination}
        return {}


@pytest.fixture
def qualified(monkeypatch):
    r.write_json(r.POLICY, {"enabled": True, "qualified": True})
    monkeypatch.setattr(r, "settle", lambda loop: True)
    visits = []

    class Review:
        def __init__(self, loop, *, minimum_reserve=0):
            self.loop, self.reserve = loop, minimum_reserve

        def visit(self, vendor):
            visits.append((vendor, self.reserve, self.loop.map_id))
            return True

    monkeypatch.setattr("conquest.equipment.EquipmentReview", Review)
    monkeypatch.setattr(
        "conquest.city_travel.ensure_city_visit",
        lambda loop, new_arrival=False: loop.calls.append(("city_visit", loop.map_id)),
    )
    return visits


def test_trip_reads_gates_both_ways_and_shops_above_the_floor(qualified):
    farmer = Farmer()
    assert g.run(farmer) is True
    actions = [c for c in farmer.calls if c[0] in ("buy", "gate-scroll", "city_visit")]
    assert actions == [
        ("buy", 1060022),  # the way home first
        ("gate-scroll", r.TYPE),
        ("city_visit", 1002),
        ("buy", r.TYPE),  # two TwinCityGates for the next trip
        ("buy", r.TYPE),
        ("gate-scroll", 1060022),
    ]
    # Blacksmith, Shopkeeper and Armorer in Twin City, never below the floor.
    assert qualified == [(5, g.KEEP_SILVER, 1002), (1, g.KEEP_SILVER, 1002), (4, g.KEEP_SILVER, 1002)]
    assert farmer.map_id == 1020 and farmer.phase == "restocking"
    assert "gear_circuit_complete" in farmer.events
    state = g.read_json(g.STATE)
    assert state["level"] == 50 and state["trips"] == 1 and state["way"] == "gate"


def test_no_gate_and_no_saved_ride_waits_instead_of_walking_the_plain(qualified, monkeypatch):
    monkeypatch.setattr(g, "saved_ride", lambda source, destination: None)
    farmer = Farmer(twin_gates=0)
    assert g.run(farmer) is False
    assert g.run(farmer) is False
    assert farmer.calls == [] and farmer.events == ["gear_circuit_waiting"]


def test_a_failed_step_in_twin_city_still_goes_home_by_gate(qualified):
    farmer = Farmer(fail="travel")
    assert g.run(farmer) is True
    assert farmer.map_id == 1020
    assert "gear_circuit_failed" in farmer.events
    assert farmer.events.index("gear_circuit_failed") < farmer.events.index(
        "gear_circuit_returning"
    )


def test_a_stop_request_does_not_walk_home(qualified):
    farmer = Farmer(fail="stop")
    with pytest.raises(OvernightStopped):
        g.run(farmer)
    assert farmer.map_id == 1002 and "gear_circuit_returning" not in farmer.events
    assert farmer.phase == "restocking"


def test_two_trips_per_level_at_most(qualified):
    for _ in range(2):
        farmer = Farmer(fail="travel")
        assert g.run(farmer) is True
    assert g.run(Farmer()) is False


def test_the_saved_ride_lands_beside_its_exit_portal(qualified, monkeypatch):
    ride = {"source_map": 1020, "destination_map": 1002, "exit_portal": 1, "service": {}}
    monkeypatch.setattr(g, "saved_ride", lambda source, destination: ride)
    crossed = []

    def take(loop, trip):
        loop.calls.append(("ride", trip["exit_portal"]))
        loop.position = [378, 14]

    def cross(loop, portal_id, expected):
        crossed.append(portal_id)
        loop.map_id, loop.position = expected, [555, 957]
        return {"portal_id": portal_id}

    monkeypatch.setattr("conquest.conductress.take_service_trip", take)
    monkeypatch.setattr("conquest.world_travel.cross_portal", cross)
    farmer = Farmer(twin_gates=0)
    assert g.run(farmer) is True
    assert ("ride", 1) in farmer.calls and crossed == [1]
    assert g.read_json(g.STATE)["way"] == "ride" and farmer.map_id == 1020


def test_a_landing_far_from_the_exit_portal_goes_home_by_gate(qualified, monkeypatch):
    ride = {"source_map": 1020, "destination_map": 1002, "exit_portal": 1, "service": {}}
    monkeypatch.setattr(g, "saved_ride", lambda source, destination: ride)

    def take(loop, trip):
        loop.position = [600, 280]  # the GiantApe plain, not portal 1 (376, 8)

    monkeypatch.setattr("conquest.conductress.take_service_trip", take)
    monkeypatch.setattr(
        "conquest.world_travel.cross_portal",
        lambda *a: pytest.fail("No walk from a far landing"),
    )
    farmer = Farmer(twin_gates=0)
    assert g.run(farmer) is True
    assert "gear_circuit_failed" in farmer.events
    assert ("gate-scroll", 1060022) in farmer.calls
    assert farmer.map_id == 1020 and farmer.position == [550, 547]


def test_ape_city_gate_receipt_and_where_gates_read():
    from dataclasses import replace

    from conquest.memory_inventory import InventorySnapshot, Item

    gate = Item(1, 1060022, 1, 5, 0, 0)
    before = InventorySnapshot(1, 1, (gate,), None, 5000, 40)
    after = replace(before, items=())
    source = NS(map_id=1002, position=(452, 335), object_address=7)
    arrival = NS(
        map_id=1020,
        position=(566, 565),
        object_address=7,
        current_hp=800,
        dead_candidate=False,
    )
    assert r.receipt(before, gate, after, source, arrival, 1020)
    # Landing outside Ape City's town box is no verified arrival.
    assert not r.receipt(before, gate, after, source, NS(**{**vars(arrival), "position": (381, 21)}), 1020)
    # Gates read anywhere but the destination's town and the Market.
    assert r.gate_readable(NS(map_id=1002, position=(452, 335)), 1020)
    assert r.gate_readable(NS(map_id=1020, position=(636, 646)), 1020)
    assert r.gate_readable(NS(map_id=1020, position=(636, 646)), 1002)
    assert not r.gate_readable(NS(map_id=1020, position=(550, 547)), 1020)
    assert not r.gate_readable(NS(map_id=1036, position=(200, 200)), 1002)
    # return_to_town keeps its rule: no TwinCityGate read from Ape City there.
    assert not r.may_read(NS(map_id=1020, position=(636, 646)))


def test_map_travel_prefers_a_carried_gate_over_the_plain(monkeypatch):
    from conquest import world_travel

    state = {"map": 1002}

    def gate(loop, destination):
        state["map"] = destination
        return True

    monkeypatch.setattr(r, "read_gate", gate)
    monkeypatch.setattr(
        world_travel, "connection_path", lambda *a: pytest.fail("No walk with a gate")
    )
    monkeypatch.setattr(world_travel, "read_terrain", lambda root, map_id: NS(map_id=map_id))
    loop = NS(living=lambda: {"embedded_controls": {"life": {"map_id": state["map"]}}})
    world_travel.travel_to_map(loop, 1020)
    assert state["map"] == 1020
