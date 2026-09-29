"""giantape-west: Suicide's own strip of the GiantApe plain north of Ape City.

2026-09-29 17:38-17:57: on Alex's new gear both farmers hunted the shared
giantape box. Suicide made 2.6M xp/h for 3 minutes with no damage, then ~1.0M
as both thinned the same ground. Alex: "Make sure the giant ape has
maximized exp per hour route". Suicide's survey (17:46-17:52, 174 samples)
put the densest GiantApe cells at x 570-590, y 280-325, between two nearly
stationary GiantApeKings; Toxic takes giantape-north [575, 240, 625, 284].

Failure modes, written before the change:
1. The strip reaches into a surveyed King's or the Aide's clearance.
2. It misses the survey's dense cells, or overlaps giantape-north once both
   patrol expansions have grown.
3. A patrol point or the anchor is off the strip or unwalkable, or the town
   connector does not run town to anchor and back over walkable tiles, or it
   passes inside a surveyed boss's clearance.
4. It hunts differently from giantape (targets, clearances, jump Scatter,
   supplies and healing).
5. The shared giantape route changes under Toxic.
"""

import pytest

from conquest.routes import RouteLibrary
from test_ratling_route import _segment

KINGS = [(601, 297, 603, 302), (543, 302, 546, 304), (570, 360, 571, 367)]
AIDE = (555, 255, 558, 257)
DENSE = [(580, 310), (570, 290), (580, 300), (580, 290), (580, 320)]
NORTH = (575, 240, 625, 284)


def gap(box, spot):
    """Chebyshev distance from a point to a sighting box."""
    left, top, right, bottom = spot
    x, y = box
    return max(max(left - x, 0, x - right), max(top - y, 0, y - bottom))


@pytest.fixture(scope="module")
def routes():
    library = RouteLibrary()
    return {name: library.load(name) for name in ("giantape", "giantape-west")}


@pytest.fixture(scope="module")
def terrain():
    from conquest.navigation import read_terrain

    try:
        return read_terrain(r"C:\Program Files\Classic Conquer 2.0", 1020)
    except (OSError, ValueError) as error:
        pytest.skip(f"installed Ape Mountain map unavailable: {error}")


def cells(boundary):
    left, top, right, bottom = boundary
    return [(x, y) for x in range(left, right + 1) for y in range(top, bottom + 1)]


def test_the_strip_stays_outside_every_surveyed_clearance(routes):
    # 1
    route = routes["giantape-west"]
    for point in cells(route.hunting_boundary):
        for king in KINGS:
            assert gap(point, king) > route.king_clearance, (point, king)
        assert gap(point, AIDE) > route.elite_clearance, point


def test_the_strip_covers_the_dense_cells_and_meets_the_north_box(routes):
    # 2
    route = routes["giantape-west"]
    left, top, right, bottom = route.hunting_boundary
    for x, y in DENSE:
        assert left <= x + 9 and x <= right and top <= y + 9 and y <= bottom, (x, y)
    grow = route.patrol_search.expansion_tiles * route.patrol_search.maximum_expansions
    assert (top - grow) - (NORTH[3] + 1) >= -2  # at most 2 rows shared, as the snake halves


def test_town_connector_and_patrol_are_walkable_and_clear_of_bosses(routes, terrain):
    # 3
    route = routes["giantape-west"]
    assert terrain.source_sha256 == route.terrain_sha256
    assert route.outbound_waypoints[0] == route.town_anchor
    assert route.outbound_waypoints[-1] == route.hunting_anchor
    assert route.return_waypoints[0] == route.hunting_anchor
    assert route.return_waypoints[-1] == route.town_anchor
    for path in (route.outbound_waypoints, route.return_waypoints):
        assert all(terrain.walkable(p) for p in path)
        for a, b in zip(path, path[1:]):
            assert all(terrain.walkable(t) for t in _segment(a, b)), (a, b)
            for tile in _segment(a, b):
                assert all(gap(tile, king) > route.king_clearance for king in KINGS), tile
    left, top, right, bottom = route.hunting_boundary
    for point in (route.hunting_anchor, *route.patrol):
        assert left <= point[0] <= right and top <= point[1] <= bottom
        assert terrain.walkable(point)
        terrain.path(route.hunting_anchor, point)


def test_the_strip_hunts_like_giantape(routes):
    # 4
    base, route = routes["giantape"], routes["giantape-west"]
    for field in (
        "map_id",
        "restock_map_id",
        "monster_type_ids",
        "recommended_levels",
        "town_anchor",
        "restock_anchor",
        "kite_when_surrounded",
        "jump_scatter",
        "attack_range_tiles",
        "king_clearance",
        "elite_clearance",
        "movement",
        "supplies",
        "tasks",
        "terrain_sha256",
    ):
        assert getattr(route, field) == getattr(base, field), field


def test_the_shared_route_is_unchanged(routes):
    # 5
    route = routes["giantape"]
    assert route.hunting_boundary == (580, 285, 632, 332)
    assert route.hunting_anchor == (605, 305)
