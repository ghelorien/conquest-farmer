"""Ratlings (levels 37-41) on Phoenix Castle, east of the Bandit fields.

Live 2026-09-27: the Ratling bracket had no saved route ("needs_survey"), so
a farmer at 36 would have kept hunting Bandits five levels below it all night
(Toxic was 35, Suicide 33). The 2026-09-11 memory survey saw 99 Ratlings
(type 8) at x541-576/y449-523 on map 1011, mixed with FireRatL38, RatAide and
RatMessenger.

Failure modes, written before the route:
1. Level 36 still selects Bandits (or nothing), or 41 does not move on.
2. The route targets anything but the Ratling family (RatAide, RatMessenger
   and BanditAide are bosses), or leaves out its FireRatL38 member.
3. The hunting area misses the surveyed Ratlings.
4. The town connector or patrol crosses a blocked tile or a gap longer than a
   movement segment (12 tiles), or does not run town to anchor and back.
5. The route leaves Phoenix Castle, its restock town or its terrain version.
"""

import json
from pathlib import Path

import pytest

from conquest import leveling_routes
from conquest.routes import RouteLibrary, route_monster_names

SURVEY = Path("data/snapshots/2026-09-11/performance/attackability-survey.json")


def sightings(kind):
    points = []

    def walk(node):
        if isinstance(node, dict):
            for actor in node.get("actors", ()):
                if actor.get("type") == kind and actor.get("position"):
                    points.append(tuple(actor["position"]))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(json.loads(SURVEY.read_text(encoding="utf-8")))
    return points


@pytest.fixture(scope="module")
def route():
    return RouteLibrary().load("ratling")


@pytest.fixture(scope="module")
def terrain():
    from conquest.navigation import read_terrain

    try:
        return read_terrain(r"C:\Program Files\Classic Conquer 2.0", 1011)
    except (OSError, ValueError) as error:
        pytest.skip(f"installed Phoenix Castle map unavailable: {error}")


def test_level_36_hunts_ratlings_until_41(route):
    # 1
    assert leveling_routes.hunting_level(36) == 37
    for level in (37, 41):
        selected, entry = leveling_routes.desired_route(level)
        assert selected.id == route.id == entry["saved_route"] == "ratling"
    assert leveling_routes.hunting_level(41) == 42
    assert leveling_routes.desired_route(42)[0].id == "firespirit"


def test_only_the_ratling_family_is_targeted(route):
    # 2. FireRatL38 (type 67, level 38) is the family's L38 member, like
    # BanditL33 beside Bandit: untargeted it only hit Suicide from 4-9 tiles
    # through its whole Ratling trial (live 2026-09-28 12:35-12:52). The
    # RatMessenger joined it as a left-click target (Alex 2026-09-28: "if
    # there ever is a messenger version of the monster just kill it").
    assert route.monster_type_ids == (8, 67, 8103)
    assert route_monster_names(route) == ("Ratling", "FireRatL38", "RatMessenger")
    for kind in (8203, 8303, 8202):
        with pytest.raises(ValueError):
            route_monster_names(route.model_copy(update={"monster_type_ids": (8, kind)}))


def test_the_hunting_area_covers_the_surveyed_ratlings(route):
    # 3
    ratlings = sightings(8)
    assert len(ratlings) == 99
    left, top, right, bottom = route.hunting_boundary
    assert all(left <= x <= right and top <= y <= bottom for x, y in ratlings)
    x, y = route.hunting_anchor
    assert left <= x <= right and top <= y <= bottom
    assert all(left <= px <= right and top <= py <= bottom for px, py in route.patrol)


def _segment(a, b):
    """The tiles of one straight movement segment (unit or diagonal steps)."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    steps = max(abs(dx), abs(dy))
    assert steps <= 12, (a, b)
    assert dx in (0, steps, -steps) or dy in (0, steps, -steps) or abs(dx) == abs(dy), (a, b)
    sx = (dx > 0) - (dx < 0)
    sy = (dy > 0) - (dy < 0)
    return [(a[0] + sx * i, a[1] + sy * i) for i in range(1, steps + 1)]


def test_town_connector_and_patrol_are_walkable(route, terrain):
    # 4, 5
    assert terrain.source_sha256 == route.terrain_sha256
    assert route.outbound_waypoints[0] == route.town_anchor
    assert route.outbound_waypoints[-1] == route.hunting_anchor
    assert route.return_waypoints[0] == route.hunting_anchor
    assert route.return_waypoints[-1] == route.town_anchor
    for path in (route.outbound_waypoints, route.return_waypoints):
        assert all(terrain.walkable(p) for p in path)
        for a, b in zip(path, path[1:]):
            assert all(terrain.walkable(t) for t in _segment(a, b)), (a, b)
    assert terrain.walkable(route.hunting_anchor)
    for point in route.patrol:
        assert terrain.walkable(point)
        terrain.path(route.hunting_anchor, point)


def test_the_route_stays_on_phoenix_castle_with_the_bandit_town(route):
    # 5
    bandit = RouteLibrary().load("bandit")
    assert route.map_id == route.restock_map_id == 1011
    assert route.terrain_sha256 == bandit.terrain_sha256
    assert (route.town_anchor, route.restock_anchor) == (
        bandit.town_anchor,
        bandit.restock_anchor,
    )
    assert route.recommended_levels == (37, 41)
    assert route.kite_when_surrounded and route.jump_scatter
