"""Walks plan round bosses seen lately, not only those in view.

2026-09-30 03:36: past King 404775, travel_path from (578, 336) to
thunderape-nw ran straight west through a GiantApeAide at (480, 324), a Msgr
at (482, 338) and Kings at (481, 309) and (454, 340). The walk round them,
north of y 300, was just as short. Suicide met them 12-15 tiles off, took
Msgr hits and went home heavy-damaged.
"""

import importlib
from types import SimpleNamespace

import pytest

from conquest import native_farm
from conquest.navigation import line_tiles, read_terrain
from conquest.routes import RouteLibrary
from conquest.world_travel import CLIENT_ROOT
from test_native_farm import setup

NEST = [("GiantApeMsgr", (482, 338)), ("GiantApeAide", (480, 324)),
        ("GiantApeKing", (481, 309)), ("GiantApeKing", (454, 340))]


def cheb(a, b):
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def boss(entity_id, name, position):
    return SimpleNamespace(entity_id=entity_id, name=name, position=position)


class Patch:
    def __init__(self, monkeypatch):
        self.monkeypatch = monkeypatch

    def setattr(self, target, name, value=None):
        if isinstance(target, str):
            module, _, attr = target.rpartition(".")
            self.monkeypatch.setattr(importlib.import_module(module), attr, name)
        else:
            self.monkeypatch.setattr(target, name, value)


@pytest.fixture
def farm(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(native_farm.time, "monotonic", lambda: clock[0])
    supervisor, _, _, _ = setup(Patch(monkeypatch))
    supervisor.map_id = 1020
    supervisor.position = (578, 336)
    return supervisor, clock


def test_bosses_are_remembered_and_forgotten(farm):
    supervisor, clock = farm
    supervisor.remember_bosses([boss(7, "GiantApeKing", (481, 309)),
                                boss(8, "GiantApe", (560, 330))])
    assert [(b.name, b.position) for b in supervisor.remembered_bosses(())] == [
        ("GiantApeKing", (481, 309))]
    # In view: not listed twice.
    assert supervisor.remembered_bosses([boss(7, "GiantApeKing", (481, 309))]) == []
    # Old sightings lapse.
    clock[0] += native_farm.BOSS_MEMORY_SECONDS + 1
    supervisor.remember_bosses([])
    assert supervisor.remembered_bosses(()) == []


def test_a_boss_missing_from_its_tile_in_plain_view_is_forgotten(farm):
    supervisor, clock = farm
    supervisor.remember_bosses([boss(7, "GiantApeKing", (481, 309))])
    supervisor.position = (485, 312)  # 4 tiles from its last tile, not in view
    clock[0] += 5
    supervisor.remember_bosses([])
    assert supervisor.remembered_bosses(()) == []


def test_only_remembered_bosses_within_reach_shape_the_walk(farm):
    supervisor, _ = farm
    far = (578 + native_farm.BOSS_MEMORY_REACH + 1, 336)
    supervisor.remember_bosses([boss(7, "GiantApeKing", (481, 309)), boss(9, "GiantApeKing", far)])
    assert [b.entity_id for b in supervisor.remembered_bosses(())] == [7]
    supervisor.position = (far[0] - 10, far[1])
    assert sorted(b.entity_id for b in supervisor.remembered_bosses(())) == [9]


def test_another_maps_boss_is_kept_but_not_listed(farm):
    supervisor, _ = farm
    supervisor.remember_bosses([boss(7, "GiantApeKing", (481, 309))])
    supervisor.map_id = 1002
    supervisor.position = (481, 310)
    supervisor.remember_bosses([])
    assert supervisor.remembered_bosses(()) == []
    supervisor.map_id = 1020
    assert len(supervisor.remembered_bosses(())) == 1


def walk(supervisor, clock, anchor, steps=80, box=None):
    terrain = supervisor.recovery.terrain
    box = box or (0, 0, terrain.width - 1, terrain.height - 1)
    position, trail = supervisor.position, [supervisor.position]
    for _ in range(steps):
        clock[0] += 1
        supervisor.position = position
        supervisor.scene_monsters = ()  # the nest is out of view the whole way
        supervisor.scene_timestamp = clock[0]
        step = supervisor.patrol_step(position, anchor, box, chase=False)
        trail += list(line_tiles(position, step))
        position = tuple(step)
        if cheb(position, anchor) <= 1:
            break
    return position, trail


@pytest.mark.parametrize("remembered", [True, False])
def test_the_walk_to_the_thunderapes_goes_round_a_remembered_nest(farm, remembered):
    supervisor, clock = farm
    supervisor.recovery.terrain = read_terrain(CLIENT_ROOT, 1020)
    route = RouteLibrary().load("thunderape-nw")
    supervisor.king_clearance = route.king_clearance
    supervisor.elite_clearance = route.elite_clearance
    if remembered:
        supervisor.remember_bosses([boss(i, name, spot) for i, (name, spot) in enumerate(NEST)])
    anchor = tuple(route.hunting_anchor)
    position, trail = walk(supervisor, clock, anchor)
    assert cheb(position, anchor) <= 1
    closest = min(cheb(tile, spot) for tile in trail for _, spot in NEST)
    if remembered:
        # Round the nest, outside every clearance (Kings 15, Aide/Msgr 13).
        for name, spot in NEST:
            clearance = route.king_clearance if name.endswith("King") else route.elite_clearance
            assert min(cheb(tile, spot) for tile in trail) > clearance, (name, spot)
    else:
        # The old walk ran through the Aide's tile.
        assert closest <= 2


def test_the_detour_fits_the_travel_box_of_the_walk_from_town(farm):
    # c736cf0 takes a detour only inside the travel boundary; the trial draws
    # it TRAVEL_PADDING round the walk planned from town.
    from conquest.navigation import hunting_return_path

    supervisor, clock = farm
    terrain = supervisor.recovery.terrain = read_terrain(CLIENT_ROOT, 1020)
    route = RouteLibrary().load("thunderape-nw")
    supervisor.king_clearance = route.king_clearance
    supervisor.elite_clearance = route.elite_clearance
    anchor = tuple(route.hunting_anchor)
    _, box = hunting_return_path(terrain, tuple(route.town_anchor), anchor, route.hunting_boundary)
    supervisor.remember_bosses([boss(i, name, spot) for i, (name, spot) in enumerate(NEST)])
    position, trail = walk(supervisor, clock, anchor, box=box)
    assert cheb(position, anchor) <= 1
    assert all(box[0] <= x <= box[2] and box[1] <= y <= box[3] for x, y in trail)
    assert min(cheb(tile, spot) for tile in trail for _, spot in NEST) > route.elite_clearance
