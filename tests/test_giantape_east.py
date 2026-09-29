"""Toxic's GiantApe field wrapped around the plain's middle King.

Alex (2026-09-29): "Make sure the giant ape has maximized exp per hour
route". giantape-north (x 575-625, y <= 284) fell from 1.5M to ~1.1M xp/h in
15 minutes as it thinned; giantape-east takes the whole plain from x 587
around the GiantApeKing idling at (600-603, 297-303), beside Suicide's
giantape-west strip.
"""

from conquest.navigation import read_terrain
from conquest.routes import RouteLibrary
from conquest.world_travel import CLIENT_ROOT

KINGS = [(601, 300), (570, 361), (570, 365), (544, 306)]
AIDE = (556, 256)


def cheb(a, b):
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def test_it_never_overlaps_suicides_strip_even_after_expansion():
    east = RouteLibrary().load("giantape-east")
    west = RouteLibrary().load("giantape-west")
    grow = east.patrol_search.expansion_tiles * east.patrol_search.maximum_expansions
    assert east.hunting_boundary[0] - grow > west.hunting_boundary[2]


def test_patrol_and_anchor_stay_outside_every_boss_clearance():
    east = RouteLibrary().load("giantape-east")
    terrain = read_terrain(CLIENT_ROOT, 1020)
    for point in list(east.patrol) + [east.hunting_anchor]:
        assert terrain.walkable(tuple(point))
        assert min(cheb(point, k) for k in KINGS) > east.king_clearance
        assert cheb(point, AIDE) > east.elite_clearance


def test_the_road_walks_into_the_south_part_and_back():
    east = RouteLibrary().load("giantape-east")
    terrain = read_terrain(CLIENT_ROOT, 1020)
    for rows in (east.outbound_waypoints, east.return_waypoints):
        for a, b in zip(rows, rows[1:]):
            terrain.path(tuple(a), tuple(b))
    assert tuple(east.outbound_waypoints[-1]) == tuple(east.hunting_anchor)
    assert tuple(east.return_waypoints[0]) == tuple(east.hunting_anchor)
    # The field's free ground is one region around the King.
    x0, y0, x1, y1 = east.hunting_boundary
    free = {
        (x, y)
        for x in range(x0, x1 + 1)
        for y in range(y0, y1 + 1)
        if terrain.walkable((x, y)) and cheb((x, y), KINGS[0]) > east.king_clearance
    }
    start = next(iter(free))
    seen, stack = {start}, [start]
    while stack:
        x, y = stack.pop()
        for n in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if n in free and n not in seen:
                seen.add(n)
                stack.append(n)
    assert len(seen) == len(free)
