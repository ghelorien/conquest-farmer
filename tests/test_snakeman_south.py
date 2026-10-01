"""snakeman-south: Toxic's Snakeman box in Love Canyon, south of Suicide's.

Alex 2026-09-30 21:4x walked Toxic (L61) into Love Canyon by hand ("I brought
you near the snakemen"). The client's help file puts Snakemen (L62/63) at Love
Canyon (232,461), Ape Mountain's walled south-west. Suicide's snakeman-canyon
holds [210,440,256,484] round that spot (Laptop2).

Failure modes, written before the change:
1. The box targets anything but the Snakeman family.
2. The anchor or a patrol point is off the box or unwalkable, or the
   documented road does not run town to anchor and back over walkable tiles.
3. The box, even expanded, meets Suicide's snakeman-canyon box expanded, or
   any ThunderApe box.
"""

import pytest

from conquest.routes import RouteLibrary, route_monster_names
from test_ratling_route import _segment

# Suicide's snakeman-canyon (Laptop2, 2026-09-30 21:5x): box and expansion.
SUICIDE_CANYON = (210, 440, 256, 484)
SUICIDE_GROW = 4


@pytest.fixture(scope="module")
def route():
    return RouteLibrary().load("snakeman-south")


@pytest.fixture(scope="module")
def terrain():
    from conquest.navigation import read_terrain

    try:
        return read_terrain(r"C:\Program Files\Classic Conquer 2.0", 1020)
    except (OSError, ValueError) as error:
        pytest.skip(f"installed Ape Mountain map unavailable: {error}")


def test_the_box_targets_only_the_snakeman_family(route):
    # 1
    assert route.monster_type_ids == (13, 72)
    assert route_monster_names(route) == ("Snakeman", "SnakemanL63")
    assert route.qualification == "planned"
    assert route.restock_map_id == route.map_id == 1020
    # Walled in like snakeman-canyon: restocks gate home, never walk out.
    assert route.entry.region == (0, 372, 506, 931) and route.entry.map_id is None


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


def test_the_box_keeps_off_suicides_box_and_the_thunderape_boxes(route):
    # 3
    grow = route.patrol_search.expansion_tiles * route.patrol_search.maximum_expansions
    left, top, right, bottom = route.hunting_boundary
    grown = (left - grow, top - grow, right + grow, bottom + grow)
    s_left, s_top, s_right, s_bottom = SUICIDE_CANYON
    suicide = (s_left - SUICIDE_GROW, s_top - SUICIDE_GROW, s_right + SUICIDE_GROW, s_bottom + SUICIDE_GROW)
    assert suicide[3] < grown[1]  # Suicide's box, expanded, ends north of ours
    library = RouteLibrary()
    for other_id in ("thunderape-west", "thunderape-scout", "thunderape-strip"):
        o_left, o_top, o_right, o_bottom = library.load(other_id).hunting_boundary
        assert o_bottom < grown[1] or o_left > grown[2], other_id
