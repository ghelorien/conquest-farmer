"""A walk to a valuable within VALUABLE_RADIUS goes on past the hunting
boundary: the trial starts no boundary return while the supervisor's chase
holds, and returns as usual once it ends.

Alex 2026-09-29: "There should be a 25 tile radius for valuables".
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


@pytest.mark.parametrize("chasing", [True, False])
def test_a_valuable_chase_holds_the_boundary_return(tmp_path, monkeypatch, chasing):
    import yaml, win32api
    from conquest import trial
    from conquest.farmer_profile import CombatSpeed
    from conquest.memory_inventory import InventorySnapshot, Item
    from conquest.navigation import TerrainMap

    now = [10.0]
    loot_turns = []
    config = trial_template("pheasant-foreground-trial.yaml")
    config.update(
        character="ValuableWalkTest",
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
                # Five tiles west of the hunting boundary, walking to a Meteor.
                values = dict(name="ValuableWalkTest", position=[95, 150], max_hp=[100],
                              kill_counter=[0], level=[40], map=[1002])
                return {"fields": [{"name": k, "value": v} for k, v in values.items()]}
            assert operation == "foreground-click"
            return {}

    class Inventory:
        def __init__(self, *args):
            pass

        def read(self):
            now[0] += 0.01
            return InventorySnapshot(now[0], now[0], (Item(1, 1000000, 1, 1, 0),),
                                     Item(2, 1050000, 200, 200, None), 0, 40)

    def loot_step(inventory, position, dispatch, **kwargs):
        loot_turns.append(tuple(position))
        return chasing

    terrain = TerrainMap(1002, 300, 300, np.zeros((300, 300), dtype=bool), "", (), ())
    supervisor = SimpleNamespace(
        last_target=None,
        escape_monsters=(),
        recovery=SimpleNamespace(terrain=terrain),
        observe=lambda: {"health_ratio": 1.0, "waiting": False, "defending": False},
        memory_targets=lambda *a: [],
        dispatch=lambda callback, **kwargs: callback(),
        loot_step=loot_step,
        valuable_chase_holds=lambda position: chasing,
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
    logger = logging.getLogger(f"valuable-walk-{chasing}")
    capture = Capture()
    logger.addHandler(capture)
    logger.setLevel(logging.INFO)
    camera = SimpleNamespace(geometry=lambda: (0, 0), close=lambda: None)
    trial.run_trial(path, None, tmp_path / "run", 2, logger, session_override=Session(),
                    camera_factory=lambda *a: camera, supervisor=supervisor)
    assert ("boundary_return_started" in capture.names) is not chasing
    assert ("valuable_walk_outside_boundary" in capture.names) is chasing
    if chasing:
        # The loot turn kept running outside the boundary.
        assert loot_turns and set(loot_turns) == {(95, 150)}


def test_no_chase_while_already_returning():
    from conquest.trial import valuable_walk_outside

    holds = SimpleNamespace(valuable_chase_holds=lambda position: True)
    assert valuable_walk_outside(holds, False, (95, 150))
    # A boundary return under way is not interrupted by a valuable.
    assert not valuable_walk_outside(holds, True, (95, 150))
    # Supervisors without the chase (other farm modes) never hold it.
    assert not valuable_walk_outside(SimpleNamespace(), False, (95, 150))
    assert not valuable_walk_outside(None, False, (95, 150))
