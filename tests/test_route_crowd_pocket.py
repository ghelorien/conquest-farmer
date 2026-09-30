"""A covered corridor must not send the farmer into a pocket.

Live 2026-09-28 06:25-06:32 (Suicide, Phoenix Castle): walking from (190,247)
to the Warehouseman (230,250), the corridor past the Pharmacist (191,250) lay
under the NPC's sprite, and every reroute clicked the open tile (193,247):
nearest to the goal in a straight line, but a pocket whose way on doubles back
through the corridor. The restock looped for seven minutes until the farmer
was jumped through by hand.

Failure modes, written before the fix:
1. The reroute picks a landing closer in a straight line but not by walk.
2. With no landing that shortens the walk, a pocket is still returned.
"""

import pytest

from conquest.route_crowd import Crowd
from conquest.viewport import scene_bounds

SOURCE, GOAL, ANCHOR = (190, 247), (230, 250), (708, 438)
BOUNDS = scene_bounds((1416, 876))


@pytest.fixture(scope="module")
def phoenix():
    from conquest.navigation import read_terrain

    try:
        return read_terrain(r"C:\Program Files\Classic Conquer 2.0", 1011)
    except (OSError, ValueError) as error:
        pytest.skip(f"installed Phoenix Castle map unavailable: {error}")


def draw(tile):
    """A Role's client draw point for its tile, seen from the farmer's anchor."""
    dx, dy = tile[0] - SOURCE[0], tile[1] - SOURCE[1]
    return (ANCHOR[0] + (dx - dy) * 32, ANCHOR[1] + (dx + dy) * 16)


def walk(terrain, start):
    return len(terrain.travel_path(start, GOAL))


def test_the_landing_shortens_the_walk_past_the_pharmacist(phoenix):
    # 1: the farmer's own sprite and the Pharmacist (an NPC UID) at (191,250).
    crowd = Crowd([(0, ANCHOR), (500, draw((191, 250)))])
    assert walk(phoenix, (193, 247)) > walk(phoenix, SOURCE)  # the pocket
    landing = crowd.open_landing(phoenix, SOURCE, GOAL, ANCHOR, bounds=BOUNDS)
    assert landing is not None and landing != (193, 247)
    assert walk(phoenix, landing) < walk(phoenix, SOURCE)


def test_a_covered_click_on_a_long_walk_lands_on_the_walk_at_once():
    # Suicide stood still 13.9 s at (563, 432) among a GiantApe pack and died
    # (2026-09-29 22:39): its 630-tile walk to town runs round by the east
    # road, so every tile closer in a straight line was replanned, 56 times.
    import time

    from conquest.navigation import read_terrain

    try:
        ape = read_terrain(r"C:\Program Files\Classic Conquer 2.0", 1020)
    except (OSError, ValueError) as error:
        pytest.skip(f"installed Ape City map unavailable: {error}")
    source, covered, goal = (563, 432), (575, 431), (580, 542)
    dx, dy = covered[0] - source[0], covered[1] - source[1]
    crowd = Crowd([(0, ANCHOR), (1_000_001, (ANCHOR[0] + (dx - dy) * 32, ANCHOR[1] + (dx + dy) * 16))])
    plans = []
    real = ape.travel_path

    def counted(*args, **kwargs):
        plans.append(args)
        return real(*args, **kwargs)

    ape.travel_path = counted
    started = time.perf_counter()
    landing = crowd.open_landing(ape, source, goal, ANCHOR, bounds=BOUNDS)
    assert len(plans) == 1 and time.perf_counter() - started < 5
    walk = real(source, goal)
    assert landing in walk and landing != covered
    assert len(real(landing, goal)) < len(walk)


def test_no_landing_rather_than_a_pocket(phoenix):
    # 2: with the corridor and everything beyond it unavailable, only the
    # pocket and the way back remain, and none of them shortens the walk.
    avoid = {(x, y) for x in range(186, 204) for y in range(248, 264)}
    crowd = Crowd([(0, ANCHOR), (500, draw((191, 250)))])
    assert crowd.open_landing(
        phoenix, SOURCE, GOAL, ANCHOR, avoid=avoid, bounds=BOUNDS
    ) is None
