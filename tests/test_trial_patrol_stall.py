"""A hunt with no traversable patrol step walks back to the hunting anchor.

Suicide 2026-10-01 21:44-21:53 on the WingedSnakes: the idle search had
widened the box 12 tiles, the farmer stood at (279, 105) just outside the
field when it narrowed again, every patrol path failed ("Waiting for a
traversable patrol step", "Navigation blocked") and it stood for 9 minutes
until a route change moved it. Only a walk back (approaching) used to replan.
"""

import json
import logging
import sqlite3
from types import SimpleNamespace

import numpy as np
from trial_template import trial_template


def run_stall(tmp_path, monkeypatch, seconds=40, targets=lambda now: []):
    import yaml, win32api
    from conquest import trial
    from conquest.capture import CaptureUnavailable
    from conquest.farmer_profile import CombatSpeed
    from conquest.memory_inventory import InventorySnapshot, Item
    from conquest.navigation import TerrainMap
    from conquest.vision import Target

    now = [10.0]
    moves = []
    position = [190, 150]  # inside the hunting box, 40 tiles from the anchor
    config = trial_template("pheasant-foreground-trial.yaml")
    config.update(
        character="PatrolStallTest",
        observation_mode="memory_only",
        kite_when_surrounded=False,
        adaptive_scatter=False,
        jump_scatter=False,
        attack_button="right",
        attack_range_tiles=8,
        boundary=[100, 100, 200, 200],
        route=[[120, 120]],
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
                values = dict(name="PatrolStallTest", position=list(position), max_hp=[100],
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

    calls = []
    walk_started = []

    def patrol(source, destination, boundary, chase=False, alternatives=()):
        calls.append((tuple(source), tuple(destination), tuple(boundary)))
        if tuple(destination) != (150, 150):
            # Hunting (patrol point or widened search): every path fails.
            raise CaptureUnavailable("Waiting for a traversable patrol step")
        # Walking back to the anchor: six tiles toward it.
        walk_started.append(now[0])
        step = tuple(s + max(-6, min(6, d - s)) for s, d in zip(source, destination))
        position[:] = list(step)
        return step

    terrain = TerrainMap(1002, 300, 300, np.zeros((300, 300), dtype=bool), "", (), ())
    supervisor = SimpleNamespace(
        last_target=None,
        escape_monsters=(),
        recovery=SimpleNamespace(terrain=terrain),
        observe=lambda: {"health_ratio": 1.0, "waiting": False, "defending": False},
        memory_targets=lambda *a: [Target(*t) for t in targets(now[0])],
        dispatch=lambda callback, **kwargs: callback(),
        loot_step=lambda *a: False,
        player_projection=lambda: (tuple(position), (518, 396)),
        patrol_step=patrol,
        movement_failed=lambda *a: None,
        finish_target=lambda *a: None,
    )
    monkeypatch.setattr(trial, "MemoryInventoryReader", Inventory)
    monkeypatch.setattr(trial, "resolve_player", lambda *a: dict.fromkeys(
        ("name", "position", "max_hp", "kill_counter", "level", "map"), 1))
    camera = SimpleNamespace(geometry=lambda: (0, 0), close=lambda: None)
    trial.run_trial(path, None, tmp_path / "run", seconds, logging.getLogger("patrol-stall"),
                    session_override=Session(), camera_factory=lambda *a: camera,
                    supervisor=supervisor)
    rows = [
        (event, json.loads(payload), at)
        for event, payload, at in sqlite3.connect(tmp_path / "run" / "trial.sqlite3").execute(
            "select event, payload, time from events where event in ('navigation_wait', "
            "'patrol_stall_return', 'boundary_return_started', 'farming_area_reached') order by time"
        )
    ]
    return rows, moves, position, walk_started


def test_a_stalled_hunt_walks_back_to_the_anchor(tmp_path, monkeypatch):
    from conquest import trial

    rows, moves, position, _ = run_stall(tmp_path, monkeypatch)
    events = [event for event, _, _ in rows]
    assert events[:4] == [
        "navigation_wait", "patrol_stall_return", "boundary_return_started", "farming_area_reached"
    ]
    # The trial's own (simulated) clock: event rows carry wall-clock times.
    waited = rows[1][1]["waited"]
    assert trial.PATROL_STALL_RETURN_SECONDS <= waited <= trial.PATROL_STALL_RETURN_SECONDS + 2
    start = rows[2][1]
    assert start["position"] == [190, 150] and start["destination"] == [150, 150]
    # It walked (not just declared arrival inside the box) to the anchor.
    reached = rows[3][1]["position"]
    assert max(abs(reached[0] - 150), abs(reached[1] - 150)) <= 3 and len(moves) >= 5


def test_shooting_between_failed_steps_restarts_the_stall_clock(tmp_path, monkeypatch):
    """A busy field: a failed patrol step, then targets for a few seconds
    with no patrol move, then failures again. The wait counts from the last
    attack, not from the first failure."""
    from conquest import trial

    rows, moves, position, walk_started = run_stall(
        tmp_path, monkeypatch, seconds=60,
        targets=lambda now: [("Pheasant", 600, 400, 1)] if 16 <= now < 20 else [],
    )
    attacks = sqlite3.connect(tmp_path / "run" / "trial.sqlite3").execute(
        "select count(*) from events where event = 'attack_attempt'"
    ).fetchone()[0]
    assert attacks >= 1
    events = [event for event, _, _ in rows]
    assert "patrol_stall_return" in events
    # Shots ended at about 20 s (the clock starts at 10 s): no walk back
    # before 20 + PATROL_STALL_RETURN_SECONDS.
    assert walk_started and walk_started[0] >= 20 + trial.PATROL_STALL_RETURN_SECONDS - 1