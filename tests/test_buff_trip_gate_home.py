"""A visit without a gate home still fetches MrBuffer's buff.

Live 2026-09-30 17:09: Alex read Toxic's last ApeCityGate by hand to bring
it home for a trade. The restart in Ape City walked out with three
TwinCityGates, no ApeCityGate and no buff: refresh_due needs the gate home,
and a restock checked for the trip before its Pharmacist stocked one, so the
buff waited a whole hunt. Now the trip follows the shopping when the visit
began without a gate home, and a start in town without one restocks first.
"""

from types import SimpleNamespace as NS

import pytest

from conquest import buff_trip, overnight, return_scroll
from conquest.city_travel import city_for
from conquest.overnight import OvernightLoop
from conquest.routes import RouteLibrary

TWIN_GATE = return_scroll.TYPE
APE_GATE = return_scroll.GATES[1020]


class Town:
    """Ape City's bag, wallet and shops, as restock uses them."""

    def __init__(self, route, gates_home=0):
        supplies = route.supplies
        self.arrow, self.potion = supplies.arrow_type, supplies.healing_type
        self.silver = 5000
        self.items = []
        for _ in range(4):
            self.add(self.arrow, amount=1000)
        for _ in range(supplies.healing_restock_to):
            self.add(self.potion)
        for _ in range(3):
            self.add(TWIN_GATE)
        for _ in range(gates_home):
            self.add(APE_GATE)
        self.bought, self.events = [], []

    def add(self, kind, amount=1):
        self.items.append(
            {
                "uid": len(self.items) + 1,
                "type_id": kind,
                "amount": amount,
                "limit": amount,
                "plus": 0,
                "slot": len(self.items),
            }
        )

    def count(self, kind):
        return sum(i["amount"] for i in self.items if i["type_id"] == kind)

    def spend(self, kind):
        self.items.remove(next(i for i in self.items if i["type_id"] == kind))

    def town(self, action, **fields):
        if action == "supplies":
            return {
                "items": [dict(i) for i in self.items],
                "equipped_ammo": {"type_id": self.arrow, "amount": 200, "limit": 200},
                "silver": self.silver,
                "capacity": 40,
            }
        if action == "shop":
            return {
                "products": [
                    {"type_id": APE_GATE, "price": return_scroll.GATE_PRICE},
                    {"type_id": self.potion, "price": 60},
                ]
            }
        if action == "buy":
            kind = fields["type_id"]
            price = return_scroll.GATE_PRICE if kind == APE_GATE else 60
            self.silver -= price
            self.add(kind)
            self.bought.append(kind)
            return {"bought": kind, "amount": 1, "price": price, "silver": self.silver}
        return {"ok": True}


@pytest.fixture
def ape_city(monkeypatch):
    from conquest import banking, equipment, session_plan, world_travel

    monkeypatch.setattr(banking, "fund_restock", lambda loop: None)
    monkeypatch.setattr(banking, "after_shopping", lambda loop, **kw: True)
    monkeypatch.setattr(
        equipment, "EquipmentReview", lambda loop: NS(visit=lambda vendor: None)
    )
    monkeypatch.setattr(session_plan, "upgrade_circuit", lambda loop: False)
    monkeypatch.setattr(world_travel, "travel_to_map", lambda loop, map_id: None)
    monkeypatch.setattr(overnight, "last_verified_price", lambda *a, **k: None)
    return_scroll.write_json(return_scroll.POLICY, {"enabled": True, "qualified": True})
    buff_trip.write_json(buff_trip.POLICY, {"stigma": True})
    # The 16:54 buff ran out at 17:24.
    buff_trip.write_json(buff_trip.STATE, {"stigma_at": 1.0})


def farmer(town, trips, monkeypatch):
    route = RouteLibrary().load("thunderape-scout")
    loop = OvernightLoop.__new__(OvernightLoop)
    loop.route = route
    loop.cycles = 0
    loop.last_level = 60
    loop.phase = "restocking"
    loop.town = town.town
    loop.record = lambda event, **fields: town.events.append(event)
    loop.travel = lambda target, **kw: None
    loop.optional_town_service = lambda: None
    loop.stop_farm = lambda: None
    loop.adopt_ammunition = lambda: None
    anchor = list(city_for(1020)["town_anchor"])
    loop.living = lambda: {
        "embedded_controls": {
            "life": {
                "max_hp": 960,
                "current_hp": 960,
                "map_id": 1020,
                "position": anchor,
                "dead_candidate": False,
            }
        }
    }

    def trip(loop, ride=False):
        # What the trip sees, then what a good one does: a gate home read,
        # the buff recorded.
        trips.append({"ride": ride, "gates_home": town.count(APE_GATE)})
        town.spend(APE_GATE)
        buff_trip.write_json(buff_trip.STATE, {"stigma_at": buff_trip.time.time()})
        return True

    monkeypatch.setattr(buff_trip, "trip", trip)
    return loop


def test_a_missing_gate_home_waits_for_a_restock(ape_city):
    home = [{"type_id": APE_GATE, "amount": 1}]
    twin = [{"type_id": TWIN_GATE, "amount": 1}]
    assert buff_trip.gate_home_missing(twin, home=1020)
    assert not buff_trip.refresh_due(twin, home=1020)
    assert not buff_trip.gate_home_missing(twin + home, home=1020)  # refresh_due's
    assert not buff_trip.gate_home_missing([], home=1020)  # the ride's (bootstrap)
    buff_trip.write_json(buff_trip.STATE, {"stigma_at": buff_trip.time.time()})
    assert not buff_trip.gate_home_missing(twin, home=1020)  # buffed
    buff_trip.write_json(buff_trip.STATE, {"failed_at": buff_trip.time.time()})
    assert not buff_trip.gate_home_missing(twin, home=1020)  # cooling down
    buff_trip.write_json(buff_trip.POLICY, {"stigma": False})
    assert not buff_trip.gate_home_missing(twin, home=1020)


def test_without_a_gate_home_the_trip_follows_the_shopping(ape_city, monkeypatch):
    town, trips = Town(RouteLibrary().load("thunderape-scout")), []
    loop = farmer(town, trips, monkeypatch)
    loop.restock()
    # The Pharmacist stocked two gates home before the one trip went, and
    # topped them back up to two after it read one home.
    assert trips == [{"ride": False, "gates_home": 2}]
    assert town.bought.count(APE_GATE) == 3 and loop.cycles == 1
    assert town.count(APE_GATE) == 2 and "restock_complete" in town.events


def test_a_danger_return_leaves_town_with_a_gate_home(ape_city, monkeypatch):
    # 2026-10-01 05:30-05:32, Toxic on snakeman-south: a boss-chase return
    # came home with one ApeCityGate. Six Amrita took the bag to its
    # free-slot reserve before stock() ran, so no spare was bought, and the
    # buff trip after the shopping read the only gate home. At 05:43 the next
    # boss-chase exit had no gate and walked for the Warehouseman from Love
    # Canyon until Alex stopped it.
    route = RouteLibrary().load("thunderape-scout")
    town, trips = Town(route, gates_home=1), []
    for _ in range(3):
        town.spend(town.potion)
    # Filler arrow packs: three potions leave the bag at the reserve.
    reserve = route.supplies.minimum_free_slots
    while len(town.items) < 40 - reserve - 3:
        town.add(town.arrow, amount=1000)
    loop = farmer(town, trips, monkeypatch)
    loop.return_reason = "boss_chase"  # urgent: the trip waits for the shopping
    loop.restock()
    assert trips == [{"ride": False, "gates_home": 2}]
    # The spare gate came before the potions took the room...
    assert town.bought[0] == APE_GATE
    # ...and the trip's read was bought back: the farmer leaves with two.
    assert town.count(APE_GATE) == 2 and "restock_complete" in town.events


def test_with_a_gate_home_the_trip_goes_first_and_once(ape_city, monkeypatch):
    town, trips = Town(RouteLibrary().load("thunderape-scout"), gates_home=1), []
    loop = farmer(town, trips, monkeypatch)
    loop.restock()
    # The trip read the carried gate; the Pharmacist then kept two, and the
    # fresh buff sends no second trip.
    assert trips == [{"ride": False, "gates_home": 1}]
    assert town.bought.count(APE_GATE) == 2 and town.count(APE_GATE) == 2


def test_a_start_in_town_without_a_gate_home_restocks_first(ape_city, monkeypatch):
    town, trips = Town(RouteLibrary().load("thunderape-scout")), []
    loop = farmer(town, trips, monkeypatch)
    restocks = []
    loop.restock = lambda **kw: restocks.append(kw)
    loop.prepare_supplies()
    assert restocks == [{}] and trips == []
    assert "supplies_ready" not in town.events
    # With the gate home carried the start goes straight to Twin City.
    town.add(APE_GATE)
    loop.prepare_supplies()
    assert restocks == [{}] and trips == [{"ride": False, "gates_home": 1}]
    assert "supplies_ready" in town.events
