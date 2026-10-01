"""desert-scout: the first Snakeman route, in the Desert (map 1000).

Alex 2026-09-30 18:4x: "scout the Desert now" (Suicide L60; Snakemen L62).
The way in: a TwinCityGate from Ape City, then Twin City's Conductress "Desert
City" (Suicide landed at (69, 473) four times on 2026-09-27), then the road
past GeneralPeace (who only warns, 20:32:57) to the SpaceMark at (96, 323)
(Alex 19:3x: "there is a npc that will bring you to the desert"). Twin City
portal 1 (44, 394), the first guess, leads to the Mine (1028): Suicide,
19:28:43. Restocks read an ApeCityGate home.

Failure modes, written before the change:
1. The route targets anything but the Snakeman family, or native farming
   refuses the family.
2. The anchor, patrol or town connector is off the box or unwalkable on the
   Desert's installed terrain.
3. The saved Twin City <-> Desert links disagree with the installed terrain
   (hashes, NPC and landing tiles), still send the farmer through the Mine's
   portal, or plan a way out of the Desert nobody has crossed.
4. Map travel from Ape City walks the GiantApe plain to Ape Mountain's Twin
   City portal instead of reading the carried TwinCityGate, or walks a portal
   on Twin City's map instead of asking the SpaceMark.
5. A map with no saved way on (the Mine) strands the farmer although a gate
   to a town with one is carried.
"""

from types import SimpleNamespace as NS

import pytest

from conquest.routes import RouteLibrary, route_monster_names
from test_ratling_route import _segment

ROOT = r"C:\Program Files\Classic Conquer 2.0"
SHA = {
    1000: "3d8a7ec5d308f8a3cfd5305768093115274265426ce3295f5bc0c111384d9213",
    1002: "cf76b99e7786b4f580f949b2f4d60e9eb2501e994a777df50d52e543b0b7999c",
    1020: "2feb3cdcf2a5cfe8a3358f15103c9994b83094e319f7d780908da5e85658ca80",
    1028: "a233d65d40668c21f8fa44ce6d0dc72780794dda3a725106ffc2821e08861569",
}


@pytest.fixture(scope="module")
def route():
    return RouteLibrary().load("desert-scout")


@pytest.fixture(scope="module")
def terrains():
    from conquest.navigation import read_terrain

    try:
        return {m: read_terrain(ROOT, m) for m in (1000, 1002, 1028)}
    except (OSError, ValueError) as error:
        pytest.skip(f"installed maps unavailable: {error}")


def test_the_scout_targets_the_snakeman_family(route):
    # 1
    from conquest.trial import TrialConfig

    assert route.map_id == 1000 and route.restock_map_id == 1020
    assert route.monster_type_ids == (13, 72)
    assert route_monster_names(route) == ("Snakeman", "SnakemanL63")
    config = TrialConfig(
        character="Suicide",
        player_profile="player.yaml",
        inventory_profile="inventory.yaml",
        template="template.png",
        client_size=(1416, 876),
        boundary=route.hunting_boundary,
        monster="Snakeman",
    )
    assert config.monster == "Snakeman"


def test_anchor_patrol_and_connector_are_walkable(route, terrains):
    # 2
    terrain = terrains[1000]
    assert terrain.source_sha256 == route.terrain_sha256
    assert route.outbound_waypoints[0] == route.town_anchor
    assert route.outbound_waypoints[-1] == route.hunting_anchor
    assert route.return_waypoints[0] == route.hunting_anchor
    assert route.return_waypoints[-1] == route.town_anchor
    for path in (route.outbound_waypoints, route.return_waypoints):
        for a, b in zip(path, path[1:]):
            assert all(terrain.walkable(t) for t in _segment(a, b)), (a, b)
    left, top, right, bottom = route.hunting_boundary
    for point in (route.hunting_anchor, *route.patrol):
        assert left <= point[0] <= right and top <= point[1] <= bottom
        assert terrain.walkable(point)
        terrain.path(route.hunting_anchor, point)


def test_the_saved_desert_links_match_the_installed_terrain(terrains):
    # 3
    from conquest import desert_gate
    from conquest.conductress import TRIPS
    from conquest.world_travel import connection_path, connections
    import json

    for m, terrain in terrains.items():
        assert terrain.source_sha256 == SHA[m]
    edges = connections()
    [mine] = [e for e in edges if (e["source_map"], e.get("portal_id")) == (1002, 1)]
    assert mine["verified"] is True and mine["destination_map"] == 1028
    assert mine["destination_terrain_sha256"] == SHA[1028]
    assert (44, 394, 1) in terrains[1002].portals
    [gate] = [e for e in edges if e.get("service") == desert_gate.SERVICE]
    assert gate["verified"] is True
    assert (gate["source_map"], gate["destination_map"]) == (1002, 1000)
    assert gate["source_terrain_sha256"] == SHA[1002]
    assert gate["destination_terrain_sha256"] == SHA[1000]
    # Each NPC's approach is open ground within its dialog's 18 tiles (12 for
    # the travel's stop); the ride's landing is open ground on their side of
    # the map, a short road away (the square is >1,000 through the maze).
    tc = terrains[1002]
    assert desert_gate.CANDIDATES and tc.walkable(desert_gate.LANDING)
    names = [name for name, _, _ in desert_gate.CANDIDATES]
    # Alex showed GeneralPeace's "I see." (21:16); the SpaceMark is Water-only.
    assert names[0] == "GeneralPeace" and "SpaceMark" not in names
    for name, tile, approach in desert_gate.CANDIDATES:
        assert tc.walkable(approach), name
        assert max(abs(a - b) for a, b in zip(approach, tile)) <= 12, name
        assert max(abs(a - b) for a, b in zip(desert_gate.LANDING, tile)) <= desert_gate.NEAR_TILES
        assert len(tc.path(desert_gate.LANDING, approach)) < 250, name
        assert len(tc.path((429, 378), approach)) > 1000, name
    # Nobody has crossed the Desert's own portals: no saved way out of it.
    assert not [e for e in edges if e["source_map"] == 1000 and e.get("verified") is True]
    trips = json.loads(TRIPS.read_text(encoding="utf-8"))["trips"]
    desert = [t for t in trips if t["destination_map"] == 1000 and t.get("verified") is True]
    assert len(desert) == 1
    assert desert[0]["source_map"] == 1002 and desert[0]["option"] == "Desert City"
    assert desert[0]["arrival_map"] == 1002 and desert[0]["price"] == 100
    assert tuple(desert[0]["arrival_position"]) == desert_gate.LANDING
    hops = connection_path(1020, 1000)
    assert [(e["source_map"], e["destination_map"]) for e in hops] == [(1020, 1002), (1002, 1000)]
    assert hops[1].get("service") == desert_gate.SERVICE
    with pytest.raises(ValueError):
        connection_path(1000, 1020)


def _fake_terrain(root, map_id):
    return NS(source_sha256=SHA[map_id], map_id=map_id)


def test_map_travel_reads_a_twincitygate_then_asks_general_peace(monkeypatch):
    # 4
    from conquest import city_travel, desert_gate, return_scroll, world_travel

    life = {"map_id": 1020, "position": [554, 545]}
    reads, calls = [], []

    def read_gate(loop, destination):
        reads.append(destination)
        if destination == 1002:
            life.update(map_id=1002, position=[429, 378])
            return True
        return False

    def stop(*args, **kwargs):
        raise AssertionError("walked a portal instead of asking GeneralPeace")

    def peace(loop):
        calls.append(("peace", life["map_id"]))
        life.update(map_id=1000, position=[480, 630])

    monkeypatch.setattr(return_scroll, "read_gate", read_gate)
    monkeypatch.setattr(world_travel, "cross_portal", stop)
    monkeypatch.setattr(world_travel, "read_terrain", _fake_terrain)
    monkeypatch.setattr(desert_gate, "travel", peace)
    monkeypatch.setattr(city_travel, "city_for", lambda map_id: {"map_id": map_id})
    monkeypatch.setattr(
        city_travel,
        "ensure_city_visit",
        lambda loop, new_arrival=False: calls.append(("town", life["map_id"], new_arrival)),
    )
    loop = NS(living=lambda: {"embedded_controls": {"life": dict(life)}})
    world_travel.travel_to_map(loop, 1000)
    # On Twin City's map a Desert gate is looked for again (there is none),
    # then GeneralPeace takes over; Desert City town follows the arrival.
    assert reads == [1000, 1002, 1000]
    assert calls == [("peace", 1002), ("town", 1000, True)]
    assert life["map_id"] == 1000


def test_a_map_with_no_saved_way_on_reads_a_gate_out(monkeypatch):
    # 5
    from conquest import city_travel, desert_gate, return_scroll, world_travel

    monkeypatch.setattr(city_travel, "city_for", lambda map_id: {"map_id": map_id})

    life = {"map_id": 1028, "position": [160, 96]}
    reads = []
    carried = {"gates": True}

    def read_gate(loop, destination):
        reads.append(destination)
        if destination == 1002 and carried["gates"]:
            life.update(map_id=1002, position=[429, 378])
            return True
        return False

    class Rode(Exception):
        pass

    def peace(loop):
        raise Rode

    monkeypatch.setattr(return_scroll, "read_gate", read_gate)
    monkeypatch.setattr(return_scroll, "carried", lambda loop, kind: carried["gates"])
    monkeypatch.setattr(world_travel, "read_terrain", _fake_terrain)
    monkeypatch.setattr(desert_gate, "travel", peace)
    loop = NS(living=lambda: {"embedded_controls": {"life": dict(life)}})
    with pytest.raises(Rode):
        world_travel.travel_to_map(loop, 1000)
    # The Desert gate first (none), then Twin City's, the town fewest saved
    # hops from the Desert, then GeneralPeace from there.
    assert reads == [1000, 1002, 1000]
    life.update(map_id=1028, position=[160, 96])
    reads.clear()
    carried["gates"] = False
    with pytest.raises(ValueError, match="No memory-verified map connection from 1028"):
        world_travel.travel_to_map(loop, 1000)
    assert reads == [1000]


def test_without_a_twincitygate_the_hop_rides_instead_of_walking_the_plain(monkeypatch):
    # 4: Ape City's Conductress lands beside portal 1 (381, 21); walking that
    # portal from town crosses the GiantApe plain (Toxic died there at 47).
    from conquest import city_travel, desert_gate, gear_circuit, return_scroll, world_travel

    life = {"map_id": 1020, "position": [554, 545]}
    calls = []

    def stop(*args, **kwargs):
        raise AssertionError("walked Ape Mountain's portal")

    def ride(loop, home):
        calls.append(("ride", home))
        life.update(map_id=1002, position=[555, 957])
        return "ride"

    class Rode(Exception):
        pass

    def peace(loop):
        calls.append(("peace", life["map_id"]))
        raise Rode

    monkeypatch.setattr(return_scroll, "read_gate", lambda loop, destination: False)
    monkeypatch.setattr(world_travel, "cross_portal", stop)
    monkeypatch.setattr(world_travel, "read_terrain", _fake_terrain)
    monkeypatch.setattr(gear_circuit, "reach_twin_city", ride)
    monkeypatch.setattr(desert_gate, "travel", peace)
    monkeypatch.setattr(city_travel, "city_for", lambda map_id: {"map_id": map_id})
    loop = NS(living=lambda: {"embedded_controls": {"life": dict(life)}})
    with pytest.raises(Rode):
        world_travel.travel_to_map(loop, 1000)
    assert calls == [("ride", 1020), ("peace", 1002)]
