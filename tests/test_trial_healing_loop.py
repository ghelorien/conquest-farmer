import logging
from types import SimpleNamespace

import pytest
import yaml

from conquest.capture import Frame
from conquest.memory_inventory import InventorySnapshot, Item
from conquest import trial
from trial_template import trial_template


@pytest.mark.parametrize("level_up", [False, True])
@pytest.mark.parametrize("initial_hp,expected_keys", [(0.399, 2), (0.4, 0), (0.8, 0)])
def test_f1_priority_and_repeat_until_above_threshold(
    tmp_path, monkeypatch, initial_hp, expected_keys, level_up
):
    import win32api

    now, keys = [10.0], []
    if level_up and expected_keys:
        expected_keys = 1
    profile = trial_template("pheasant-foreground-trial.yaml")
    profile["observation_mode"] = (
        "memory_only" if level_up else "legacy_visual"
    )  # Exercise the historical loop with fakes.
    profile["route"] = []
    path = tmp_path / "profile.yaml"
    path.write_text(yaml.safe_dump(profile))
    monkeypatch.setattr(trial.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(
        trial.time, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds)
    )
    monkeypatch.setattr(win32api, "GetAsyncKeyState", lambda key: 0)

    class Session:
        def __init__(self, *args):
            pass

        def request(self, name, args=None):
            if name == "health":
                return {"input_revision": 6, "window": {"hwnd": 1}}
            if name == "sample":
                values = dict(
                    name="Parasite",
                    position=[423, 455],
                    max_hp=[110 if level_up and keys else 100],
                    kill_counter=[0],
                    level=[7],
                    map=[1002],
                )
                return {"fields": [{"name": k, "value": v} for k, v in values.items()]}
            assert name == "foreground-key", (
                "Low health must preempt combat and movement"
            )
            assert args["vk"] == 112 and args["require_foreground"]
            keys.append(now[0])

    class Inventory:
        def __init__(self, *args):
            pass

        def read(self):
            now[0] += 0.1
            potions = tuple(
                Item(i, 1000000, 1, 1, i)
                for i in range(3 if level_up else 3 - len(keys))
            )
            return InventorySnapshot(
                now[0], now[0], potions, Item(99, 1050000, 200, 200, None), 0, 40
            )

    camera = SimpleNamespace(
        geometry=lambda: (0, 0),
        read=lambda: Frame(now[0], None, (0, 0)),
        close=lambda: None,
    )
    monkeypatch.setattr(trial, "WorkerPointerSession", Session)
    monkeypatch.setattr(trial, "MemoryInventoryReader", Inventory)
    monkeypatch.setattr(trial, "DesktopFrames", lambda *args: camera)
    monkeypatch.setattr(
        trial,
        "resolve_player",
        lambda *args: dict.fromkeys(
            ("name", "position", "max_hp", "kill_counter", "level", "map"), 1
        ),
    )
    monkeypatch.setattr(
        trial,
        "health_ratio",
        lambda frame, size: (
            initial_hp if not keys else (0.3995 if len(keys) == 1 else 0.9)
        ),
    )
    monkeypatch.setattr(trial, "targets", lambda *args: [])
    monkeypatch.setattr(trial, "nearby_drops", lambda *args: [])
    supervisor = None
    if level_up:

        def no_pixels(*args, **kwargs):
            pytest.fail("Level-up recovery must use memory only")

        camera.read = no_pixels
        monkeypatch.setattr(trial.cv2, "imread", no_pixels)
        supervisor = SimpleNamespace(
            recovery=SimpleNamespace(terrain=SimpleNamespace(width=1000, height=1000)),
            observe=lambda: {
                "health_ratio": 1.0 if keys else initial_hp,
                "waiting": False,
            },
            memory_targets=lambda *args: [],
            loot_step=lambda *args: False,
            dispatch=lambda callback, **kwargs: callback(),
        )
    result = trial.run_trial(
        path,
        "unused",
        tmp_path / "output",
        4,
        logging.getLogger("test"),
        supervisor=supervisor,
    )
    assert len(keys) == expected_keys
    assert result["verified_heals"] == (0 if level_up else expected_keys)
    assert result["reason"] == "duration_limit"
    if len(keys) > 1:
        assert keys[1] - keys[0] >= 1
