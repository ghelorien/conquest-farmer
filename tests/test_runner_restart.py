"""A combat runner stopped by an observation or input error restarts in place.

Live 2026-09-28 (Toxic): an unverified heal (00:44) and a stale reload frame
(02:03) stopped the runner; the route's own restart took 20-40 s and Toxic
died standing among the monsters both times.
"""

from types import SimpleNamespace

from conquest import overnight
from conquest.overnight import OvernightLoop

NOTE = "Farm runner stopped: observation_or_input_failure: Reload observation expired"


def loop_with(monkeypatch):
    loop = OvernightLoop.__new__(OvernightLoop)
    calls = []
    loop.info = "worker.json"
    loop.route = SimpleNamespace(monster_type_ids=(6, 65))
    loop.record = lambda event, **fields: calls.append(("record", event))
    loop.stop_farm = lambda: calls.append(("stop_farm",))
    monkeypatch.setattr(
        overnight, "request", lambda info, name, body: calls.append((name, body))
    )
    now = [1000.0]
    monkeypatch.setattr(overnight.time, "monotonic", lambda: now[0])
    return loop, calls, now


def health(dead=False):
    return {"life": {"dead_candidate": dead}, "control": {"note": NOTE}}


def test_a_runner_error_restarts_combat_the_way_a_hunt_starts(monkeypatch):
    loop, calls, _ = loop_with(monkeypatch)
    assert loop.restart_runner(health())
    assert calls == [
        ("record", "runner_restarted"),
        ("stop_farm",),
        ("controls", {"enabled": True, "target_type_ids": [6, 65], "target_ids": []}),
    ]


def test_restarts_are_budgeted_and_never_touch_a_dead_character(monkeypatch):
    loop, calls, now = loop_with(monkeypatch)
    for _ in range(overnight.RUNNER_RESTARTS):
        assert loop.restart_runner(health())
        now[0] += 10
    # The budget is spent: the route restarts as before.
    assert not loop.restart_runner(health())
    now[0] += overnight.RUNNER_RESTART_WINDOW
    assert loop.restart_runner(health())
    # Death recovery owns a dead character; no life reading is no evidence.
    calls.clear()
    now[0] += overnight.RUNNER_RESTART_WINDOW
    assert not loop.restart_runner(health(dead=True))
    assert not loop.restart_runner({"life": None, "control": {"note": NOTE}})
    assert calls == []
