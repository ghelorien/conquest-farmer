"""FireSpirits (levels 42-46) on Phoenix Castle, south of the Ratlings.

2026-09-28: Toxic and Suicide reached level 40 on Ratlings (level 37), whose
XP drops once they are more than five levels below the farmer, so the
FireSpirit route (level 42) is next. It had never been hunted: its route came
from the installed UserHelpInfo.json seed, without jump Scatter or a King
clearance, and it may have Kings of its own.

Failure modes, written before the first run:
1. Level 41 does not move on to FireSpirits.
2. The route targets anything but the FireSpirit family (FireSpirit, L42 and
   FireSpiritL43, L43).
3. The town connector or patrol crosses a blocked tile or a gap longer than a
   movement segment (12 tiles), or does not run town to anchor and back.
4. It hunts differently from the Ratling route before its bosses are known:
   no jump Scatter, a King clearance under 15, or other arrows.
5. The route leaves Phoenix Castle, its restock town or its terrain version.
"""

import pytest

from conquest import leveling_routes
from conquest.routes import RouteLibrary, route_monster_names
from test_ratling_route import _segment


@pytest.fixture(scope="module")
def route():
    return RouteLibrary().load("firespirit")


@pytest.fixture(scope="module")
def terrain():
    from conquest.navigation import read_terrain

    try:
        return read_terrain(r"C:\Program Files\Classic Conquer 2.0", 1011)
    except (OSError, ValueError) as error:
        pytest.skip(f"installed Phoenix Castle map unavailable: {error}")


def test_level_41_moves_on_to_fire_spirits(route):
    # 1
    assert leveling_routes.hunting_level(41) == 42
    for level in (42, 46):
        selected, entry = leveling_routes.desired_route(level)
        assert selected.id == route.id == entry["saved_route"] == "firespirit"


def test_only_the_fire_spirit_family_is_targeted(route):
    # 2. The ElfMessenger, ElfAide and ElfBoss are elites to dodge (Alex
    # 2026-09-30: "dont attack messengers anymore, just dodge them").
    assert route.monster_type_ids == (9, 68)
    assert route_monster_names(route) == ("FireSpirit", "FireSpiritL43")
    for kind in (8104, 8204, 8304):
        with pytest.raises(ValueError):
            route_monster_names(route.model_copy(update={"monster_type_ids": (9, kind)}))


def test_the_hunting_area_covers_the_surveyed_core(route):
    # Toxic's survey (2026-09-28 16:34-16:53): 35-49 FireSpirits a 10x10 cell
    # at x530-550/y780-800 and 14 still at y820, past the seed's y803 edge.
    left, top, right, bottom = route.hunting_boundary
    assert left <= 510 and top <= 760 and right >= 570 and bottom >= 830


def test_town_connector_and_patrol_are_walkable(route, terrain):
    # 3, 5
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


def test_it_hunts_as_the_ratling_route_until_its_bosses_are_known(route):
    # 4
    ratling = RouteLibrary().load("ratling")
    assert route.kite_when_surrounded and route.jump_scatter
    assert route.king_clearance >= ratling.king_clearance == 15
    assert route.attack_range_tiles == ratling.attack_range_tiles
    assert route.supplies == ratling.supplies


def test_the_route_stays_on_phoenix_castle_with_the_ratling_town(route):
    # 5
    ratling = RouteLibrary().load("ratling")
    assert route.map_id == route.restock_map_id == ratling.map_id == 1011
    assert (route.town_anchor, route.restock_anchor) == (
        ratling.town_anchor,
        ratling.restock_anchor,
    )
    assert route.recommended_levels == (42, 46)
