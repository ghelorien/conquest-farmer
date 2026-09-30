"""thunderape-nw: a planned scout of Ape Mountain's unseen north-west.

Nobody has seen where ThunderApes (L57-58, types 12/71) spawn, and Suicide
reaches 57 on 2026-09-29's evening. The north-west region (x ~72-368,
y ~40-340) is the nearest unseen ground: 237-276 tiles from the GiantApe
plain through the passage at (360-420, 312-318).

Failure modes, written before the change:
1. The scout targets anything but the ThunderApe family.
2. The anchor or a patrol point is off its box or unwalkable, or the connector
   does not run town to anchor and back over walkable tiles.
3. The only safe way in, a field switch from giantape-south's anchor, passes
   inside a surveyed GiantApeKing's or the Aide's clearance.
"""

import pytest

from conquest.routes import RouteLibrary, route_monster_names
from test_ratling_route import _segment

KINGS = [(601, 297, 603, 302), (543, 302, 546, 304), (570, 360, 571, 367)]
AIDE = (555, 255, 558, 257)


def gap(point, box):
    left, top, right, bottom = box
    x, y = point
    return max(max(left - x, 0, x - right), max(top - y, 0, y - bottom))


@pytest.fixture(scope="module")
def route():
    return RouteLibrary().load("thunderape-nw")


@pytest.fixture(scope="module")
def terrain():
    from conquest.navigation import read_terrain

    try:
        return read_terrain(r"C:\Program Files\Classic Conquer 2.0", 1020)
    except (OSError, ValueError) as error:
        pytest.skip(f"installed Ape Mountain map unavailable: {error}")


def test_the_scout_targets_the_thunderape_family(route):
    # 1
    assert route.monster_type_ids == (12, 71, 8107)
    assert route_monster_names(route) == ("ThunderApe", "ThunderApeL58", "ThunderApeMsgr")
    assert route.qualification == "planned"


def test_anchor_patrol_and_connector_are_walkable(route, terrain):
    # 2
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


def test_the_switch_from_giantape_south_stays_clear_of_the_plain_bosses(route, terrain):
    # 3
    south = RouteLibrary().load("giantape-south")
    for tile in terrain.travel_path(south.hunting_anchor, route.hunting_anchor):
        assert all(gap(tile, king) > route.king_clearance for king in KINGS), tile
        assert gap(tile, AIDE) > route.elite_clearance, tile
