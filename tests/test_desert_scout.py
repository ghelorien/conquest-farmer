"""desert-scout: the first Snakeman route, in the Desert (map 1000).

Alex 2026-09-30 18:4x: "scout the Desert now" (Suicide L60; Snakemen L62).
The way in is saved, not guessed: a TwinCityGate from Ape City, then Twin City's
Conductress "Desert City" (Suicide landed at (69, 473) four times on
2026-09-27), then Twin City portal 1 (44, 394) to the Desert's east portal
(977, 668). Restocks read an ApeCityGate home.

Failure modes, written before the change:
1. The route targets anything but the Snakeman family, or native farming
   refuses the family.
2. The anchor, patrol or town connector is off the box or unwalkable on the
   Desert's installed terrain.
3. The saved Twin City <-> Desert links disagree with the installed terrain
   (hashes, portal tiles) or need an unverified Conductress trip.
4. Map travel from Ape City walks the GiantApe plain to Ape Mountain's Twin
   City portal instead of reading the carried TwinCityGate.
"""

from types import SimpleNamespace as NS

import pytest

from conquest.routes import RouteLibrary, route_monster_names
from test_ratling_route import _segment

ROOT = r"C:\Program Files\Classic Conquer 2.0"


@pytest.fixture(scope="module")
def route():
    return RouteLibrary().load("desert-scout")


@pytest.fixture(scope="module")
def terrains():
    from conquest.navigation import read_terrain

    try:
        return {m: read_terrain(ROOT, m) for m in (1000, 1002)}
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
    from conquest.conductress import TRIPS
    from conquest.world_travel import connection_path, connections
    import json

    edges = {(e["source_map"], e["portal_id"]): e for e in connections()}
    for (source, portal), target in (((1002, 1), 1000), ((1000, 1), 1002)):
        edge = edges[(source, portal)]
        assert edge["verified"] is True and edge["destination_map"] == target
        assert edge["source_terrain_sha256"] == terrains[source].source_sha256
        assert edge["destination_terrain_sha256"] == terrains[target].source_sha256
        assert tuple(edge["portal_position"]) + (portal,) in terrains[source].portals
    trips = json.loads(TRIPS.read_text(encoding="utf-8"))["trips"]
    desert = [t for t in trips if t["destination_map"] == 1000 and t.get("verified") is True]
    assert len(desert) == 1
    assert desert[0]["source_map"] == 1002 and desert[0]["option"] == "Desert City"
    assert desert[0]["arrival_map"] == 1002 and desert[0]["price"] == 100
    assert terrains[1002].walkable(tuple(desert[0]["arrival_position"]))
    hops = connection_path(1020, 1000)
    assert [(e["source_map"], e["destination_map"]) for e in hops] == [(1020, 1002), (1002, 1000)]


def test_map_travel_reads_a_twincitygate_for_a_hop_through_twin_city(monkeypatch):
    # 4
    from conquest import return_scroll, world_travel

    life = {"map_id": 1020, "position": [554, 545]}
    reads = []

    def read_gate(loop, destination):
        reads.append(destination)
        if destination == 1002:
            life.update(map_id=1002, position=[429, 378])
            return True
        return False

    def stop(*args, **kwargs):
        raise AssertionError("walked or rode instead of reading the gate")

    monkeypatch.setattr(return_scroll, "read_gate", read_gate)
    monkeypatch.setattr(world_travel, "cross_portal", stop)
    loop = NS(living=lambda: {"embedded_controls": {"life": dict(life)}})
    # Stop after the hop: Twin City's next leg is the Conductress's business.
    monkeypatch.setattr(world_travel, "read_terrain", lambda root, map_id: stop())
    with pytest.raises(AssertionError, match="walked or rode"):
        world_travel.travel_to_map(loop, 1000)
    # On Twin City's map a Desert gate is looked for again (there is none),
    # then the Conductress leg plans on Twin City's terrain.
    assert reads == [1000, 1002, 1000] and life["map_id"] == 1002
