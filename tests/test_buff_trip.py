"""Twin City buff trips for MrBuffer's double damage.

Alex 2026-09-30: "mr buffer will double your damage for 30 minutes when you
walk past him ... if you die, you lose the double damage buff ... keep twin
city scrolls available and use one to go grab the buff, then another scroll
back to ape city"; "mrbuffer roams around the middle square". He is a
player-range actor, "MrBuffer[Bot]" (uid 2146483646), at (455, 368) on
2026-09-30 07:22; a TwinCityGate lands on (429, 378).
"""

import struct
from types import SimpleNamespace as NS

import pytest

from conquest import buff_trip as b
from conquest import return_scroll as r

APE_TOWN = [565, 562]
TC_LANDING = [429, 378]


@pytest.fixture
def clock(monkeypatch):
    now = [1_000_000.0]
    monkeypatch.setattr(b.time, "time", lambda: now[0])
    monkeypatch.setattr(b.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(b.time, "sleep", lambda s: now.__setitem__(0, now[0] + s))
    return now


def gate(n=1):
    return [{"type_id": r.TYPE, "amount": 1}] * n


def test_off_by_default_and_a_first_visit_waits_for_a_restock(clock):
    assert not b.refresh_due(gate())
    b.write_json(b.POLICY, {"stigma": True})
    assert b.refresh_due(gate())  # never buffed: the next restock goes
    assert not b.refresh_due([])  # no TwinCityGate: straight home
    assert not b.hunt_should_end(gate())  # no hunt ends before a first visit


def test_the_buff_is_refreshed_at_restocks_and_when_it_runs_out(clock):
    b.write_json(b.POLICY, {"stigma": True})
    b.write_json(b.STATE, {"stigma_at": clock[0]})
    assert b.stigma_left() == b.STIGMA_SECONDS
    assert not b.refresh_due(gate()) and not b.hunt_should_end(gate())
    clock[0] += b.STIGMA_SECONDS - b.REFRESH_WITHIN + 1
    assert b.refresh_due(gate()) and not b.hunt_should_end(gate())
    clock[0] += b.REFRESH_WITHIN - b.HUNT_END_WITHIN
    assert b.hunt_should_end(gate())
    clock[0] += 3600
    assert b.stigma_left() == 0 and b.hunt_should_end(gate())


def test_a_death_clears_the_buff(clock):
    b.write_json(b.STATE, {"stigma_at": clock[0]})
    assert b.lost("death") and b.stigma_left() == 0
    assert not b.lost("death")  # nothing left to lose


def test_restocks_keep_silver_carried_for_the_twin_city_gates(clock):
    # 2026-09-30 08:38: the first trip reached Twin City's Pharmacist with 100
    # silver after the fare (the rest banked in Ape City) and bought no gate.
    assert b.silver_needed() == 0
    b.write_json(b.POLICY, {"stigma": True})
    assert b.silver_needed() == b.GATE_KEEP * r.GATE_PRICE + 200


def test_a_failed_trip_cools_down(clock):
    b.write_json(b.POLICY, {"stigma": True})
    b.write_json(b.STATE, {"failed_at": clock[0]})
    assert not b.refresh_due(gate())
    clock[0] += b.FAILURE_COOLDOWN + 1
    assert b.refresh_due(gate())


def test_no_refresh_without_a_gate_home(clock):
    # 2026-09-30 09:57 (Suicide, ThunderApe field): no ApeCityGate and 200
    # silver. The refresh would have spent the fare on a TwinCityGate and left
    # Twin City's only way home a Conductress ride and the GiantApe plain.
    b.write_json(b.POLICY, {"stigma": True})
    b.write_json(b.STATE, {"stigma_at": clock[0] - b.STIGMA_SECONDS})
    home = [{"type_id": r.GATES[1020], "amount": 1}]
    assert b.refresh_due(gate() + home, home=1020)
    assert b.hunt_should_end(gate() + home, home=1020)
    assert not b.refresh_due(gate(), home=1020)
    assert not b.hunt_should_end(gate(), home=1020)
    assert b.way_home([], 1002)  # Twin City's own trips need no gate


def test_a_trip_without_a_gate_home_stays_put(qualified, monkeypatch):
    farmer = Farmer()
    farmer.items = gate()  # the TwinCityGate only
    assert run(farmer, monkeypatch) is False
    assert not [c for c in farmer.calls if c[0] in ("quiet", "gate-scroll", "buy", "travel")]
    assert [e for e, _ in farmer.events] == ["buff_trip_skipped"]
    assert farmer.map_id == 1020 and not b.cooling_down()
    ride = {"source_map": 1020, "destination_map": 1002, "exit_portal": 1, "service": {}}
    monkeypatch.setattr("conquest.gear_circuit.saved_ride", lambda source, destination: ride)
    assert not b.bootstrap_due(farmer, [])  # nor a ride out with none to come back
    assert b.bootstrap_due(farmer, [{"type_id": r.GATES[1020], "amount": 1}])


class Farmer:
    """A farmer returning from the ThunderApe field to restock in Ape City."""

    def __init__(self, *, twin_gates=1, buffer_path=None):
        self.map_id, self.position = 1020, [330, 300]
        self.items = gate(twin_gates) + [{"type_id": 1060022, "amount": 1}]
        self.silver = 100_000
        self.calls, self.events = [], []
        self.phase = "hunting"
        self.route = NS(restock_map_id=1020, supplies=NS(minimum_free_slots=4))
        # MrBuffer's tile each time the scene is read (None: out of view).
        self.buffer_path = list(buffer_path or [(455, 368)])

    def living(self):
        return {"embedded_controls": {"life": {"map_id": self.map_id, "position": list(self.position)}}}

    def quiet_for_gate(self):
        self.calls.append(("quiet",))
        return True

    def record(self, event, **fields):
        self.events.append((event, fields))

    def travel(self, point, **kw):
        self.calls.append(("travel", tuple(point), kw.get("arrival_radius", 0)))
        radius = kw.get("arrival_radius", 0)
        self.position = [point[0] - radius, point[1]]

    def find(self):
        return self.buffer_path.pop(0) if len(self.buffer_path) > 1 else self.buffer_path[0]

    def town(self, action, **kw):
        if action == "supplies":
            return {"silver": self.silver, "items": list(self.items), "capacity": 40}
        if action == "shop":
            return {"products": [{"type_id": r.GATES[self.map_id], "price": 200}]}
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
            self.position = list(TC_LANDING if destination == 1002 else APE_TOWN)
            return {"state": "verified", "map_id": destination}
        return {}


@pytest.fixture
def qualified(monkeypatch, clock):
    r.write_json(r.POLICY, {"enabled": True, "qualified": True})
    b.write_json(b.POLICY, {"stigma": True})
    monkeypatch.setattr(r, "settle", lambda loop: True)
    monkeypatch.setattr(b, "arrived", lambda loop, map_id: loop.calls.append(("arrived", map_id)))


def run(farmer, monkeypatch):
    real = b.visit_buffer
    monkeypatch.setattr(b, "visit_buffer", lambda loop, **kw: real(loop, find=loop.find, **kw))
    return b.trip(farmer)


def test_trip_gates_out_walks_past_mrbuffer_stocks_gates_and_gates_home(qualified, monkeypatch):
    farmer = Farmer()
    assert run(farmer, monkeypatch) is True
    steps = [c for c in farmer.calls if c[0] in ("quiet", "gate-scroll", "buy")]
    assert steps == [
        ("quiet",),  # never read under fire
        ("gate-scroll", r.TYPE),
        ("buy", r.TYPE), ("buy", r.TYPE), ("buy", r.TYPE),  # GATE_KEEP for the next trips
        ("gate-scroll", 1060022),
    ]
    # Beside MrBuffer, then the Pharmacist (466, 333).
    walks = [c[1] for c in farmer.calls if c[0] == "travel"]
    assert walks[0] == (455, 368) and walks[-1] == (466, 333)
    assert farmer.map_id == 1020 and farmer.phase == "hunting"
    names = [e for e, _ in farmer.events if e.startswith("buff_")]
    assert names == ["buff_trip_departing", "buff_received", "buff_trip_complete"]
    assert b.stigma_left() > b.STIGMA_SECONDS - 60
    # Each map's terrain before its walks: 10:04, the first trip by gate
    # planned MrBuffer's tile on Ape Mountain's terrain and failed.
    maps = [c for c in farmer.calls if c[0] in ("gate-scroll", "arrived", "travel")]
    assert maps[:3] == [("gate-scroll", r.TYPE), ("arrived", 1002), ("travel", (455, 368), 2)]
    assert maps[-2:] == [("gate-scroll", 1060022), ("arrived", 1020)]


def test_a_twin_city_restock_walks_past_mrbuffer_from_town(qualified, monkeypatch):
    # Suicide on Twin City's Poltergeists, 2026-10-01 07:54: a Twin City home
    # skipped MrBuffer, so every hunt ended for the buff at once and the
    # farmer looped Conductress, TwinCityGate and restock.
    farmer = Farmer()
    farmer.route = NS(restock_map_id=1002, supplies=NS(minimum_free_slots=4))
    farmer.map_id, farmer.position = 1002, [130, 370]  # the Poltergeist field
    assert run(farmer, monkeypatch) is False  # restock start, out of town: later
    assert farmer.calls == [] and b.stigma_left() == 0
    farmer.position = [466, 333]  # after the shopping, at the Pharmacist
    assert b.trip(farmer) is False  # no gate read, no trip
    assert [c[1] for c in farmer.calls if c[0] == "travel"][-1] == (455, 368)
    assert not [c for c in farmer.calls if c[0] in ("gate-scroll", "buy", "quiet")]
    assert [e for e, _ in farmer.events] == ["buff_received"]
    assert b.stigma_left() > b.STIGMA_SECONDS - 60


@pytest.mark.parametrize(
    "map_id, restock, carried, buys",
    [
        (1002, 1011, 1, 2),  # Twin City field restocked in Phoenix: top up to GATE_KEEP
        (1002, 1011, 3, 0),  # stocked already
        (1002, 1002, 0, 0),  # restocks in Twin City itself
        (1011, 1011, 0, 0),  # a Phoenix field
    ],
)
def test_arriving_in_twin_city_tops_up_twin_city_gates(qualified, monkeypatch, map_id, restock, carried, buys):
    # 2026-10-01 11:0x: Suicide's buff trips (which bought TwinCityGates) were
    # turned off for Twin City's weak fields; each Phoenix restock still
    # returns by TwinCityGate, and Phoenix sells none.
    farmer = Farmer(twin_gates=carried)
    farmer.route = NS(map_id=map_id, restock_map_id=restock, supplies=NS(minimum_free_slots=4))
    farmer.map_id, farmer.position = 1002, list(TC_LANDING)
    assert b.stock_on_arrival(farmer) is bool(buys)
    assert [c for c in farmer.calls if c[0] == "buy"] == [("buy", r.TYPE)] * buys
    assert not [c for c in farmer.calls if c[0] in ("gate-scroll", "quiet")]


def test_short_of_silver_for_gates_the_trip_says_so(qualified, monkeypatch):
    farmer = Farmer()
    farmer.silver = 100  # 08:38: 200 carried, minus the Conductress fare
    assert run(farmer, monkeypatch) is True
    short = [f for e, f in farmer.events if e == "buff_trip_gates_short"]
    assert short and short[0]["carried"] == 0 and short[0]["silver"] == 100
    assert not [c for c in farmer.calls if c[0] == "buy"]
    assert farmer.map_id == 1020  # still home by the ApeCityGate


def test_mrbuffer_roams_so_the_walk_follows_him(qualified, monkeypatch):
    # Out of view on landing, then seen twice as he moves.
    farmer = Farmer(buffer_path=[None, (440, 360), (448, 366)])
    assert run(farmer, monkeypatch) is True
    walks = [c[1] for c in farmer.calls if c[0] == "travel"]
    assert walks[:3] == [b.SQUARE, (440, 360), (448, 366)]


def test_mrbuffer_missing_still_goes_home_and_cools_down(qualified, monkeypatch):
    farmer = Farmer(buffer_path=[None])
    assert run(farmer, monkeypatch) is True
    names = [e for e, _ in farmer.events]
    assert "buff_trip_failed" in names and names[-1] == "buff_trip_complete"
    assert farmer.map_id == 1020 and b.cooling_down()
    assert ("gate-scroll", 1060022) in farmer.calls


def test_no_quiet_spot_reads_no_gate(qualified, monkeypatch):
    farmer = Farmer()
    farmer.quiet_for_gate = lambda: False
    assert run(farmer, monkeypatch) is False
    assert not [c for c in farmer.calls if c[0] == "gate-scroll"]
    assert farmer.map_id == 1020 and b.cooling_down()


def test_already_in_twin_city_skips_the_outbound_gate(qualified, monkeypatch):
    farmer = Farmer()
    farmer.map_id, farmer.position = 1002, [448, 368]
    assert run(farmer, monkeypatch) is True
    gates = [c for c in farmer.calls if c[0] == "gate-scroll"]
    assert gates == [("gate-scroll", 1060022)]


def test_without_a_gate_the_first_trip_rides_from_town(qualified, monkeypatch):
    # Ape City sells no TwinCityGate: the saved Conductress ride goes once,
    # and that trip buys the gates for every later one.
    ride = {"source_map": 1020, "destination_map": 1002, "exit_portal": 1, "service": {}}
    monkeypatch.setattr("conquest.gear_circuit.saved_ride", lambda source, destination: ride)
    farmer = Farmer(twin_gates=0)
    farmer.position = [566, 565]
    assert not b.refresh_due(farmer.town("supplies")["items"])
    assert b.bootstrap_due(farmer, farmer.town("supplies")["items"])

    def reach(loop, home):
        loop.calls.append(("ride", home))
        loop.map_id, loop.position = 1002, [555, 957]
        return "ride"

    monkeypatch.setattr("conquest.gear_circuit.reach_twin_city", reach)
    monkeypatch.setattr("conquest.gear_circuit.enter_town",
                        lambda loop: setattr(loop, "position", [430, 381]))
    real = b.visit_buffer
    monkeypatch.setattr(b, "visit_buffer", lambda loop, **kw: real(loop, find=loop.find, **kw))
    assert b.trip(farmer, ride=True) is True
    assert ("ride", 1020) in farmer.calls and ("quiet",) not in farmer.calls
    # GATE_KEEP TwinCityGates bought; the way home read the ApeCityGate.
    assert b.gates_carried(farmer.items) == b.GATE_KEEP
    assert ("gate-scroll", 1060022) in farmer.calls
    assert farmer.map_id == 1020 and b.stigma_left() > 0
    # With gates carried the next trips go by scroll.
    b.write_json(b.STATE, {"stigma_at": None})
    assert b.refresh_due(farmer.town("supplies")["items"])
    assert not b.bootstrap_due(farmer, farmer.town("supplies")["items"])


def test_a_hunt_reached_through_twin_city_fetches_him_on_the_way(qualified, monkeypatch):
    # The Desert (2026-09-30): Ape City -> TwinCityGate -> Twin City's square
    # -> GeneralPeace. desert_gate fetches MrBuffer on that hop, so a restock
    # goes straight home and the buff is fresh on arrival.
    from conquest.routes import RouteLibrary

    library = RouteLibrary()
    assert b.on_the_way(library.load("desert-scout"))
    # Love Canyon is walked into from Ape City: the restock trip fetches him.
    assert not b.on_the_way(library.load("snakeman-canyon"))
    for route in ("macaque", "thunderape-nw", "giantape-west"):
        assert not b.on_the_way(library.load(route)), route
    farmer = Farmer()
    farmer.route = NS(restock_map_id=1020, map_id=1000, supplies=NS(minimum_free_slots=4))
    assert run(farmer, monkeypatch) is False
    assert not [c for c in farmer.calls if c[0] in ("quiet", "gate-scroll", "buy", "travel")]
    assert [f["reason"] for e, f in farmer.events if e == "buff_trip_skipped"] == ["on_the_way"]


def test_find_buffer_reads_a_player_range_actor_by_name(monkeypatch):
    layout = NS(begin_offset=0x8, end_offset=0x10, entry_stride=16, entry_object_offset=8,
                monster_vtable_rva=0x5E12D0, name_offset=0xA4, position_offset=0xE8,
                id_offset=0x78, kind_offset=0x80)
    base = 0x140000000

    def actor(name, uid, tile, vtable=base + 0x5E12D0):
        block = bytearray(0x100)
        struct.pack_into("<Q", block, 0, vtable)
        struct.pack_into("<I", block, 0x78, uid)
        block[0xA4 : 0xA4 + len(name)] = name.encode()
        struct.pack_into("<2I", block, 0xE8, *tile)
        return bytes(block)

    objects = {0x10000: actor("Shopboy", 100165, (424, 350)),
               0x10100: actor("MrBuffer[Bot]", 2146483646, (455, 368)),
               0x10200: actor("Effect", 7, (0, 0), vtable=base + 0x5E8458)}
    table = b"".join(struct.pack("<QQ", 0, a) for a in objects)
    collection = 0x9000

    class Session:
        def read_block(self, address, size):
            if address == collection + layout.begin_offset:
                return struct.pack("<Q", 0x20000)
            if address == collection + layout.end_offset:
                return struct.pack("<Q", 0x20000 + len(table))
            assert address == 0x20000
            return table[:size]

        def read_blocks(self, addresses, size):
            return [objects.get(a) for a in addresses]

    monkeypatch.setattr(
        "conquest.memory_entities.MemoryEntityReader.for_session",
        classmethod(lambda cls, session: NS(layout=layout, _resolve=lambda: (base, collection, None))),
    )
    monkeypatch.setattr("conquest.addressing.checked_address", lambda a, size=0: a)
    assert b.find_buffer(Session()) == (455, 368)
    del objects[0x10100]
    assert b.find_buffer(Session()) is None
