"""thunderape-scout: Toxic's supervised ThunderApe scout beyond Suicide's box.

Alex 2026-09-30 10:2x approved a scout for Toxic's own ThunderApe ground, so
it does not share Suicide's thunderape-nw box [300,270,348,318]. The ground
north-west of that box's ring of bosses has never been seen from inside.

Failure modes, written before the change:
1. The scout targets anything but the ThunderApe family, or a messenger
   (Alex 2026-09-30: dodge them).
2. The anchor or a patrol point is off the box or unwalkable, or the
   documented road does not run town to anchor and back over walkable tiles.
3. The box overlaps Suicide's box, or lies within a boss's clearance of a
   spot where Laptop2 logged a ThunderApe boss on 2026-09-30.
"""

import pytest

from conquest.routes import RouteLibrary, route_monster_names
from test_ratling_route import _segment

SUICIDE_BOX = (300, 270, 348, 318)
KINGS = [(293, 333), (303, 287), (336, 213), (331, 256)]
AIDES = [(312, 333), (284, 279), (304, 259), (281, 327), (344, 308)]
MSGRS = [(297, 333), (289, 296), (280, 267), (279, 332), (329, 336)]


def gap(point, box):
    left, top, right, bottom = box
    x, y = point
    return max(max(left - x, 0, x - right), max(top - y, 0, y - bottom))


@pytest.fixture(scope="module")
def route():
    return RouteLibrary().load("thunderape-scout")


@pytest.fixture(scope="module")
def terrain():
    from conquest.navigation import read_terrain

    try:
        return read_terrain(r"C:\Program Files\Classic Conquer 2.0", 1020)
    except (OSError, ValueError) as error:
        pytest.skip(f"installed Ape Mountain map unavailable: {error}")


def test_the_scout_targets_only_the_thunderape_family(route):
    # 1
    assert route.monster_type_ids == (12, 71)
    assert route_monster_names(route) == ("ThunderApe", "ThunderApeL58")
    assert route.qualification == "planned"
    assert route.restock_map_id == route.map_id == 1020


def test_anchor_patrol_and_road_are_walkable(route, terrain):
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


def test_the_box_stays_off_suicides_box_and_todays_boss_spots(route):
    # 3
    left, top, right, bottom = route.hunting_boundary
    corners = [(left, top), (right, top), (left, bottom), (right, bottom)]
    s_left, s_top, s_right, s_bottom = SUICIDE_BOX
    assert right < s_left or bottom < s_top  # no overlap with Suicide's box
    for king in KINGS:
        assert gap(king, route.hunting_boundary) > route.king_clearance, king
    for elite in AIDES + MSGRS:
        assert gap(elite, route.hunting_boundary) > route.elite_clearance, elite
    assert corners  # the box is a real rectangle
