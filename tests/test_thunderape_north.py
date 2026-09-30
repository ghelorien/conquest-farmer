"""thunderape-north: the rested partner of Toxic's thunderape-scout box.

Buffed, a ThunderApe box empties in 20-30 minutes and respawns slowly, so
Toxic rotates two boxes, one per town visit (session_plan "rotation"). This
ground north of the scout box was never in view before 2026-09-30 14:0x.

Failure modes, written before the change:
1. It targets anything but the ThunderApe family, or a messenger.
2. The anchor or a patrol point is off the box or unwalkable, or the road
   does not run town to anchor and back over walkable tiles.
3. It overlaps Suicide's thunderape-nw or Toxic's thunderape-scout box, so
   the rotation's rest would not be a rest, or its anchor or a patrol point
   lies within a King's clearance of a spot a ThunderApeKing was logged on.
4. It cannot share a rotation with thunderape-scout (another restock town).
"""

import pytest

from conquest.routes import RouteLibrary, route_monster_names
from test_ratling_route import _segment
from test_thunderape_scout_nw import KINGS, SUICIDE_BOX, gap


@pytest.fixture(scope="module")
def route():
    return RouteLibrary().load("thunderape-north")


@pytest.fixture(scope="module")
def terrain():
    from conquest.navigation import read_terrain

    try:
        return read_terrain(r"C:\Program Files\Classic Conquer 2.0", 1020)
    except (OSError, ValueError) as error:
        pytest.skip(f"installed Ape Mountain map unavailable: {error}")


def test_it_targets_only_the_thunderape_family(route):
    # 1
    assert route.monster_type_ids == (12, 71)
    assert route_monster_names(route) == ("ThunderApe", "ThunderApeL58")


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


def test_it_rests_apart_from_the_other_boxes_and_off_the_kings(route):
    # 3
    left, top, right, bottom = route.hunting_boundary
    scout = RouteLibrary().load("thunderape-scout").hunting_boundary
    for other in (SUICIDE_BOX, scout):
        o_left, o_top, o_right, o_bottom = other
        assert right < o_left or o_right < left or bottom < o_top or o_bottom < top, other
    for point in (route.hunting_anchor, *route.patrol):
        for king in KINGS + [(336, 213), (340, 182)]:
            assert gap(point, (*king, *king)) > route.king_clearance, (point, king)


def test_it_rotates_with_the_scout_box(route):
    # 4
    scout = RouteLibrary().load("thunderape-scout")
    assert route.restock_map_id == scout.restock_map_id == route.map_id == 1020
