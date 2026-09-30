"""A walk back to the hunting area that finds no step inside its travel
boundary redraws the boundary round the walk from where it stands.

Suicide 2026-09-30 01:52: the box was drawn round the walk from the Macaque
field (604, 611), [592, 320, 844, 623]. From (788, 445) a fresh plan to the
anchor (605, 335) swung up to y 308, patrol_step skipped every step
("Waiting for a traversable patrol step"), and the farmer stood on the east
road until nudged.
"""

import json
import logging
import sqlite3
from types import SimpleNamespace

import numpy as np
from trial_template import trial_template


def run_return(tmp_path, monkeypatch, fits, seconds=10):
    """Start outside the hunting box; the first landing stays inside the travel
    box but off the first walk; later steps come only once fits(box)."""
    import yaml, win32api
    from conquest import trial
    from conquest.capture import CaptureUnavailable
    from conquest.farmer_profile import CombatSpeed
    from conquest.memory_inventory import InventorySnapshot, Item
    from conquest.navigation import TerrainMap

    now = [10.0]
    moves = []
    position = [95, 150]
    config = trial_template("pheasant-foreground-trial.yaml")
    config.update(
        character="ReturnReplanTest",
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
                values = dict(name="ReturnReplanTest", position=list(position), max_hp=[100],
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

    boundaries = []

    def patrol(source, destination, boundary, chase=False):
        boundaries.append(tuple(boundary))
        if len(boundaries) == 1:
            position[:] = [90, 160]
            return (90, 160)
        if not fits(tuple(boundary), boundaries[0]):
            raise CaptureUnavailable("Waiting for a traversable patrol step")
        return (source[0] + 6, source[1])

    terrain = TerrainMap(1002, 300, 300, np.zeros((300, 300), dtype=bool), "", (), ())
    supervisor = SimpleNamespace(
        last_target=None,
        escape_monsters=(),
        recovery=SimpleNamespace(terrain=terrain),
        observe=lambda: {"health_ratio": 1.0, "waiting": False, "defending": False},
        memory_targets=lambda *a: [],
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
    trial.run_trial(path, None, tmp_path / "run", seconds, logging.getLogger("return-replan"),
                    session_override=Session(), camera_factory=lambda *a: camera,
                    supervisor=supervisor)
    rows = [
        (event, json.loads(payload))
        for event, payload in sqlite3.connect(tmp_path / "run" / "trial.sqlite3").execute(
            "select event, payload from events where event in "
            "('boundary_return_started', 'navigation_wait') order by time"
        )
    ]
    return rows, boundaries, moves


def test_boundary_return_replans_when_no_step_fits(tmp_path, monkeypatch):
    rows, boundaries, moves = run_return(
        tmp_path, monkeypatch, lambda box, first: box != first
    )
    starts = [payload for event, payload in rows if event == "boundary_return_started"]
    assert [event for event, _ in rows][:3] == [
        "boundary_return_started", "navigation_wait", "boundary_return_started"
    ]
    assert "replanned" not in starts[0]
    assert starts[1]["replanned"] == "no_step_inside" and starts[1]["position"] == [90, 160]
    assert tuple(starts[1]["travel_boundary"]) != tuple(starts[0]["travel_boundary"])
    # The walk goes on from the redrawn box.
    assert tuple(starts[1]["travel_boundary"]) in boundaries and len(moves) >= 2


def width(box):
    return box[2] - box[0]


def test_a_redraw_that_still_fits_no_step_widens_the_box(tmp_path, monkeypatch):
    # patrol_step plans round obstructions and boss zones, so its walk may
    # need more room than a box drawn round the plain walk.
    from conquest.navigation import TRAVEL_PADDING

    rows, boundaries, moves = run_return(
        tmp_path, monkeypatch, lambda box, first: width(box) >= width(first) + 2 * TRAVEL_PADDING
    )
    redraws = [p for event, p in rows if event == "boundary_return_started" and "replanned" in p]
    assert [p["padding"] for p in redraws] == [TRAVEL_PADDING, 2 * TRAVEL_PADDING]
    assert width(redraws[1]["travel_boundary"]) > width(redraws[0]["travel_boundary"])
    assert len(moves) >= 2


def test_redraws_are_spaced_and_the_widening_is_capped(tmp_path, monkeypatch):
    from conquest import trial
    from conquest.navigation import TRAVEL_PADDING

    rows, _, moves = run_return(tmp_path, monkeypatch, lambda box, first: False, seconds=20)
    redraws = [p for event, p in rows if event == "boundary_return_started" and "replanned" in p]
    assert moves == [moves[0]]  # only the first landing
    assert 4 <= len(redraws) <= 20 / trial.RETURN_REPLAN_WAIT_SECONDS + 1
    cap = (1 + trial.RETURN_REPLAN_WIDENINGS) * TRAVEL_PADDING
    assert max(p["padding"] for p in redraws) == cap
