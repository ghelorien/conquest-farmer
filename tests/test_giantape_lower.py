"""Toxic's second GiantApe field: the ground below the south lobe.

Suicide alternates giantape-west and giantape-south from 21:29 (2026-09-29);
its scan of the south box found the GiantApes just south of it, at
(573-609, 368-379). Toxic rotates between giantape-north and this field.
"""

from conquest.navigation import read_terrain
from conquest.routes import RouteLibrary
from conquest.world_travel import CLIENT_ROOT

# GiantApeKing 404775 idles over (598-603, 297-303); the others and the Aide
# as surveyed at 17:44-17:54 (Kings move: 404772 stood at (601-603, 315) at
# 20:41, which the trial's live boss checks cover).
KINGS = [(x, y) for x in range(598, 604) for y in range(297, 304)] + [
    (570, 361), (570, 365), (544, 306), (541, 303), (547, 310), (602, 315)]
AIDES = [(555, 255), (558, 257)]
TOWN = (564, 539)  # Ape City's Warehouseman approach, where restocks start.


def cheb(a, b):
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def king_distance(point):
    return min(cheb(point, k) for k in KINGS)


def grown(route):
    grow = route.patrol_search.expansion_tiles * route.patrol_search.maximum_expansions
    x0, y0, x1, y1 = route.hunting_boundary
    return x0 - grow, y0 - grow, x1 + grow, y1 + grow


def test_it_never_meets_suicides_fields_after_expansion():
    library = RouteLibrary()
    lower = grown(library.load("giantape-lower"))
    for other in ("giantape-south", "giantape-west", "giantape-north"):
        x0, y0, x1, y1 = grown(library.load(other))
        assert lower[1] > y1 or lower[0] > x1 or lower[2] < x0 or lower[3] < y0, other


def test_patrol_and_anchor_stay_outside_every_boss_clearance():
    lower = RouteLibrary().load("giantape-lower")
    terrain = read_terrain(CLIENT_ROOT, 1020)
    for point in list(lower.patrol) + [lower.hunting_anchor]:
        assert terrain.walkable(tuple(point))
        assert king_distance(point) > lower.king_clearance
        assert min(cheb(point, a) for a in AIDES) > lower.elite_clearance


def test_boundary_returns_and_town_walks_never_walk_toward_a_king():
    lower = RouteLibrary().load("giantape-lower")
    terrain = read_terrain(CLIENT_ROOT, 1020)
    x0, y0, x1, y1 = grown(lower)
    sources = [
        (x, y)
        for x in range(x0, x1 + 1, 4)
        for y in range(y0, y1 + 1, 4)
        if terrain.walkable((x, y))
    ]
    assert len(sources) > 60
    for source in sources:
        floor = min(king_distance(source), lower.king_clearance + 1)
        for goal in (tuple(lower.hunting_anchor), TOWN):
            path = terrain.travel_path(source, goal)
            assert min(king_distance(p) for p in path) >= floor, (source, goal)


def test_the_approach_and_replans_beside_it_keep_clear_of_every_king():
    # travel_path's equally short "V" paths crossed King 404775 from 2-4 tiles
    # off the road to the west strip; this field's geometry has none.
    lower = RouteLibrary().load("giantape-lower")
    terrain = read_terrain(CLIENT_ROOT, 1020)
    anchor = tuple(lower.hunting_anchor)
    road = terrain.travel_path(TOWN, anchor)
    assert min(king_distance(p) for p in road) > lower.king_clearance
    for x, y in road[::9]:
        if y > 460:
            continue  # Town streets and the road east, far from every King.
        for dx, dy in ((4, 0), (-4, 0), (0, 4), (0, -4), (3, 3), (-3, -3)):
            source = (x + dx, y + dy)
            if not terrain.walkable(source):
                continue
            path = terrain.travel_path(source, anchor)
            assert min(king_distance(p) for p in path) > lower.king_clearance, source


def test_the_road_walks_into_the_field_and_back():
    lower = RouteLibrary().load("giantape-lower")
    terrain = read_terrain(CLIENT_ROOT, 1020)
    for rows in (lower.outbound_waypoints, lower.return_waypoints):
        for a, b in zip(rows, rows[1:]):
            terrain.path(tuple(a), tuple(b))


def test_it_hunts_like_giantape_south():
    library = RouteLibrary()
    lower, south = library.load("giantape-lower"), library.load("giantape-south")
    for field in ("king_clearance", "elite_clearance", "jump_scatter",
                  "kite_when_surrounded", "supplies", "restock_map_id", "patrol_search"):
        assert getattr(lower, field) == getattr(south, field), field
    # GiantApeMsgr (8106) is an elite to dodge, never a target (Alex 2026-09-30).
    assert lower.monster_type_ids == south.monster_type_ids == (11, 70)
