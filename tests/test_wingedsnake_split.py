"""The WingedSnake herd split east/west for two farmers.

2026-09-29 ~03:50: after Defender's quarantine both farmers came back onto the
old wingedsnake route and nearly stopped killing (Suicide 6/min, Toxic ~10/min
against 18.7 and 25 before). Alex: "the patrol is just not going to the
highest mass of monsters". Toxic's survey (85 minutes, 5,319 cell sightings)
put the snakes at x310-395, y70-126; 48% stood outside the old boundary
(282,42-352,112), whose north third and west strip held none.

Failure modes, written before the change:
1. The halves overlap, leave a gap, or miss the surveyed herd.
2. The WingedSnakeKing's box (317-348 x 97-114) is not wholly in the west half.
3. A patrol point or the anchor is off the hunting area or unwalkable, or the
   town connector does not run town to anchor and back over walkable tiles.
4. The halves hunt differently from wingedsnake (targets, clearances, jump
   Scatter), or drop IronArrows (Alex: "Keep the iron arrows").
5. The old wingedsnake route changes (the level bracket 27-31 still uses it).
"""

import pytest

from conquest.routes import RouteLibrary
from test_ratling_route import _segment

HERD = (310, 70, 395, 126)
KING_BOX = (317, 97, 348, 114)


@pytest.fixture(scope="module")
def routes():
    library = RouteLibrary()
    return {
        name: library.load(name)
        for name in ("wingedsnake", "wingedsnake-west", "wingedsnake-east")
    }


@pytest.fixture(scope="module")
def terrain():
    from conquest.navigation import read_terrain

    try:
        return read_terrain(r"C:\Program Files\Classic Conquer 2.0", 1011)
    except (OSError, ValueError) as error:
        pytest.skip(f"installed Phoenix Castle map unavailable: {error}")


def test_the_halves_meet_at_one_edge_and_cover_the_herd(routes):
    # 1
    west = routes["wingedsnake-west"].hunting_boundary
    east = routes["wingedsnake-east"].hunting_boundary
    assert west[2] == east[0]
    assert (west[1], west[3]) == (east[1], east[3])
    assert west[0] <= HERD[0] and east[2] >= HERD[2]
    assert west[1] <= HERD[1] and west[3] >= HERD[3]


def test_the_king_box_lies_in_the_west_half(routes):
    # 2
    left, top, right, bottom = routes["wingedsnake-west"].hunting_boundary
    assert left <= KING_BOX[0] and top <= KING_BOX[1]
    assert right >= KING_BOX[2] and bottom >= KING_BOX[3]
    east = routes["wingedsnake-east"].hunting_boundary
    assert east[0] > KING_BOX[2]


@pytest.mark.parametrize("name", ["wingedsnake-west", "wingedsnake-east"])
def test_town_connector_and_patrol_are_walkable(routes, terrain, name):
    # 3
    route = routes[name]
    assert terrain.source_sha256 == route.terrain_sha256
    assert route.outbound_waypoints[0] == route.town_anchor
    assert route.outbound_waypoints[-1] == route.hunting_anchor
    assert route.return_waypoints[0] == route.hunting_anchor
    assert route.return_waypoints[-1] == route.town_anchor
    for path in (route.outbound_waypoints, route.return_waypoints):
        assert all(terrain.walkable(p) for p in path)
        for a, b in zip(path, path[1:]):
            assert all(terrain.walkable(t) for t in _segment(a, b)), (a, b)
    left, top, right, bottom = route.hunting_boundary
    for point in (route.hunting_anchor, *route.patrol):
        assert left <= point[0] <= right and top <= point[1] <= bottom
        assert terrain.walkable(point)
        terrain.path(route.hunting_anchor, point)


@pytest.mark.parametrize("name", ["wingedsnake-west", "wingedsnake-east"])
def test_the_halves_hunt_like_wingedsnake_with_iron_arrows(routes, name):
    # 4
    base, route = routes["wingedsnake"], routes[name]
    for field in (
        "map_id",
        "restock_map_id",
        "monster_type_ids",
        "town_anchor",
        "restock_anchor",
        "kite_when_surrounded",
        "jump_scatter",
        "attack_range_tiles",
        "king_clearance",
        "elite_clearance",
        "patrol_search",
        "movement",
    ):
        assert getattr(route, field) == getattr(base, field), field
    assert route.supplies.arrow_type == 1050001


def test_the_old_route_is_unchanged(routes):
    # 5
    route = routes["wingedsnake"]
    assert route.hunting_boundary == (282, 42, 352, 112)
    assert route.hunting_anchor == (317, 77)
