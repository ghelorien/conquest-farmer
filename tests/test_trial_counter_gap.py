import json
import logging
import sqlite3
from pathlib import Path
from types import SimpleNamespace
import pytest
import yaml
from conquest import trial
from conquest.farmer_profile import CombatSpeed
from conquest.memory_inventory import InventorySnapshot, Item
from trial_template import trial_template


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("unstable_confirmation", [False, True])
def test_counter_gap_is_excluded_without_losing_verified_totals(
    tmp_path, monkeypatch, enabled, unstable_confirmation
):
    import win32api

    now = [10.0]
    index = [0]
    current = [100]
    checks = [0]
    config = trial_template("pheasant-foreground-trial.yaml")
    config.update(
        character="CounterTest",
        observation_mode="memory_only",
        kite_when_surrounded=False,
        adaptive_scatter=False,
        jump_scatter=False,
        attack_button="right",
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
        trial, "load_combat_speed", lambda _: CombatSpeed(counter_gap_recovery=enabled)
    )

    class Session:
        def request(self, operation, body=None):
            if operation == "health":
                return {"input_revision": 7, "window": {"hwnd": 1}}
            assert operation == "sample"
            if len(body["fields"]) == 2:
                checks[0] += 1
                value = current[0] + int(unstable_confirmation and checks[0] == 1)
                return {
                    "fields": [
                        {"name": "name", "value": "CounterTest"},
                        {"name": "kill_counter", "value": [value]},
                    ]
                }
            values = [100, 102, 142, 142, 145]
            current[0] = values[min(index[0], len(values) - 1)]
            index[0] += 1
            values = dict(
                name="CounterTest",
                position=[423, 455],
                max_hp=[100],
                kill_counter=[current[0]],
                level=[18],
                map=[1002],
            )
            return {"fields": [{"name": k, "value": v} for k, v in values.items()]}

    class Inventory:
        def __init__(self, *args):
            pass

        def read(self):
            now[0] += 0.01
            return InventorySnapshot(
                now[0],
                now[0],
                (Item(1, 1000000, 1, 1, 0),),
                Item(2, 1050000, 200, 200, None),
                0,
                40,
            )

    supervisor = SimpleNamespace(
        last_target=None,
        recovery=SimpleNamespace(terrain=SimpleNamespace(width=1000, height=1000)),
        observe=lambda: {"health_ratio": 1.0, "waiting": False, "defending": False},
        memory_targets=lambda *a: [],
        loot_step=lambda *a: False,
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
        2,
        logging.getLogger("test"),
        session_override=Session(),
        camera_factory=lambda *a: camera,
        supervisor=supervisor,
    )
    with sqlite3.connect(tmp_path / "run" / "trial.sqlite3") as db:
        events = [
            (e, json.loads(p))
            for e, p in db.execute("select event,payload from events")
        ]
    kills = [p for e, p in events if e == "kill_verified"]
    gaps = [p for e, p in events if e == "kill_counter_gap"]
    assert result["reason"] == (
        "duration_limit" if enabled else "kill_counter_discontinuity"
    )
    assert sum(p["count"] for p in kills) == (5 if enabled else 2)
    assert len(gaps) == int(enabled)
    if enabled:
        assert gaps[0]["unverified_increment"] == 40 and gaps[0]["verified_total"] == 2
        assert checks[0] == (2 if unstable_confirmation else 1)
        assert kills[-1]["total"] == 5
