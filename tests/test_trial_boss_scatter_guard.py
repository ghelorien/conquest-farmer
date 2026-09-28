"""Scatter is never cast while a boss could be inside its fan.

A hit boss follows the farmer: the BanditKing trailed Suicide 137 tiles in
14 minutes, and Toxic made 210 boss escapes in 3 h on WingedSnakes
(2026-09-28).

The fan reaches 8 tiles on 1078 whatever the route's attack_range_tiles (16
on every live route): a hold out to 17 tiles held every cast while a boss
stood 10-17 tiles off, where no boss escape (9 tiles) moves the farmer.
"""

import logging

import pytest
from trial_template import trial_template


class Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.names = []

    def emit(self, record):
        self.names.append(record.getMessage())


@pytest.mark.parametrize(
    "boss_at,casts,attack_range",
    [
        ((432, 455), False, 8),
        ((423, 464), False, 8),
        ((433, 455), True, 8),
        (None, True, 8),
        # Live routes target out to 16 tiles; the fan still reaches 8.
        ((432, 455), False, 16),
        ((435, 455), True, 16),
        ((423, 440), True, 16),
    ],
)
def test_scatter_waits_while_a_boss_is_within_its_reach(
    tmp_path, monkeypatch, boss_at, casts, attack_range
):
    import yaml, win32api
    from types import SimpleNamespace
    from conquest import trial
    from conquest.farmer_profile import CombatSpeed
    from conquest.memory_inventory import InventorySnapshot, Item
    from conquest.vision import Target

    now = [10.0]
    calls = []
    config = trial_template("pheasant-foreground-trial.yaml")
    config.update(
        character="BossGuardTest",
        observation_mode="memory_only",
        kite_when_surrounded=False,
        adaptive_scatter=True,
        jump_scatter=False,
        attack_button="right",
        attack_range_tiles=attack_range,
        route=[],
        healing_enabled=False,
        loot_allowlist=[],
        interval=0.15,
        client_size=[1036, 793],
        player_anchor=[518, 396],
    )
    path = tmp_path / "profile.yaml"
    path.write_text(yaml.safe_dump(config))
    monkeypatch.setattr(trial.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(
        trial.time, "sleep", lambda delay: now.__setitem__(0, now[0] + delay)
    )
    monkeypatch.setattr(win32api, "GetAsyncKeyState", lambda _: 0)
    monkeypatch.setattr(
        trial, "load_combat_speed", lambda _: CombatSpeed(coherent_projection=True)
    )

    class Session:
        def request(self, operation, body=None):
            if operation == "health":
                return {"input_revision": 7, "window": {"hwnd": 1}}
            if operation == "sample":
                values = dict(
                    name="BossGuardTest",
                    position=[423, 455],
                    max_hp=[100],
                    kill_counter=[0],
                    level=[38],
                    map=[1002],
                )
                return {"fields": [{"name": k, "value": v} for k, v in values.items()]}
            assert operation == "foreground-click"
            calls.append(body["button"])
            return {}

    class Inventory:
        def __init__(self, *args):
            pass

        def read(self):
            now[0] += 0.01
            return InventorySnapshot(
                now[0],
                now[0],
                (Item(1, 1000000, 1, 1, 0),),
                Item(2, 1050000, 200 - 2 * len(calls), 200, None),
                0,
                40,
            )

    bosses = (
        [SimpleNamespace(name="WingedSnakeKing", position=boss_at)] if boss_at else []
    )
    strategy = SimpleNamespace(
        observe=lambda *a: None, button=lambda *a: "right", issued=lambda *a: None
    )
    supervisor = SimpleNamespace(
        last_target=None,
        escape_monsters=tuple(bosses),
        recovery=SimpleNamespace(terrain=SimpleNamespace(width=1000, height=1000)),
        observe=lambda: {"health_ratio": 1.0, "waiting": False, "defending": False},
        memory_targets=lambda *a: [
            Target("Pheasant", 600, 400, 1, 1, 1000, (427, 455), 100)
        ],
        attack_strategy=lambda: strategy,
        dispatch=lambda callback, **kwargs: callback(),
        loot_step=lambda *a: False,
        player_projection=lambda: ((423, 455), (518, 396)),
        finish_target=lambda *a: None,
    )
    monkeypatch.setattr(trial, "MemoryInventoryReader", Inventory)
    monkeypatch.setattr(
        trial,
        "resolve_player",
        lambda *a: dict.fromkeys(
            ("name", "position", "max_hp", "kill_counter", "level", "map"), 1
        ),
    )
    logger = logging.getLogger(f"boss-guard-{boss_at}-{attack_range}")
    capture = Capture()
    logger.addHandler(capture)
    logger.setLevel(logging.INFO)
    camera = SimpleNamespace(geometry=lambda: (0, 0), close=lambda: None)
    result = trial.run_trial(
        path,
        None,
        tmp_path / "run",
        3,
        logger,
        session_override=Session(),
        camera_factory=lambda *a: camera,
        supervisor=supervisor,
    )
    assert result["reason"] == "duration_limit"
    assert bool(calls) is casts
    assert ("scatter_held_for_boss" in capture.names) is not casts
