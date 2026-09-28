"""Route travel keeps BOSS_CLEARANCE from a boss in the scene.

Combat keeps its distance from bosses (Scatter hold, boss escape, roomy
landings), but route travel had no boss logic: Toxic's WingedSnake walk runs
straight up x=317 through the WingedSnakeKing's roaming box (314-335, 88-104)
at 0-10 tiles, and runbacks cross the Bandit field past up to four
BanditKings (2026-09-28).

Failure modes, written before the change:
1. A travel path passes within BOSS_CLEARANCE of a boss in the scene when
   the terrain has a way around.
2. A boss far from the path changes the route.
3. A boss beside the destination makes the trip impossible.
4. A farmer already beside a boss cannot plan its way out.
5. With no way around, travel walks straight past the boss instead of
   waiting for it to move, or waits forever.
6. A detour's longer path is mistaken for a stall.
7. The detour is not logged.
8. No visible landing clears the bosses and the walk fails where it stands:
   three BanditKings' 15-tile zones did that to Toxic at (324,329), every
   restart failed on the spot with the farm off while Bandits hit it, and it
   died there (2026-09-28 17:44-17:46).
"""

import numpy as np
import pytest
from types import SimpleNamespace

from conquest import overnight
from conquest.navigation import TerrainMap
from conquest.overnight import OvernightLoop
from conquest.routes import BOSS_CLEARANCE, boss_zone


def terrain(width=200, height=60, walls=()):
    blocked = np.zeros((height, width), dtype=bool)
    for x0, y0, x1, y1 in walls:
        blocked[y0 : y1 + 1, x0 : x1 + 1] = True
    return TerrainMap(1011, width, height, blocked, "", (), ())


def king(x, y, name="BanditKing"):
    return {"name": name, "position": [x, y]}


def distance(a, b):
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def test_the_zone_covers_bosses_near_the_walk_only():
    # 2, 3, 4
    zone = boss_zone((20, 30), (180, 30), [king(100, 30), {"name": "Bandit", "position": [60, 30]}])
    assert (100 - BOSS_CLEARANCE, 30) in zone and (100, 30 + BOSS_CLEARANCE) in zone
    assert (100 - BOSS_CLEARANCE - 1, 30) not in zone
    assert not any(distance(p, (60, 30)) == 0 for p in zone)  # not a boss
    # A boss beside the destination: nothing to detour to.
    assert boss_zone((20, 30), (180, 30), [king(175, 30)]) == frozenset()
    # Already beside one: only its inner tiles, so the walk can leave.
    inner = boss_zone((95, 30), (180, 30), [king(100, 30)])
    assert (95, 30) not in inner and inner == frozenset(
        (x, y) for x in range(97, 104) for y in range(27, 34)
    )


def rig(monkeypatch, world, monsters, *, urgent=False):
    from conquest import city_travel, runback_monitor, scene_input

    monkeypatch.setattr(city_travel, "service_role", lambda *a: None)
    monkeypatch.setattr(scene_input, "memory_player_anchor", lambda *a: (708, 438))
    # Real terrain checks (clear_segment) on the synthetic map; only the
    # screen-side click check is stubbed.
    monkeypatch.setattr(scene_input, "clear_route_point", lambda *a: True)
    # Isolate the boss wait from the runback's own evasion step.
    monkeypatch.setattr(runback_monitor, "escape_step", lambda *a, **kw: None)
    clock = [1000.0]
    monkeypatch.setattr(overnight.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(overnight.time, "sleep", lambda s: clock.__setitem__(0, clock[0] + s))
    position = [20, 30]
    visited, events = [tuple(position)], []

    def living():
        clock[0] += 0.2
        scene = monsters(clock[0]) if callable(monsters) else monsters
        return {
            "window": {"client_size": [1416, 876]},
            "embedded_controls": {
                "life": {"position": list(position)},
                "monsters": scene,
            },
        }

    def step(target, *, expected_position):
        position[:] = target
        visited.append(tuple(target))
        return {"reached": True}

    loop = SimpleNamespace(
        terrain=world,
        route=SimpleNamespace(supplies=SimpleNamespace(arrow_type=1050000)),
        record=lambda event, **kw: events.append((event, kw)),
        living=living,
        care=SimpleNamespace(check=lambda h: None, session=None),
        stepper=SimpleNamespace(step_to=step),
        runback_watch=SimpleNamespace(urgent=urgent, recovery=lambda: None),
    )
    return loop, position, visited, events, clock


def walked_tiles(visited):
    from conquest.navigation import line_tiles

    tiles = []
    for a, b in zip(visited, visited[1:]):
        tiles += line_tiles(a, b)
    return tiles


def test_travel_detours_around_a_boss_on_the_path(monkeypatch):
    # 1, 6, 7
    loop, position, visited, events, _ = rig(monkeypatch, terrain(), [king(100, 30)])
    OvernightLoop._travel(loop, (180, 30))
    assert position == [180, 30]
    assert min(distance(p, (100, 30)) for p in walked_tiles(visited)) > BOSS_CLEARANCE
    detours = [kw for event, kw in events if event == "travel_boss_detour"]
    assert len(detours) == 1 and detours[0]["bosses"] == [["BanditKing", [100, 30]]]


def test_no_boss_keeps_the_straight_walk(monkeypatch):
    # 2
    loop, position, visited, events, _ = rig(monkeypatch, terrain(), [king(100, 55)])
    OvernightLoop._travel(loop, (180, 30))
    assert position == [180, 30]
    assert all(p[1] == 30 for p in visited)
    assert not [e for e, _ in events if e.startswith("travel_boss")]


def test_a_boss_in_the_only_corridor_is_waited_out_then_passed(monkeypatch):
    # 5: walls leave a 5-tile corridor at y 28-32 around x=100.
    world = terrain(walls=[(90, 0, 110, 27), (90, 33, 110, 59)])
    loop, position, visited, events, clock = rig(monkeypatch, world, [king(100, 30)])
    started = clock[0]
    OvernightLoop._travel(loop, (180, 30))
    assert position == [180, 30]
    waits = [kw for event, kw in events if event == "travel_boss_wait"]
    assert len(waits) == 1
    assert clock[0] - started >= overnight.BOSS_WAIT_SECONDS


def test_the_wait_ends_when_the_boss_leaves(monkeypatch):
    # 5
    world = terrain(walls=[(90, 0, 110, 27), (90, 33, 110, 59)])
    leaves = [None]

    def scene(now):
        if leaves[0] is None:
            leaves[0] = now + 5
        return [king(100, 30)] if now < leaves[0] else []

    loop, position, visited, events, clock = rig(monkeypatch, world, scene)
    started = clock[0]
    OvernightLoop._travel(loop, (180, 30))
    assert position == [180, 30]
    assert clock[0] - started < overnight.BOSS_WAIT_SECONDS


def test_no_landing_clear_of_the_bosses_walks_on_instead_of_failing(monkeypatch):
    # 8
    from conquest import navigation

    loop, position, visited, events, _ = rig(monkeypatch, terrain(), [king(100, 30)])
    real = navigation.travel_waypoint
    full = boss_zone((20, 30), (180, 30), [king(100, 30)])

    def no_landing_near_bosses(world, path, step, *, avoid=(), viewport=None):
        if full & set(avoid):
            raise ValueError("No visible route landing point")
        return real(world, path, step, avoid=avoid, viewport=viewport)

    monkeypatch.setattr(navigation, "travel_waypoint", no_landing_near_bosses)
    OvernightLoop._travel(loop, (180, 30))
    assert position == [180, 30]
    assert [event for event, _ in events].count("travel_boss_zone_crossed") == 1


def test_under_attack_the_walk_does_not_stand_and_wait(monkeypatch):
    # 5
    world = terrain(walls=[(90, 0, 110, 27), (90, 33, 110, 59)])
    loop, position, visited, events, clock = rig(
        monkeypatch, world, [king(100, 30)], urgent=True
    )
    started = clock[0]
    OvernightLoop._travel(loop, (180, 30))
    assert position == [180, 30]
    assert clock[0] - started < overnight.BOSS_WAIT_SECONDS
