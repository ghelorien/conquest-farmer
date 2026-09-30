"""Toxic's GiantApe ground west of King 404774.

Suicide walked through it at 22:06 (2026-09-29) with 32-41 GiantApes in view,
while the plain Toxic had hunted all evening held 6 monsters within 45 tiles
at 22:22. The planned road from town crosses the plain beside King 404774's
idle spot, so native_farm.patrol_step's walk round visible bosses is part of
this route's safety.
"""

import importlib
from types import SimpleNamespace

from conquest.navigation import line_tiles, read_terrain
from conquest.routes import RouteLibrary
from conquest.world_travel import CLIENT_ROOT

KINGS = [(x, y) for x in range(598, 604) for y in range(297, 304)] + [
    (544, 306), (541, 303), (547, 310), (570, 361), (570, 365), (556, 356),
    (566, 396), (478, 277), (462, 349)]
AIDES = [(473, 280), (527, 335), (552, 329), (556, 256), (592, 358)]


def cheb(a, b):
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def grown(route):
    grow = route.patrol_search.expansion_tiles * route.patrol_search.maximum_expansions
    x0, y0, x1, y1 = route.hunting_boundary
    return x0 - grow, y0 - grow, x1 + grow, y1 + grow


def test_it_keeps_off_suicides_road_and_thunderape_field():
    library = RouteLibrary()
    west = grown(library.load("giantape-far-west"))
    thunder = grown(library.load("thunderape-nw"))
    # Suicide's road from thunderape-nw to town crosses y 326-359 at x 450-510.
    assert west[3] < 326
    assert west[0] > thunder[2] + 50


def test_patrol_and_anchor_stay_outside_every_known_boss_clearance():
    route = RouteLibrary().load("giantape-far-west")
    terrain = read_terrain(CLIENT_ROOT, 1020)
    for point in list(route.patrol) + [route.hunting_anchor]:
        assert terrain.walkable(tuple(point))
        assert min(cheb(point, k) for k in KINGS) > route.king_clearance
        assert min(cheb(point, a) for a in AIDES) > route.elite_clearance


def test_boundary_returns_never_walk_toward_a_known_boss():
    route = RouteLibrary().load("giantape-far-west")
    terrain = read_terrain(CLIENT_ROOT, 1020)
    x0, y0, x1, y1 = grown(route)

    def room(p):
        return min(
            min(cheb(p, k) for k in KINGS) - route.king_clearance,
            min(cheb(p, a) for a in AIDES) - route.elite_clearance,
        )

    sources = [
        (x, y)
        for x in range(x0, x1 + 1, 4)
        for y in range(y0, y1 + 1, 4)
        if terrain.walkable((x, y))
    ]
    assert len(sources) > 100
    for source in sources:
        path = terrain.travel_path(source, tuple(route.hunting_anchor))
        assert min(room(p) for p in path) >= min(room(source), 1), source


def test_the_walk_from_the_plain_goes_round_king_404774_in_view(monkeypatch):
    from conquest import native_farm
    from test_native_farm import setup

    class Patch:
        def setattr(self, target, name, value=None):
            if isinstance(target, str):
                module, _, attr = target.rpartition(".")
                monkeypatch.setattr(importlib.import_module(module), attr, name)
            else:
                monkeypatch.setattr(target, name, value)

    route = RouteLibrary().load("giantape-far-west")
    terrain = read_terrain(CLIENT_ROOT, 1020)
    clock = [1000.0]
    monkeypatch.setattr(native_farm.time, "monotonic", lambda: clock[0])
    supervisor, _, _, _ = setup(Patch())
    supervisor.recovery.terrain = terrain
    supervisor.king_clearance = route.king_clearance
    supervisor.elite_clearance = route.elite_clearance
    bosses = [("GiantApeKing", p) for p in ((544, 306), (478, 277), (556, 356))] + [
        ("GiantApeAide", p) for p in ((552, 329), (473, 280), (592, 358))]
    # Toxic's approach road where it turns west across the plain.
    position, anchor = (700, 380), tuple(route.hunting_anchor)
    closest = {pos: cheb(position, pos) for _, pos in bosses}
    for _ in range(120):
        clock[0] += 1
        supervisor.scene_monsters = tuple(
            SimpleNamespace(name=name, position=pos, alive=True, current_hp=1)
            for name, pos in bosses
            if cheb(position, pos) <= 30
        )
        supervisor.scene_timestamp = clock[0]
        step = supervisor.patrol_step(
            position, anchor, (0, 0, terrain.width - 1, terrain.height - 1), chase=False
        )
        for tile in line_tiles(position, step):
            for _, pos in bosses:
                closest[pos] = min(closest[pos], cheb(tile, pos))
        position = tuple(step)
        if cheb(position, anchor) <= 1:
            break
    assert cheb(position, anchor) <= 1
    for name, pos in bosses:
        clearance = route.king_clearance if name.endswith("King") else route.elite_clearance
        assert closest[pos] > clearance, (name, pos, closest[pos])


def test_the_road_walks_into_the_field_and_back():
    route = RouteLibrary().load("giantape-far-west")
    terrain = read_terrain(CLIENT_ROOT, 1020)
    for rows in (route.outbound_waypoints, route.return_waypoints):
        for a, b in zip(rows, rows[1:]):
            terrain.path(tuple(a), tuple(b))


def test_it_hunts_like_giantape_south():
    library = RouteLibrary()
    west, south = library.load("giantape-far-west"), library.load("giantape-south")
    for field in ("monster_type_ids", "king_clearance", "elite_clearance", "jump_scatter",
                  "kite_when_surrounded", "supplies", "restock_map_id", "patrol_search"):
        assert getattr(west, field) == getattr(south, field), field
