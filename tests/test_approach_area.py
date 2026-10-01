"""The approach boundary holds the walk patrol_step actually plans.

2026-10-01 00:00 and 00:39 (Toxic, Ape City to Love Canyon): the approach
boundary came from terrain.path, which looped east (top y 315, boundary top
291), while patrol_step planned with terrain.travel_path across the GiantApe
plain at y 288-290. Every planned walk left the boundary and was refused; each
loop re-planned all its destinations until the 0.35 s frame expired, and the
farmer stood still 14 and 5 minutes.

From the stall tile itself travel_path went further north again (y 228), so
no boundary drawn at the departure holds every later plan: the walk now plans
inside its boundary (travel_path bounds) instead of refusing what leaves it.

Failure modes, written before the change:
1. The boundary misses the travel_path route from the departure, or a walk
   from a point along the way (the stall tile) cannot be planned inside it.
2. A terrain without travel_path (test fakes, straight_path terrains) or a
   failing travel_path loses the plain path's boundary.
3. A bounded search leaves its box, or accepts an endpoint outside it.
4. patrol_step still refuses a walk whose shortest route leaves the travel
   boundary when a longer one inside it exists.
"""

from types import SimpleNamespace as NS

import numpy as np
import pytest

from conquest import native_farm
from conquest.navigation import TRAVEL_PADDING, TerrainMap, approach_area
from test_native_farm import setup


def inside(box, route):
    x0, y0, x1, y1 = box
    return all(x0 <= x <= x1 and y0 <= y <= y1 for x, y in route)


@pytest.fixture(scope="module")
def ape_mountain():
    from conquest.navigation import read_terrain

    try:
        return read_terrain(r"C:\Program Files\Classic Conquer 2.0", 1020)
    except (OSError, ValueError) as error:
        pytest.skip(f"installed Ape Mountain map unavailable: {error}")


def test_the_canyon_walk_stays_inside_its_approach_boundary(ape_mountain):
    # 1: the live departure, anchor and stall tiles.
    from conquest.navigation import FIELD_TRAVEL_LIMIT

    path, box = approach_area(ape_mountain, (557, 530), (239, 525))
    assert path[0] == (557, 530) and tuple(path[-1]) == (239, 525)
    assert inside(box, path)
    for start in ((557, 530), (814, 539)):
        walk = ape_mountain.travel_path(start, (239, 525), limit=FIELD_TRAVEL_LIMIT)
        assert inside(box, walk), start
    # From the stall tile the free route crosses the plain further north
    # (y 228); bounded, the walk is planned inside the box.
    free = ape_mountain.travel_path((788, 445), (239, 525), limit=FIELD_TRAVEL_LIMIT)
    assert not inside(box, free)
    walk = ape_mountain.travel_path(
        (788, 445), (239, 525), limit=FIELD_TRAVEL_LIMIT, bounds=box
    )
    assert inside(box, walk) and walk[0] == (788, 445) and tuple(walk[-1]) == (239, 525)


def wall_with_two_gaps():
    # A wall at x 100 open only at y <= 20 (outside the boundary used below)
    # and y >= 180 (inside it).
    blocked = np.zeros((200, 200), dtype=bool)
    blocked[21:180, 100] = True
    return TerrainMap(1020, 200, 200, blocked, "", (), ())


def test_a_bounded_search_keeps_its_box():
    # 3
    terrain = wall_with_two_gaps()
    free = terrain.travel_path((150, 60), (50, 60))
    assert min(y for _, y in free) <= 20  # the short way round the north
    box = (0, 30, 199, 199)
    walk = terrain.travel_path((150, 60), (50, 60), bounds=box)
    assert inside(box, walk) and max(y for x, y in walk if x == 100) >= 180
    with pytest.raises(ValueError, match="outside the travel boundary"):
        terrain.travel_path((150, 10), (50, 60), bounds=box)


def test_a_walk_is_planned_inside_its_travel_boundary(monkeypatch):
    # 4
    supervisor, _, _, _ = setup(monkeypatch)
    monkeypatch.setattr(native_farm.time, "monotonic", lambda: 100.0)
    supervisor.recovery.terrain = wall_with_two_gaps()
    supervisor.scene_monsters = ()
    supervisor.scene_timestamp = 100.0
    box = (0, 30, 199, 199)
    step = supervisor.patrol_step((150, 60), (50, 60), box, chase=False)
    assert step is not None
    _, path, _ = supervisor.travel_path_cache
    assert inside(box, path) and max(y for x, y in path if x == 100) >= 180


def test_a_terrain_without_travel_path_keeps_the_plain_boundary():
    # 2
    plain = [(10, 10), (11, 11), (12, 12)]
    flat = NS(width=100, height=100, path=lambda a, b: plain)
    path, box = approach_area(flat, (10, 10), (12, 12), padding=2)
    assert path == plain and box == (8, 8, 14, 14)

    def failing(a, b, limit):
        raise ValueError("Route search exceeded its node budget")

    flat.travel_path = failing
    assert approach_area(flat, (10, 10), (12, 12), padding=2)[1] == (8, 8, 14, 14)
    flat.travel_path = lambda a, b, limit: [(10, 10), (30, 5), (12, 12)]
    assert approach_area(flat, (10, 10), (12, 12), padding=2)[1] == (8, 3, 32, 14)
    assert TRAVEL_PADDING == 24
