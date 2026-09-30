"""thunderape-west: Toxic's ThunderApe box west of its farmed-down scout box.

Alex 2026-09-30 18:3x: XP/h was ~0.9M. The scout box held 0.1-0.6
ThunderApes a look per 10x10 cell by 17:30, and boss_chase exits (17:48,
18:03, 18:21) took Toxic off it three times. West of it, where nobody has
hunted since 11:4x, cells at x 220-229 held 3.2-16.7 a look (17:22-18:15).

Failure modes, written before the change:
1. The box targets anything but the ThunderApe family, or a messenger
   (Alex 2026-09-30: dodge them).
2. The anchor or a patrol point is off the box or unwalkable, or the
   documented road does not run town to anchor and back over walkable tiles.
3. The box overlaps the scout box (or, expanded, meets its expansion), or
   Suicide's boxes, or its anchor or a patrol point lies within a King's
   clearance of a tile where a ThunderApeKing was logged on 2026-09-30.
"""

import pytest

from conquest.routes import RouteLibrary, route_monster_names
from test_ratling_route import _segment
from test_thunderape_scout_nw import SUICIDE_BOX, gap

# ThunderApeKing tiles logged in or beside the box on 2026-09-30 (11:21-18:10):
# the morning King's idle spot, the one that walked (243,225-234) at 12:02,
# and the south-west King's (246-265,250-270).
KINGS = [
    (228, 189), (234, 186), (228, 186), (231, 183), (225, 186), (228, 183),
    (222, 180), (222, 183), (225, 183), (216, 177), (219, 174), (219, 270),
    (216, 267), (213, 267), (246, 258), (246, 264), (249, 258), (249, 261),
    (243, 225), (243, 228), (243, 231), (243, 234), (251, 259), (259, 258),
]


@pytest.fixture(scope="module")
def route():
    return RouteLibrary().load("thunderape-west")


@pytest.fixture(scope="module")
def terrain():
    from conquest.navigation import read_terrain

    try:
        return read_terrain(r"C:\Program Files\Classic Conquer 2.0", 1020)
    except (OSError, ValueError) as error:
        pytest.skip(f"installed Ape Mountain map unavailable: {error}")


def test_the_box_targets_only_the_thunderape_family(route):
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


def test_the_box_keeps_off_the_other_boxes_and_its_spots_off_the_kings(route):
    # 3
    library = RouteLibrary()
    grow = route.patrol_search.expansion_tiles * route.patrol_search.maximum_expansions
    left, top, right, bottom = route.hunting_boundary
    grown = (left - grow, top - grow, right + grow, bottom + grow)
    for other_id in ("thunderape-scout", "thunderape-strip"):
        other = library.load(other_id)
        extra = other.patrol_search.expansion_tiles * other.patrol_search.maximum_expansions
        o_left, o_top, o_right, o_bottom = other.hunting_boundary
        other_grown = (o_left - extra, o_top - extra, o_right + extra, o_bottom + extra)
        # Even expanded, the boxes do not meet.
        assert grown[2] < other_grown[0] or grown[3] < other_grown[1], other_id
    s_left, s_top, _, _ = SUICIDE_BOX
    assert right < s_left or bottom < s_top
    for point in (route.hunting_anchor, *route.patrol):
        for king in KINGS:
            spot = (king[0], king[1], king[0], king[1])
            assert gap(point, spot) > route.king_clearance, (point, king)
