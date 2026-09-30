"""thunderape-scout: Toxic's supervised ThunderApe scout beyond Suicide's box.

Alex 2026-09-30 10:2x approved a scout for Toxic's own ThunderApe ground, so
it does not share Suicide's thunderape-nw box [300,242,348,318]. The ground
north-west of that box's ring of bosses has never been seen from inside.

Failure modes, written before the change:
1. The scout targets anything but the ThunderApe family, or a messenger
   (Alex 2026-09-30: dodge them).
2. The anchor or a patrol point is off the box or unwalkable, or the
   documented road does not run town to anchor and back over walkable tiles.
3. The box overlaps Suicide's box, or its anchor or a patrol point lies within
   a King's clearance of a spot where a ThunderApeKing was logged on
   2026-09-30. Aides and Msgrs crossed most of the ground in two hours (814
   boss track points), so the box itself may hold their roaming ground: the
   trial's live boss checks keep the farmer off them.
"""

import pytest

from conquest.routes import RouteLibrary, route_monster_names
from test_ratling_route import _segment

SUICIDE_BOX = (300, 242, 348, 318)  # north to y 242 since 2026-09-30 13:4x
# Laptop2's logs round Suicide's box, plus the first scout's own (11:21-11:36):
# a King idling at (228-234, 185-189) and an Aide at (239-240, 221-222).
KINGS = [(293, 333), (303, 287), (336, 213), (331, 256), (228, 189), (234, 186), (331, 208)]
AIDES = [(312, 333), (284, 279), (304, 259), (281, 327), (344, 308), (239, 222), (240, 221)]
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


def test_the_box_stays_off_suicides_box_and_its_spots_off_the_kings(route):
    # 3
    left, top, right, bottom = route.hunting_boundary
    s_left, s_top, s_right, s_bottom = SUICIDE_BOX
    assert right < s_left or bottom < s_top  # no overlap with Suicide's box
    assert left < right and top < bottom
    for point in (route.hunting_anchor, *route.patrol):
        for king in KINGS + [(336, 213)]:
            spot = (king[0], king[1], king[0], king[1])
            assert gap(point, spot) > route.king_clearance, (point, king)
