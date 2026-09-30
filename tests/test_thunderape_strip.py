"""thunderape-strip: thunderape-nw's north strip while a King holds its middle.

2026-09-30 afternoon: ThunderApeKing 402180 settled in thunderape-nw's middle
((317-330, 298-302)), and every nw hunt since 16:45 ended in a boss_chase
return within ~5 minutes (17:32: Suicide pinned at the east edge (340-346,
302) with 10 boss escapes in 2 minutes).

Failure modes, written before the change:
1. The strip targets anything but the ThunderApe family.
2. The anchor or a patrol point is off the strip or unwalkable, or the
   connector from nw's road does not run over walkable tiles.
3. The strip reaches within the King's clearance plus BOSS_ROOM's margin of
   his spot, or loses the gap to Toxic's thunderape-scout box.
"""

import pytest

from conquest.routes import BOSS_ROOM, BOSS_CLEARANCE, RouteLibrary, route_monster_names
from test_ratling_route import _segment

KING_SPOT = (317, 298, 330, 302)
SCOUT_BOX = (254, 160, 345, 235)


def gap(point, box):
    left, top, right, bottom = box
    x, y = point
    return max(max(left - x, 0, x - right), max(top - y, 0, y - bottom))


@pytest.fixture(scope="module")
def route():
    return RouteLibrary().load("thunderape-strip")


@pytest.fixture(scope="module")
def terrain():
    from conquest.navigation import read_terrain

    try:
        return read_terrain(r"C:\Program Files\Classic Conquer 2.0", 1020)
    except (OSError, ValueError) as error:
        pytest.skip(f"installed Ape Mountain map unavailable: {error}")


def test_the_strip_targets_the_thunderape_family(route):
    # 1
    assert route.monster_type_ids == (12, 71)
    assert route_monster_names(route) == ("ThunderApe", "ThunderApeL58")


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


def test_the_strip_keeps_off_the_king_and_the_scout_box(route):
    # 3
    nw = RouteLibrary().load("thunderape-nw")
    left, top, right, bottom = route.hunting_boundary
    n_left, n_top, n_right, n_bottom = nw.hunting_boundary
    assert n_left <= left and n_top <= top and right <= n_right and bottom <= n_bottom
    margin = route.patrol_search.expansion_tiles * route.patrol_search.maximum_expansions
    reach = route.king_clearance + BOSS_ROOM - BOSS_CLEARANCE
    corners = [(x, y) for x in (left, right) for y in (bottom + margin,)]
    spot_top = KING_SPOT[1]
    assert spot_top - (bottom + margin) > reach, (bottom, margin, reach)
    assert all(gap(c, KING_SPOT) > reach for c in corners)
    assert top - SCOUT_BOX[3] >= 7  # Toxic's box keeps its gap
