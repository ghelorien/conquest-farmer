"""Loot yields to an overdue Scatter while a living target is in reach.

Toxic 2026-09-28 09:40: a chain of silver pickups and a terrain walk toward
one pile held casts for 17.75 s with seven WingedSnakes in reach.
"""

import logging

import pytest
from trial_template import trial_template


@pytest.mark.parametrize("target_at,casts_expected", [((427, 455), True), ((440, 455), False)])
def test_endless_loot_work_cannot_starve_jump_scatter(
    tmp_path, monkeypatch, target_at, casts_expected
):
    import yaml, win32api
    from types import SimpleNamespace
    from conquest import scatter_movement, trial
    from conquest.farmer_profile import CombatSpeed
    from conquest.memory_inventory import InventorySnapshot, Item
    from conquest.vision import Target

    now = [10.0]
    casts = []
    loot_calls = []
    config = trial_template("pheasant-foreground-trial.yaml")
    config.update(
        character="LootYieldTest",
        observation_mode="memory_only",
        kite_when_surrounded=False,
        adaptive_scatter=False,
        jump_scatter=True,
        attack_button="right",
        attack_range_tiles=8,
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
    # No jumps: this test is about loot versus casting only.
    monkeypatch.setattr(scatter_movement, "scatter_landing", lambda *a, **k: None)
    monkeypatch.setattr(
        scatter_movement, "wounded_group_in_range", lambda *a, **k: False
    )

    class Session:
        def request(self, operation, body=None):
            if operation == "health":
                return {"input_revision": 7, "window": {"hwnd": 1}}
            if operation == "sample":
                values = dict(
                    name="LootYieldTest",
                    position=[423, 455],
                    max_hp=[100],
                    kill_counter=[0],
                    level=[38],
                    map=[1002],
                )
                return {"fields": [{"name": k, "value": v} for k, v in values.items()]}
            assert operation == "foreground-click"
            casts.append(now[0])
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
                Item(2, 1050000, 200 - 2 * len(casts), 200, None),
                0,
                40,
            )

    target = Target("Pheasant", 600, 400, 1, 1, 1000, target_at, 100)

    def scan(*args):
        supervisor.scatter_scene_targets = (target,)
        return [target]

    def endless_silver(inventory, position, dispatch, valuables_only=False):
        loot_calls.append((now[0], valuables_only))
        # Always another silver pile to walk to; nothing valuable on the ground.
        return not valuables_only

    supervisor = SimpleNamespace(
        last_target=None,
        escape_monsters=(),
        scatter_scene_targets=(target,),
        recovery=SimpleNamespace(terrain=SimpleNamespace(width=1000, height=1000)),
        observe=lambda: {"health_ratio": 1.0, "waiting": False, "defending": False},
        memory_targets=scan,
        dispatch=lambda callback, **kwargs: callback(),
        loot_step=endless_silver,
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
    camera = SimpleNamespace(geometry=lambda: (0, 0), close=lambda: None)
    result = trial.run_trial(
        path,
        None,
        tmp_path / "run",
        6,
        logging.getLogger("loot-yield"),
        session_override=Session(),
        camera_factory=lambda *a: camera,
        supervisor=supervisor,
    )
    assert result["reason"] == "duration_limit"
    assert loot_calls  # loot still runs between casts
    if casts_expected:
        assert len(casts) >= 2
        assert all(
            b - a >= trial.LOOT_YIELD_SECONDS - 0.2 for a, b in zip(casts, casts[1:])
        )
        # An overdue cast still gives valuables their loot turn, silver none.
        assert any(valuables for _, valuables in loot_calls)
    else:
        assert casts == []  # nothing in reach: loot keeps its turn
        assert not any(valuables for _, valuables in loot_calls)
