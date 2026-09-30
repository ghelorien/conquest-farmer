"""Toxic's second GiantApe field: the west strip below its rocks.

Toxic rotates between giantape-north and this strip at each restock: a field
gives ~1.5-2.4M xp/h fresh and thins to ~0.7-1.1M within 15-30 min, and the
rested north gave 2.39M at 20:14-20:24 (2026-09-29). Suicide hunts
giantape-south since 20:34, beside the strip's east edge.
"""

from conquest.navigation import read_terrain
from conquest.routes import RouteLibrary
from conquest.world_travel import CLIENT_ROOT

# GiantApeKing 404775 idles over (598-603, 297-303); the others and the Aide
# as surveyed at 17:44-17:54.
KINGS = [(x, y) for x in range(598, 604) for y in range(297, 304)] + [
    (570, 361), (570, 365), (544, 306), (541, 303), (547, 310)]
AIDES = [(555, 255), (558, 257)]


def cheb(a, b):
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def king_distance(point):
    return min(cheb(point, k) for k in KINGS)


def grown(route):
    grow = route.patrol_search.expansion_tiles * route.patrol_search.maximum_expansions
    x0, y0, x1, y1 = route.hunting_boundary
    return x0 - grow, y0 - grow, x1 + grow, y1 + grow


def test_it_stays_apart_from_suicides_field_and_the_resting_north_after_expansion():
    library = RouteLibrary()
    strip = grown(library.load("giantape-strip"))
    south = grown(library.load("giantape-south"))
    north = grown(library.load("giantape-north"))
    assert strip[2] < south[0]
    assert strip[1] > north[3]


def test_patrol_and_anchor_stay_outside_every_boss_clearance():
    strip = RouteLibrary().load("giantape-strip")
    terrain = read_terrain(CLIENT_ROOT, 1020)
    for point in list(strip.patrol) + [strip.hunting_anchor]:
        assert terrain.walkable(tuple(point))
        assert king_distance(point) > strip.king_clearance
        assert min(cheb(point, a) for a in AIDES) > strip.elite_clearance


def test_boundary_returns_never_walk_toward_a_king():
    # The trial's boundary return walks travel_path straight to the anchor,
    # with no boss avoidance. From giantape-west's north-west corner
    # (563, 286) it detoured round the rocks 2 tiles from King 404774.
    strip = RouteLibrary().load("giantape-strip")
    terrain = read_terrain(CLIENT_ROOT, 1020)
    x0, y0, x1, y1 = grown(strip)
    sources = [
        (x, y)
        for x in range(x0, x1 + 1, 2)
        for y in range(y0, y1 + 1, 2)
        if terrain.walkable((x, y))
    ]
    assert len(sources) > 200
    for source in sources:
        path = terrain.travel_path(source, tuple(strip.hunting_anchor))
        # Never closer than the clearance, or than where it started when the
        # box edge already lies inside one (the east edge by King 404775).
        floor = min(king_distance(source), strip.king_clearance + 1)
        assert min(king_distance(p) for p in path) >= floor, source


def test_the_approach_from_town_passes_south_of_the_middle_king():
    strip = RouteLibrary().load("giantape-strip")
    terrain = read_terrain(CLIENT_ROOT, 1020)
    # The plan from Ape City's exit and from points on that road. A replan
    # from 2-4 tiles off it (657-673, 350-360) is as short through King
    # 404775's idle spot; native_farm.patrol_step walks round a visible King
    # (test_approach_and_returns_walk_round_a_visible_king).
    for source in [(564, 539), (591, 586), (754, 576), (831, 489)]:
        path = terrain.travel_path(source, tuple(strip.hunting_anchor))
        assert min(king_distance(p) for p in path) > strip.king_clearance, source


def test_the_road_walks_into_the_field_and_back():
    strip = RouteLibrary().load("giantape-strip")
    terrain = read_terrain(CLIENT_ROOT, 1020)
    for rows in (strip.outbound_waypoints, strip.return_waypoints):
        for a, b in zip(rows, rows[1:]):
            terrain.path(tuple(a), tuple(b))


def test_it_hunts_like_giantape_west():
    library = RouteLibrary()
    strip, west = library.load("giantape-strip"), library.load("giantape-west")
    for field in ("monster_type_ids", "king_clearance", "elite_clearance", "jump_scatter",
                  "kite_when_surrounded", "supplies", "restock_map_id", "hunting_anchor"):
        assert getattr(strip, field) == getattr(west, field), field
