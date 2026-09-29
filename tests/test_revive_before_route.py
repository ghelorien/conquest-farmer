"""A route (re)started on a dead farmer revives it with Farming Off first.

2026-09-29 11:51-12:37 (Toxic, Ape City): a deploy relaunched the route
controller on a dead farmer. living() waits forever while Farming is On
(travel care revives only with Farming Off), and the app cannot start
farming on a dead archer, so nothing pressed Revive until an operator
toggled Farming Off. The log stayed silent the whole time.
"""

from types import SimpleNamespace

from conquest import overnight
from conquest.overnight import OvernightLoop


def loop_with(life_states, *, farming_on=False):
    calls = []
    lives = iter(life_states)
    current = {"life": next(lives)}

    def health():
        calls.append("health")
        return {
            "embedded_controls": {
                "life": current["life"],
                "control": {"enabled": farming_on},
                "observed_at": overnight.time.time(),
            },
            "window": {"foreground": 1, "root_hwnd": 1, "minimized": False},
            "target": {"pid": 1},
        }

    loop = OvernightLoop.__new__(OvernightLoop)
    loop.health = health
    loop.record = lambda event, **fields: calls.append(event)
    loop.stop_farm = lambda: calls.append("stop_farm")

    def living():
        calls.append("living")
        current["life"] = next(lives)
        return health()

    loop.living = living
    return loop, calls


DEAD = {"dead_candidate": True, "position": [602, 278]}
ALIVE = {"dead_candidate": False, "position": [566, 565]}


def test_a_dead_start_turns_farming_off_before_waiting_for_life():
    loop, calls = loop_with([DEAD, ALIVE])
    assert loop.revive_before_route() is True
    assert calls[:4] == ["health", "revive_before_route", "stop_farm", "living"]


def test_a_living_start_changes_nothing():
    loop, calls = loop_with([ALIVE])
    assert loop.revive_before_route() is False
    assert calls == ["health"]


def test_living_waits_on_a_dead_farmer_with_farming_on_say_so(monkeypatch):
    events = []
    states = iter([dict(DEAD), dict(DEAD), dict(ALIVE)])
    now = [100.0]

    def health():
        life = next(states)
        return {
            "embedded_controls": {
                "life": {**life, "timestamp": now[0]},
                "control": {"enabled": True},
                "observed_at": overnight.time.time(),
            },
            "window": {"foreground": 1, "root_hwnd": 1, "minimized": False},
        }

    loop = OvernightLoop.__new__(OvernightLoop)
    loop.health = health
    loop.focus = lambda h: True
    loop.record = lambda event, **fields: events.append(event)
    loop.care = SimpleNamespace(check=lambda h: events.append("care"))
    monkeypatch.setattr(overnight.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(overnight.time, "monotonic", lambda: now[0])
    loop.living()
    # Farming On: no travel-care revive, one explicit wait event (rate-limited).
    assert events == ["living_wait_dead_farming_on"]
