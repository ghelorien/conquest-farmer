import logging
from types import SimpleNamespace

import pytest
import yaml

from conquest import trial
from conquest.capture import CaptureUnavailable, Frame
from conquest.memory_inventory import InventorySnapshot, Item
from conquest.vision import Target


def test_manual_mouse_kills_are_excluded_without_stopping_farming(
    tmp_path, monkeypatch
):
    import win32api

    now, samples = [10.0], [0]
    profile = yaml.safe_load(open("profiles/pheasant-foreground-trial.yaml"))
    profile.update(
        observation_mode="memory_only",
        route=[],
        loot_allowlist=[],
        healing_enabled=False,
    )
    path = tmp_path / "profile.yaml"
    path.write_text(yaml.safe_dump(profile))
    monkeypatch.setattr(trial.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(trial.time, "sleep", lambda t: now.__setitem__(0, now[0] + t))
    monkeypatch.setattr(win32api, "GetAsyncKeyState", lambda _: 0)

    class Session:
        def request(self, operation, body=None):
            if operation == "health":
                return {"input_revision": 7, "window": {"hwnd": 1}}
            assert operation == "sample"
            samples[0] += 1
            values = dict(
                name="Parasite",
                position=[423, 455],
                max_hp=[100],
                kill_counter=[
                    0 if samples[0] == 1 else 100 if samples[0] == 2 else 101
                ],
                level=[12],
                map=[1002],
            )
            return {"fields": [{"name": k, "value": v} for k, v in values.items()]}

    class Inventory:
        def __init__(self, *args):
            pass

        def read(self):
            now[0] += 0.1
            return InventorySnapshot(
                now[0],
                now[0],
                (Item(1, 1000000, 1, 1, 0),),
                Item(2, 1050000, 100, 500, None),
                0,
                40,
            )

    paused = [False]
    observed = [False]

    def geometry():
        if observed[0] and not paused[0]:
            paused[0] = True
            raise CaptureUnavailable(
                "Mouse control is yours; farming resumes after 2 seconds idle"
            )
        return (0, 0)

    def no_pixels(*args, **kwargs):
        pytest.fail("Memory-only regression")

    camera = SimpleNamespace(geometry=geometry, read=no_pixels, close=lambda: None)
    monkeypatch.setattr(trial, "MemoryInventoryReader", Inventory)
    monkeypatch.setattr(
        trial,
        "resolve_player",
        lambda *args: dict.fromkeys(
            ("name", "position", "max_hp", "kill_counter", "level", "map"), 1
        ),
    )

    def targets(*args):
        observed[0] = True
        return []

    supervisor = SimpleNamespace(
        recovery=SimpleNamespace(terrain=SimpleNamespace(width=1000, height=1000)),
        observe=lambda: {"health_ratio": 1, "waiting": False},
        memory_targets=targets,
        loot_step=lambda *args: False,
        finish_target=lambda reason: None,
    )
    result = trial.run_trial(
        path,
        None,
        tmp_path / "run",
        2,
        logging.getLogger("test"),
        session_override=Session(),
        camera_factory=lambda *args: camera,
        supervisor=supervisor,
    )
    assert paused[0] and samples[0] >= 3
    assert result["reason"] == "duration_limit"
    assert result["confirmed_kills"] == 1


@pytest.mark.parametrize("defending", [False, True])
@pytest.mark.parametrize(
    "memory_only,monster",
    [(False, "Pheasant"), (True, "Pheasant"), (True, "Apparition")],
)
@pytest.mark.parametrize(
    "failure", [CaptureUnavailable, ValueError, "expired_observation"]
)
def test_focus_loss_at_dispatch_reobserves_but_geometry_failure_stops(
    tmp_path, monkeypatch, failure, memory_only, monster, defending
):
    import win32api

    now, calls, seen = [10.0], [], []
    profile = yaml.safe_load(open("profiles/pheasant-foreground-trial.yaml"))
    profile.update(
        monster=monster,
        observation_mode="memory_only" if memory_only else "legacy_visual",
        route=[],
        loot_allowlist=[],
        healing_enabled=False,
    )
    path = tmp_path / "profile.yaml"
    path.write_text(yaml.safe_dump(profile))
    monkeypatch.setattr(trial.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(trial.time, "sleep", lambda t: now.__setitem__(0, now[0] + t))
    monkeypatch.setattr(win32api, "GetAsyncKeyState", lambda _: 0)

    class Session:
        def request(self, operation, body=None):
            if operation == "health":
                return {"input_revision": 7, "window": {"hwnd": 1}}
            if operation == "sample":
                values = dict(
                    name="Parasite",
                    position=[423, 455],
                    max_hp=[100],
                    kill_counter=[int(len(calls) > 1)],
                    level=[12],
                    map=[1002],
                )
                return {
                    "fields": [
                        {"name": key, "value": value} for key, value in values.items()
                    ]
                }
            assert operation == "foreground-click"
            calls.append(body["point"])
            if len(calls) == 1 and failure != "expired_observation":
                raise failure("Window unavailable before button down")
            return {}

    class Inventory:
        def __init__(self, *args):
            pass

        def read(self):
            now[0] += 0.1
            return InventorySnapshot(
                now[0],
                now[0],
                (Item(1, 1000000, 1, 1, 0),),
                Item(2, 1050000, 100, 500, None),
                0,
                40,
            )

    camera = SimpleNamespace(
        geometry=lambda: (0, 0),
        read=lambda: Frame(now[0], None, (0, 0)),
        close=lambda: None,
    )
    monkeypatch.setattr(trial, "MemoryInventoryReader", Inventory)
    monkeypatch.setattr(
        trial,
        "resolve_player",
        lambda *args: dict.fromkeys(
            ("name", "position", "max_hp", "kill_counter", "level", "map"), 1
        ),
    )
    monkeypatch.setattr(trial, "health_ratio", lambda *args: 1)
    monkeypatch.setattr(trial.cv2, "imwrite", lambda *args: True)

    def observations(*args):
        seen.append(now[0])
        return [Target(monster, 800 + 10 * len(seen), 450, 1)] if len(seen) <= 2 else []

    monkeypatch.setattr(trial, "targets", observations)
    if failure == "expired_observation":
        choose = trial.choose_target

        def slow_first_selection(*args):
            target = choose(*args)
            if target and len(seen) == 1:
                now[0] += 0.4
            return target

        monkeypatch.setattr(trial, "choose_target", slow_first_selection)
    supervisor = None
    if memory_only:

        def no_pixels(*args, **kwargs):
            pytest.fail(
                "Hosted memory combat must not capture, load, match or save images"
            )

        camera.read = no_pixels
        monkeypatch.setattr(trial, "targets", no_pixels)
        monkeypatch.setattr(trial, "health_ratio", no_pixels)
        monkeypatch.setattr(trial.cv2, "imread", no_pixels)
        monkeypatch.setattr(trial.cv2, "imwrite", no_pixels)
        monkeypatch.setattr(trial, "InventoryReader", no_pixels)
        monkeypatch.setattr(trial, "nearby_drops", no_pixels)
        monkeypatch.setattr(trial, "revive_button", no_pixels)
        supervisor = SimpleNamespace(
            last_target=None,
            recovery=SimpleNamespace(terrain=SimpleNamespace(width=1000, height=1000)),
            observe=lambda: {
                "health_ratio": 1,
                "waiting": False,
                "defending": defending,
            },
            memory_targets=observations,
            loot_step=lambda *args: False,
            finish_target=lambda reason: None,
            dispatch=lambda callback, **kwargs: callback(),
        )
    result = trial.run_trial(
        path,
        None,
        tmp_path / "run",
        4,
        logging.getLogger("test"),
        session_override=Session(),
        camera_factory=lambda *args: camera,
        supervisor=supervisor,
    )
    if failure == "expired_observation":
        assert result["reason"] == (
            "duration_limit" if memory_only else "observation_or_input_failure"
        )
        assert calls == ([[820, 450]] if memory_only else [])
        assert result["attack_attempts"] == int(memory_only)
        if memory_only:
            assert seen[1] > seen[0]
    elif failure is CaptureUnavailable:
        assert result["reason"] == "duration_limit"
        assert calls == [[810, 450], [820, 450]]
        assert seen[1] > seen[0]
        assert result["attack_attempts"] == result["confirmed_kills"] == 1
    else:
        assert result["reason"] == "observation_or_input_failure"
        assert calls == [[810, 450]]
        assert result["attack_attempts"] == 0


def test_three_failed_moves_replan_and_combat_continues_without_switching_off(
    tmp_path, monkeypatch
):
    import win32api
    import sqlite3

    now = [10.0]
    failures = []
    clicks = []
    profile = yaml.safe_load(open("profiles/pheasant-foreground-trial.yaml"))
    profile.update(
        observation_mode="memory_only",
        route=[[435, 455]],
        loot_allowlist=[],
        healing_enabled=False,
        client_size=[1036, 793],
        player_anchor=[518, 396],
    )
    path = tmp_path / "profile.yaml"
    path.write_text(yaml.safe_dump(profile))
    monkeypatch.setattr(trial.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(trial.time, "sleep", lambda t: now.__setitem__(0, now[0] + t))
    monkeypatch.setattr(win32api, "GetAsyncKeyState", lambda _: 0)

    class Session:
        def request(self, operation, body=None):
            if operation == "health":
                return {"input_revision": 7, "window": {"hwnd": 1}}
            if operation == "sample":
                values = dict(
                    name="Parasite",
                    position=[423, 455],
                    max_hp=[100],
                    kill_counter=[0],
                    level=[12],
                    map=[1002],
                )
                return {"fields": [{"name": k, "value": v} for k, v in values.items()]}
            assert operation == "foreground-click"
            clicks.append(body)

    class Inventory:
        def __init__(self, *args):
            pass

        def read(self):
            now[0] += 0.01
            return InventorySnapshot(
                now[0],
                now[0],
                (Item(1, 1000000, 1, 1, 0),),
                Item(2, 1050000, 100, 500, None),
                0,
                40,
            )

    def no_pixels(*args, **kwargs):
        pytest.fail("Movement recovery must use memory only")

    camera = SimpleNamespace(
        geometry=lambda: (0, 0), read=no_pixels, close=lambda: None
    )
    monkeypatch.setattr(trial, "MemoryInventoryReader", Inventory)
    monkeypatch.setattr(
        trial,
        "resolve_player",
        lambda *args: dict.fromkeys(
            ("name", "position", "max_hp", "kill_counter", "level", "map"), 1
        ),
    )
    monkeypatch.setattr(trial.cv2, "imread", no_pixels)
    monkeypatch.setattr(trial.cv2, "imwrite", no_pixels)
    supervisor = SimpleNamespace(
        last_target=None,
        recovery=SimpleNamespace(terrain=SimpleNamespace(width=1000, height=1000)),
        observe=lambda: {"health_ratio": 1, "waiting": False},
        memory_targets=lambda *args: (
            [Target("Pheasant", 600, 400, 1)] if len(failures) >= 3 else []
        ),
        patrol_step=lambda *args, **kwargs: (435, 455),
        movement_failed=lambda position, destination: failures.append(
            (position, destination)
        ),
        loot_step=lambda *args: False,
        finish_target=lambda reason: None,
        dispatch=lambda callback, **kwargs: callback(),
    )
    result = trial.run_trial(
        path,
        None,
        tmp_path / "run",
        9,
        logging.getLogger("test"),
        session_override=Session(),
        camera_factory=lambda *args: camera,
        supervisor=supervisor,
    )
    assert result["reason"] == "duration_limit" and len(failures) == 3
    assert result["attack_attempts"] >= 1
    events = (
        sqlite3.connect(tmp_path / "run" / "trial.sqlite3")
        .execute("select event from events")
        .fetchall()
    )
    assert events.count(("movement_recovery",)) == 3


@pytest.mark.parametrize("slow_plan", [False, True])
@pytest.mark.parametrize("position", [(603, 576), (644, 570), (632, 575)])
def test_restarted_native_session_replans_from_memory_not_old_approach(
    tmp_path, monkeypatch, position, slow_plan
):
    import win32api
    import numpy as np
    import sqlite3, json
    from conquest.navigation import TerrainMap, native_waypoint

    now = [10.0]
    clicks = []
    profile = yaml.safe_load(open("profiles/pheasant-foreground-trial.yaml"))
    profile.update(
        observation_mode="memory_only",
        route=[[632, 558]],
        loot_allowlist=[],
        healing_enabled=False,
        client_size=[1036, 793],
        player_anchor=[518, 396],
        hunting_anchor=[644, 570],
        boundary=[620, 546, 668, 594],
        approach_route=[[580, 576], [644, 576], [644, 570]],
        approach_boundary=[568, 558, 656, 588],
    )
    path = tmp_path / "profile.yaml"
    path.write_text(yaml.safe_dump(profile))
    monkeypatch.setattr(trial.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(trial.time, "sleep", lambda t: now.__setitem__(0, now[0] + t))
    monkeypatch.setattr(win32api, "GetAsyncKeyState", lambda _: 0)

    class Session:
        def request(self, operation, body=None):
            if operation == "health":
                return {"input_revision": 7, "window": {"hwnd": 1}}
            if operation == "sample":
                values = dict(
                    name="Parasite",
                    position=position,
                    max_hp=[100],
                    kill_counter=[0],
                    level=[17],
                    map=[1002],
                )
                return {"fields": [{"name": k, "value": v} for k, v in values.items()]}
            assert operation == "foreground-click"
            clicks.append(body)

    class Inventory:
        def __init__(self, *args):
            pass

        def read(self):
            now[0] += 0.01
            return InventorySnapshot(
                now[0],
                now[0],
                (Item(1, 1000000, 1, 1, 0),),
                Item(2, 1050000, 100, 500, None),
                0,
                40,
            )

    def no_pixels(*args, **kwargs):
        pytest.fail("Restart routing must use memory only")

    camera = SimpleNamespace(
        geometry=lambda: (0, 0), read=no_pixels, close=lambda: None
    )
    monkeypatch.setattr(trial, "MemoryInventoryReader", Inventory)
    monkeypatch.setattr(
        trial,
        "resolve_player",
        lambda *args: dict.fromkeys(
            ("name", "position", "max_hp", "kill_counter", "level", "map"), 1
        ),
    )
    monkeypatch.setattr(trial.cv2, "imread", no_pixels)
    terrain = TerrainMap(
        1002, 1000, 1000, np.zeros((1000, 1000), dtype=bool), "", (), ()
    )
    destinations = []

    def patrol(source, destination, boundary, **kwargs):
        if slow_plan and not destinations:
            now[0] += 0.5
        destinations.append(destination)
        return native_waypoint(terrain.path(source, destination))

    supervisor = SimpleNamespace(
        last_target=None,
        recovery=SimpleNamespace(terrain=terrain),
        observe=lambda: {"health_ratio": 1, "waiting": False},
        memory_targets=lambda *args: [],
        patrol_step=patrol,
        movement_failed=lambda *args: None,
        loot_step=lambda *args: False,
        dispatch=lambda callback, **kwargs: callback(),
    )
    result = trial.run_trial(
        path,
        None,
        tmp_path / "run",
        1,
        logging.getLogger("test"),
        session_override=Session(),
        camera_factory=lambda *args: camera,
        supervisor=supervisor,
    )
    assert result["reason"] == "duration_limit" and clicks
    assert destinations[0] != (580, 576)
    events = (
        sqlite3.connect(tmp_path / "run" / "trial.sqlite3")
        .execute("select payload from events where event='boundary_return_started'")
        .fetchall()
    )
    if position == (603, 576):
        assert json.loads(events[0][0])["position"] == [603, 576]
        assert destinations[0] == (644, 570)
    else:
        assert not events and destinations[0] == (632, 558)
