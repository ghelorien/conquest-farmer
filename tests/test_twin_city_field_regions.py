"""Twin City's Poltergeist and Apparition fields are hunted as rotated regions.

2026-10-01: Laptop2's species log saw 185 distinct Poltergeists at x 88-194,
y 309-500 on open, connected ground, while the route's box [100, 340, 165,
400] held 14 of the 63 samples. After a rested burst (190-210 kills in 2-3
minutes) that box gave 3-20 kills a minute. Widened at 22:43, Suicide made
186 kills a minute. The same night's log saw 190 Apparitions at x 238-383,
y 557-647 against the Apparition box [252, 570, 340, 644]. The Bandit
field's three rotated regions held Suicide at 121-133 a minute.
"""

from pathlib import Path

import pytest
import yaml

from conquest.routes import SavedRoute

ROUTES = Path(__file__).resolve().parents[1] / "profiles" / "routes"
CLIENT = Path(r"C:\Program Files\Classic Conquer 2.0")
FIELDS = {
    # route: (region names, the box the regions must cover)
    "poltergeist": (["north", "east", "south"], (95, 320, 190, 495)),
    "poltergeist-phx": (["north", "east", "south"], (95, 320, 190, 495)),
    "apparition": (["center", "east", "west"], (240, 557, 383, 647)),
    "apparition-phx": (["center", "east", "west"], (240, 557, 383, 647)),
}


def route(name):
    return SavedRoute.model_validate(
        yaml.safe_load((ROUTES / f"{name}.yaml").read_text(encoding="utf-8"))
    )


@pytest.mark.parametrize("name", sorted(FIELDS))
def test_the_field_rotates_regions_covering_the_surveyed_spawns(name):
    names, (left, top, right, bottom) = FIELDS[name]
    saved = route(name)
    regions = saved.patrol_search.regions
    assert [r.name for r in regions] == names
    # The walk out still ends in the first region, round the old anchor.
    assert regions[0].contains(saved.hunting_anchor)
    assert saved.patrol == tuple(p for r in regions for p in r.patrol)
    x0, y0, x1, y1 = saved.hunting_boundary
    assert x0 <= left and y0 <= top and x1 >= right and y1 >= bottom


@pytest.mark.skipif(
    not (CLIENT / "ini" / "GameMap.json").exists(), reason="no installed client"
)
@pytest.mark.parametrize("name", ["poltergeist", "apparition"])
def test_every_patrol_point_is_open_ground_reachable_from_the_anchor(name):
    from conquest.navigation import read_terrain

    saved = route(name)
    terrain = read_terrain(CLIENT, saved.map_id)
    for point in saved.patrol:
        assert all(
            terrain.walkable((point[0] + dx, point[1] + dy))
            for dx in range(-2, 3)
            for dy in range(-2, 3)
        ), point
        assert terrain.path(saved.hunting_anchor, point, limit=400000), point
