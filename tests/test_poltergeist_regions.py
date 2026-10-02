"""Twin City's Poltergeist field is hunted as three rotated regions.

2026-10-01: Laptop2's species log saw 185 distinct Poltergeists at x 88-194,
y 309-500 on open, connected ground, while the route's box [100, 340, 165,
400] held 14 of the 63 samples. After a rested burst (190-210 kills in 2-3
minutes) that box gave 3-20 kills a minute. The Bandit field's three
rotated regions held Suicide at 121-133 a minute.
"""

from pathlib import Path

import pytest
import yaml

from conquest.routes import SavedRoute

ROUTES = Path(__file__).resolve().parents[1] / "profiles" / "routes"
CLIENT = Path(r"C:\Program Files\Classic Conquer 2.0")


def route(name):
    return SavedRoute.model_validate(
        yaml.safe_load((ROUTES / f"{name}.yaml").read_text(encoding="utf-8"))
    )


@pytest.mark.parametrize("name", ["poltergeist", "poltergeist-phx"])
def test_the_field_rotates_north_east_and_south(name):
    saved = route(name)
    regions = saved.patrol_search.regions
    assert [r.name for r in regions] == ["north", "east", "south"]
    # The walk out still ends in the old box, now the north region.
    assert regions[0].contains(saved.hunting_anchor)
    assert saved.patrol == tuple(p for r in regions for p in r.patrol)
    x0, y0, x1, y1 = saved.hunting_boundary
    assert x0 <= 95 and x1 >= 190 and y0 <= 320 and y1 >= 495


@pytest.mark.skipif(
    not (CLIENT / "ini" / "GameMap.json").exists(), reason="no installed client"
)
def test_every_patrol_point_is_open_ground_reachable_from_the_anchor():
    from conquest.navigation import read_terrain

    saved = route("poltergeist")
    terrain = read_terrain(CLIENT, saved.map_id)
    for point in saved.patrol:
        assert all(
            terrain.walkable((point[0] + dx, point[1] + dy))
            for dx in range(-2, 3)
            for dy in range(-2, 3)
        ), point
        assert terrain.path(saved.hunting_anchor, point, limit=400000), point
