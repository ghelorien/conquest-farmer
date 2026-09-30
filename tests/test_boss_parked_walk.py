"""A boss parked on a walk back to the hunting area is walked round, not waited on.

2026-09-30 10:37: a GiantApeKing parked at (619, 331) on the GiantApe plain's
gateway held Suicide 8 minutes at (636, 346): every planned step closed in on
him, so the boss hold (boss_step_ok) refused it, and patrol_step's detour both
hugged his 15-tile clearance (inside the hold's 19-tile room) and had to fit a
travel box drawn round the direct walk. An equally short walk kept his
clearance, round the north via (617, 297).

Failure modes, written before the fix:
1. The walk's boss zone is only the clearance, so a detour's first step closes
   in inside the room and the hold refuses it.
2. A farmer already inside the room plans through the tiles closer than it
   stands (or keeps only BOSS_INNER), and every step is refused again.
3. A hold that refuses every step never redraws the travel box, so a detour
   wider than the box is never taken.
"""

import logging
from types import SimpleNamespace

import numpy as np
import pytest
from trial_template import trial_template

from conquest import native_farm
from conquest.navigation import TerrainMap
from conquest.routes import BOSS_INNER, boss_step_ok, boss_zone
from test_native_farm import setup

KING = SimpleNamespace(name="GiantApeKing", position=(100, 100), alive=True, current_hp=45000)


def test_the_zone_takes_the_room_and_never_lets_a_walk_close_in():
    # 1: from afar, clearance 15 + margin 4.
    far = boss_zone((140, 100), (0, 100), [KING], king_clearance=15, margin=4)
    assert (119, 100) in far and (120, 100) not in far
    # 2: 17 tiles off (inside the room), only the tiles closer than that.
    near = boss_zone((117, 100), (0, 100), [KING], king_clearance=15, margin=4)
    assert (100, 116) in near and (100, 117) not in near
    # Inside the clearance itself the walk may still leave by BOSS_INNER.
    inside = boss_zone((110, 100), (0, 100), [KING], king_clearance=15, margin=4)
    assert (100 + BOSS_INNER, 100) in inside and (100 + BOSS_INNER + 1, 100) not in inside
    # No margin: the old zone, unchanged for town travel.
    plain = boss_zone((140, 100), (0, 100), [KING], king_clearance=15)
    assert (115, 100) in plain and (116, 100) not in plain
    assert boss_zone((117, 100), (0, 100), [KING], king_clearance=15) == plain


def test_a_walk_held_beside_a_parked_king_steps_round_him(monkeypatch):
    # 1 and 2, on the plain's scale: the King sits across the walk west.
    supervisor, _, _, _ = setup(monkeypatch)
    monkeypatch.setattr(native_farm.time, "monotonic", lambda: 100.0)
    supervisor.recovery.terrain = TerrainMap(
        1020, 200, 200, np.zeros((200, 200), dtype=bool), "", (), ()
    )
    supervisor.king_clearance = 15
    supervisor.elite_clearance = 13
    supervisor.scene_monsters = (KING,)
    supervisor.scene_timestamp = 100.0
    source = (117, 115)  # 17 tiles from him, like Suicide at (636, 346)
    step = supervisor.patrol_step(source, (10, 100), (0, 0, 199, 199), chase=False)
    assert boss_step_ok(source, step, [KING], king_clearance=15, elite_clearance=13)
    _, path, detoured = supervisor.travel_path_cache
    assert detoured and min(max(abs(p[0] - 100), abs(p[1] - 100)) for p in path) >= 17


class Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.rows = []

    def emit(self, record):
        self.rows.append((record.getMessage(), getattr(record, "fields", {})))


def test_a_hold_that_refuses_every_step_redraws_a_wider_travel_box(tmp_path, monkeypatch):
    # 3: the farmer's walk east closes in on a King ahead; after
    # RETURN_REPLAN_WAIT_SECONDS of refused steps the box is redrawn (and
    # widened on each redraw with no step between).
    import yaml, win32api
    from conquest import trial
    from conquest.farmer_profile import CombatSpeed
    from conquest.memory_inventory import InventorySnapshot, Item

    now = [10.0]
    config = trial_template("pheasant-foreground-trial.yaml")
    config.update(
        character="ParkedKingTest",
        observation_mode="memory_only",
        kite_when_surrounded=False,
        adaptive_scatter=False,
        jump_scatter=False,
        attack_button="right",
        attack_range_tiles=8,
        boundary=[100, 100, 200, 200],
        route=[[150, 150]],
        hunting_anchor=[150, 150],
        healing_enabled=False,
        loot_allowlist=[],
        interval=0.15,
        client_size=[1036, 793],
        player_anchor=[518, 396],
    )
    path = tmp_path / "profile.yaml"
    path.write_text(yaml.safe_dump(config))
    monkeypatch.setattr(trial.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(trial.time, "sleep", lambda d: now.__setitem__(0, now[0] + d))
    monkeypatch.setattr(win32api, "GetAsyncKeyState", lambda _: 0)
    monkeypatch.setattr(trial, "load_combat_speed", lambda _: CombatSpeed(coherent_projection=True))

    class Session:
        def request(self, operation, body=None):
            if operation == "health":
                return {"input_revision": 7, "window": {"hwnd": 1}}
            if operation == "sample":
                # The template profile's own map (expected_map 1002).
                values = dict(name="ParkedKingTest", position=[95, 150], max_hp=[100],
                              kill_counter=[0], level=[57], map=[1002])
                return {"fields": [{"name": k, "value": v} for k, v in values.items()]}
            raise AssertionError("no step may be sent toward the King")

    class Inventory:
        def __init__(self, *args):
            pass

        def read(self):
            now[0] += 0.01
            return InventorySnapshot(now[0], now[0], (Item(1, 1000000, 1, 1, 0),),
                                     Item(2, 1050000, 200, 200, None), 0, 40)

    terrain = TerrainMap(1020, 400, 400, np.zeros((400, 400), dtype=bool), "", (), ())
    king = SimpleNamespace(name="GiantApeKing", position=(106, 150))
    supervisor = SimpleNamespace(
        last_target=None,
        king_clearance=9,
        escape_monsters=(king,),
        recovery=SimpleNamespace(terrain=terrain),
        observe=lambda: {"health_ratio": 1.0, "waiting": False, "defending": False},
        memory_targets=lambda *a: [],
        dispatch=lambda callback, **kwargs: callback(),
        loot_step=lambda *a: False,
        player_projection=lambda: ((95, 150), (518, 396)),
        # The walk's step always heads east, straight at him.
        patrol_step=lambda position, destination, boundary, chase=False, alternatives=(): (
            position[0] + 6, position[1]
        ),
        movement_failed=lambda *a: None,
        finish_target=lambda *a: None,
    )
    monkeypatch.setattr(trial, "MemoryInventoryReader", Inventory)
    monkeypatch.setattr(trial, "resolve_player", lambda *a: dict.fromkeys(
        ("name", "position", "max_hp", "kill_counter", "level", "map"), 1))
    logger = logging.getLogger("parked-king-replan")
    capture = Capture()
    logger.addHandler(capture)
    logger.setLevel(logging.INFO)
    camera = SimpleNamespace(geometry=lambda: (0, 0), close=lambda: None)
    trial.run_trial(path, None, tmp_path / "run", 12, logger, session_override=Session(),
                    camera_factory=lambda *a: camera, supervisor=supervisor)
    starts = [fields for name, fields in capture.rows if name == "boundary_return_started"]
    assert "boundary_return_held_for_boss" in [name for name, _ in capture.rows]
    replans = [f for f in starts if f.get("replanned") == "boss_hold"]
    assert len(replans) >= 2
    paddings = [f["padding"] for f in replans]
    assert paddings == sorted(paddings) and paddings[-1] > paddings[0]
