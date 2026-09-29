"""A route restart pause outside town jumps clear of monsters closing in.

2026-09-29 15:58: a failed restock travel left Suicide among four GiantApes
on the plain north of Ape City (1020), and the 20 s restart pause ran only
life care: it drank its last potion and died 8 s in.
"""

from types import SimpleNamespace as NS

import numpy as np
import pytest

from conquest import overnight
from conquest.navigation import TerrainMap

PLAIN = (593, 308)
TOWN = (554, 545)  # inside Ape City's saved boundary (515, 477, 593, 614)


def health(position, monsters, map_id=1020):
    return {
        "embedded_controls": {
            "life": {"map_id": map_id, "position": list(position), "current_hp": 300},
            "monsters": monsters,
        },
        "window": {"client_size": [1416, 876]},
    }


def ape(position, **fields):
    return {"name": "GiantApe", "position": list(position), **fields}


@pytest.fixture
def loop(monkeypatch):
    monkeypatch.setattr(
        "conquest.scene_input.memory_player_anchor", lambda observer, life: (708, 438)
    )
    loop = object.__new__(overnight.OvernightLoop)
    loop.terrain = TerrainMap(1020, 932, 932, np.zeros((932, 932), dtype=bool), "", (), ())
    loop.route = NS(town_anchor=TOWN)
    loop.care = NS(session=None, check=lambda h: None)
    loop.steps = []
    loop.stepper = NS(
        step_to=lambda target, expected_position: loop.steps.append(
            (tuple(target), tuple(expected_position))
        )
    )
    loop.events = []
    loop.record = lambda event, **fields: loop.events.append((event, fields))
    return loop


def test_field_restart_jumps_clear_of_an_adjacent_monster(loop):
    assert loop.evade_in_field(health(PLAIN, [ape((594, 309))]))
    [(target, expected)] = loop.steps
    assert expected == PLAIN
    assert max(abs(a - b) for a, b in zip(target, (594, 309))) >= 4
    assert loop.events[0][0] == "restart_evading"


@pytest.mark.parametrize(
    "position, monsters, map_id",
    [
        (TOWN, [ape((555, 546))], 1020),  # inside the town boundary
        (PLAIN, [ape((600, 308))], 1020),  # nearest monster 7 tiles away
        (PLAIN, [ape((594, 309), alive=False)], 1020),  # a corpse
        (PLAIN, [ape((594, 309))], 1002),  # not the route's terrain map
    ],
)
def test_no_jump_in_town_or_without_a_close_living_monster(loop, position, monsters, map_id):
    assert not loop.evade_in_field(health(position, monsters, map_id))
    assert loop.steps == [] and loop.events == []


def test_no_landing_inside_a_far_boss_clearance(loop):
    loop.route = NS(town_anchor=TOWN, king_clearance=15, elite_clearance=13)
    king = {"name": "MonkeyKing", "position": [PLAIN[0], PLAIN[1] - 20]}  # 20 north
    assert loop.evade_in_field(health(PLAIN, [ape((PLAIN[0], PLAIN[1] + 1)), king]))
    [(target, _)] = loop.steps
    assert max(abs(a - b) for a, b in zip(target, king["position"])) > 15


def test_a_boss_inside_its_clearance_calls_for_a_jump_away(loop):
    loop.route = NS(town_anchor=TOWN, king_clearance=15, elite_clearance=13)
    king = {"name": "MonkeyKing", "position": [PLAIN[0] + 3, PLAIN[1]]}
    assert loop.evade_in_field(health(PLAIN, [king]))
    [(target, _)] = loop.steps
    before = max(abs(a - b) for a, b in zip(PLAIN, king["position"]))
    assert max(abs(a - b) for a, b in zip(target, king["position"])) > before


def test_jumps_are_spaced(loop, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(overnight.time, "monotonic", lambda: clock[0])
    assert loop.evade_in_field(health(PLAIN, [ape((594, 309))]))
    clock[0] += 0.5
    assert not loop.evade_in_field(health(PLAIN, [ape((594, 309))]))
    clock[0] += overnight.FIELD_EVADE_SECONDS
    assert loop.evade_in_field(health(PLAIN, [ape((594, 309))]))
    assert len(loop.steps) == 2


def test_auto_restart_pause_evades(loop, monkeypatch):
    monkeypatch.setattr(overnight, "AUTO_RESTART_PAUSE_SECONDS", 0.05)
    monkeypatch.setattr(overnight.time, "sleep", lambda seconds: None)
    loop.check_stop = lambda: None
    loop.refresh = lambda: None
    loop.living = lambda: health(PLAIN, [ape((594, 309))])
    assert loop.auto_restart(ValueError("No traversable route between the endpoints"))
    assert loop.steps and loop.steps[0][1] == PLAIN
    assert [event for event, _ in loop.events][:2] == ["recovered_failure", "restart_evading"]
