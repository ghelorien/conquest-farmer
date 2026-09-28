"""A walk back into the hunting area waits while a boss has no room from us,
and distant bosses stay visible to the escape.

Suicide 2026-09-28 14:38: after a boss escape left the Ratling boundary, the
boundary return walked it ~18 tiles back into the RatKing, where it died.
"""

import logging
from types import SimpleNamespace

import numpy as np
import pytest
from trial_template import trial_template


class Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.names = []

    def emit(self, record):
        self.names.append(record.getMessage())


@pytest.mark.parametrize("king_at,walks", [((90, 150), False), ((60, 150), True), (None, True)])
def test_boundary_return_waits_for_room_from_a_boss(tmp_path, monkeypatch, king_at, walks):
    import yaml, win32api
    from conquest import trial
    from conquest.farmer_profile import CombatSpeed
    from conquest.memory_inventory import InventorySnapshot, Item
    from conquest.navigation import TerrainMap

    now = [10.0]
    moves = []
    config = trial_template("pheasant-foreground-trial.yaml")
    config.update(
        character="ReturnHoldTest",
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
                values = dict(name="ReturnHoldTest", position=[95, 150], max_hp=[100],
                              kill_counter=[0], level=[40], map=[1002])
                return {"fields": [{"name": k, "value": v} for k, v in values.items()]}
            assert operation == "foreground-click"
            moves.append(body["point"])
            return {}

    class Inventory:
        def __init__(self, *args):
            pass

        def read(self):
            now[0] += 0.01
            return InventorySnapshot(now[0], now[0], (Item(1, 1000000, 1, 1, 0),),
                                     Item(2, 1050000, 200, 200, None), 0, 40)

    terrain = TerrainMap(1002, 300, 300, np.zeros((300, 300), dtype=bool), "", (), ())
    bosses = [SimpleNamespace(name="RatKing", position=king_at)] if king_at else []
    supervisor = SimpleNamespace(
        last_target=None,
        escape_monsters=tuple(bosses),
        recovery=SimpleNamespace(terrain=terrain),
        observe=lambda: {"health_ratio": 1.0, "waiting": False, "defending": False},
        memory_targets=lambda *a: [],
        dispatch=lambda callback, **kwargs: callback(),
        loot_step=lambda *a: False,
        player_projection=lambda: ((95, 150), (518, 396)),
        patrol_step=lambda position, destination, boundary, chase=False: (
            position[0] + 6, position[1]
        ),
        movement_failed=lambda *a: None,
        finish_target=lambda *a: None,
    )
    monkeypatch.setattr(trial, "MemoryInventoryReader", Inventory)
    monkeypatch.setattr(trial, "resolve_player", lambda *a: dict.fromkeys(
        ("name", "position", "max_hp", "kill_counter", "level", "map"), 1))
    logger = logging.getLogger(f"return-hold-{king_at}")
    capture = Capture()
    logger.addHandler(capture)
    logger.setLevel(logging.INFO)
    camera = SimpleNamespace(geometry=lambda: (0, 0), close=lambda: None)
    trial.run_trial(path, None, tmp_path / "run", 2, logger, session_override=Session(),
                    camera_factory=lambda *a: camera, supervisor=supervisor)
    assert "boundary_return_started" in capture.names
    assert bool(moves) is walks
    assert ("boundary_return_held_for_boss" in capture.names) is not walks


def test_distant_king_is_watched_without_an_hp_read(monkeypatch):
    from test_native_farm import setup
    from conquest import monster_health
    from conquest.memory_entities import MonsterObservation

    supervisor, _, _, _ = setup(monkeypatch)
    supervisor.position = (20, 20)
    king = MonsterObservation(2000, 90, "RatKing", (37, 20), (900, 400), 81, 7, type_id=8302)
    near = MonsterObservation(1000, 25, "Pheasant", (22, 20), (600, 400), 81, 7, type_id=1)
    supervisor.observer.entities = SimpleNamespace(
        layout=None, read=lambda **kw: SimpleNamespace(monsters=[king, near])
    )
    reads = []
    monkeypatch.setattr(
        monster_health, "read_monster_health", lambda a, l, m: reads.append(m.name) or 50
    )
    supervisor.king_clearance = 15  # a 17-tile King is inside 15 + 4
    supervisor.memory_targets()
    assert "RatKing" in [m.name for m in supervisor.escape_monsters]
    assert "RatKing" not in reads and supervisor.targets_observation_available
    supervisor.king_clearance = 9  # 17 tiles is beyond 9 + 4
    supervisor.memory_targets()
    assert "RatKing" not in [m.name for m in supervisor.escape_monsters]
