"""snakeman-bold: Toxic's Snakeman box over the canyon's boss packs.

Alex 2026-10-01 05:2x: "can toxic be more bold try to get at least 2m xp per
hour find bigger packs". The Snakemen pack round their bosses; snakeman-south
kept 13-15 tiles from them and ended each hunt after 10 boss escapes.

Failure modes, written before the change:
1. The box targets anything but the Snakeman family, or loses the canyon's
   walled-region gate home.
2. The bold settings drift: elites must keep the minimum clearance (9),
   Kings 12, and the hunt ends only on 25 boss escapes.
3. The anchor or a patrol point is unwalkable or off the box, the road does
   not run town to anchor and back, or a spot lies within 13 tiles of a
   SnakemanKing tile logged on 2026-10-01.
"""

import pytest

from conquest.routes import BOSS_CLEARANCE, RouteLibrary, route_monster_names
from test_ratling_route import _segment

KINGS = [
    (249, 579), (282, 489), (249, 576), (288, 528), (246, 579), (279, 549),
    (237, 534), (282, 549), (288, 525), (285, 528), (288, 549), (282, 546),
    (288, 522), (285, 549), (279, 546), (246, 576), (285, 525), (291, 525),
    (237, 531), (282, 561),
]


@pytest.fixture(scope="module")
def route():
    return RouteLibrary().load("snakeman-bold")


@pytest.fixture(scope="module")
def terrain():
    from conquest.navigation import read_terrain

    try:
        return read_terrain(r"C:\Program Files\Classic Conquer 2.0", 1020)
    except (OSError, ValueError) as error:
        pytest.skip(f"installed Ape Mountain map unavailable: {error}")


def test_the_box_targets_the_snakemen_inside_the_canyon(route):
    # 1
    assert route.monster_type_ids == (13, 72)
    assert route_monster_names(route) == ("Snakeman", "SnakemanL63")
    assert route.restock_map_id == route.map_id == 1020
    assert route.entry.region == (0, 372, 506, 931) and route.entry.map_id is None


def test_the_bold_settings(route):
    # 2
    assert route.elite_clearance == BOSS_CLEARANCE == 9
    assert route.king_clearance == 12
    assert route.boss_chase_escapes == 25


def test_anchor_patrol_and_road(route, terrain):
    # 3
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
        assert all(max(abs(point[0] - k[0]), abs(point[1] - k[1])) > 13 for k in KINGS), point
