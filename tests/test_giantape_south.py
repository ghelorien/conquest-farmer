"""Toxic's GiantApe field south of the plain's middle King.

Alex (2026-09-29): "Make sure the giant ape has maximized exp per hour
route". giantape-north (x 575-625, y <= 284) fell from 1.5M to ~1.1M xp/h in
15 minutes as it thinned; giantape-south takes the plain from x 587 south of
the GiantApeKing idling at (600-603, 297-303), beside Suicide's giantape-west
strip. A field wrapped around that King was rejected: the trial's boundary
return walks travel_path straight to the anchor, and from its north lobe
that path passed 2 tiles from him.
"""

from conquest.navigation import read_terrain
from conquest.routes import RouteLibrary
from conquest.world_travel import CLIENT_ROOT

KINGS = [(601, 300), (570, 361), (570, 365), (544, 306)]
AIDE = (556, 256)


def cheb(a, b):
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def test_it_never_overlaps_suicides_strip_even_after_expansion():
    south = RouteLibrary().load("giantape-south")
    west = RouteLibrary().load("giantape-west")
    grow = south.patrol_search.expansion_tiles * south.patrol_search.maximum_expansions
    assert south.hunting_boundary[0] - grow > west.hunting_boundary[2]


def test_patrol_and_anchor_stay_outside_every_boss_clearance():
    south = RouteLibrary().load("giantape-south")
    terrain = read_terrain(CLIENT_ROOT, 1020)
    for point in list(south.patrol) + [south.hunting_anchor]:
        assert terrain.walkable(tuple(point))
        assert min(cheb(point, k) for k in KINGS) > south.king_clearance
        assert cheb(point, AIDE) > south.elite_clearance


def test_boundary_returns_to_the_anchor_keep_clear_of_every_king():
    # Laptop2 18:27: a boundary return held Suicide 11 tiles from a King.
    south = RouteLibrary().load("giantape-south")
    terrain = read_terrain(CLIENT_ROOT, 1020)
    x0, y0, x1, y1 = south.hunting_boundary
    # Box corners, just outside it, the x-605 road, and the plain's east side
    # where Toxic's live approach from town came in (832, 539) -> (746, 406).
    sources = [(x0 + 1, y0 + 1), (x1 - 2, y0 + 1), (x0 + 1, y1 - 2), (x1 - 2, y1 - 2),
               (605, 356), (650, 341), (583, 330), (640, 330), (605, 380),
               (746, 406), (832, 539), (700, 380)]
    for source in sources:
        path = terrain.travel_path(source, tuple(south.hunting_anchor))
        assert min(cheb(p, k) for p in path for k in KINGS) > south.king_clearance, source


def test_the_road_walks_into_the_field_and_back():
    south = RouteLibrary().load("giantape-south")
    terrain = read_terrain(CLIENT_ROOT, 1020)
    for rows in (south.outbound_waypoints, south.return_waypoints):
        for a, b in zip(rows, rows[1:]):
            terrain.path(tuple(a), tuple(b))
    assert tuple(south.outbound_waypoints[-1]) == tuple(south.hunting_anchor)
    assert tuple(south.return_waypoints[0]) == tuple(south.hunting_anchor)
