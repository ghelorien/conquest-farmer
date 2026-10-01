"""bandit-east-half: Toxic's half of the dense Bandit ground.

Alex 2026-10-01 06:3x: "go farm bandits with suicide", goal "minimum of 100
kpm for each over the span of an hour". Laptop2 split the ground Suicide
measured at 99-141 kills/min a cell at x = 421: Toxic east, Suicide west
(bandit-west-half). bandit-southern-fields, south of it, saw ~1 Bandit a look.

Failure modes, written before the change:
1. The box targets anything but the Bandit family, restocks outside
   Phoenix, or reaches west of x 421 (Suicide's half), even expanded.
2. The anchor or a patrol point is unwalkable, off the box, unreachable
   from the anchor, or within 12 tiles of a BanditKing's parked spot.
3. The road does not run town to anchor and back over walkable tiles.
"""

import pytest

from conquest.routes import RouteLibrary, route_monster_names
from test_ratling_route import _segment

SPLIT_X = 421
KINGS = [(399, 353), (443, 338)]


@pytest.fixture(scope="module")
def route():
    return RouteLibrary().load("bandit-east-half")


@pytest.fixture(scope="module")
def terrain():
    from conquest.navigation import read_terrain

    try:
        return read_terrain(r"C:\Program Files\Classic Conquer 2.0", 1011)
    except (OSError, ValueError) as error:
        pytest.skip(f"installed Phoenix map unavailable: {error}")


def test_the_east_half_of_the_bandit_ground(route):
    # 1
    assert route.monster_type_ids == (7, 66)
    assert route_monster_names(route)[0] == "Bandit"
    assert route.map_id == route.restock_map_id == 1011
    assert route.hunting_boundary == (SPLIT_X, 337, 513, 525)
    search = route.patrol_search
    assert SPLIT_X - search.expansion_tiles * search.maximum_expansions >= SPLIT_X - 2


def test_anchor_and_patrol(route, terrain):
    # 2
    assert terrain.source_sha256 == route.terrain_sha256
    left, top, right, bottom = route.hunting_boundary
    for point in (route.hunting_anchor, *route.patrol):
        assert left <= point[0] <= right and top <= point[1] <= bottom, point
        assert terrain.walkable(point), point
        terrain.path(route.hunting_anchor, point)
        assert all(max(abs(point[0] - k[0]), abs(point[1] - k[1])) > 12 for k in KINGS), point


def test_the_road(route, terrain):
    # 3
    assert route.outbound_waypoints[0] == route.town_anchor
    assert route.outbound_waypoints[-1] == route.hunting_anchor
    assert route.return_waypoints[0] == route.hunting_anchor
    assert route.return_waypoints[-1] == route.town_anchor
    for path in (route.outbound_waypoints, route.return_waypoints):
        for a, b in zip(path, path[1:]):
            assert all(terrain.walkable(t) for t in _segment(a, b)), (a, b)
