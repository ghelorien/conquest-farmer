"""Failed-edge evidence survives a retreat without weakening the watchdog."""

from types import SimpleNamespace

import pytest

from conquest.overnight import OvernightLoop, OvernightStopped
from conquest.navigation import line_tiles


def rig(monkeypatch, *, stop_at_retreat=False):
    from conquest import city_travel, navigation, scene_input

    monkeypatch.setattr(city_travel, "service_role", lambda *a: None)
    monkeypatch.setattr(scene_input, "memory_player_anchor", lambda *a: (708, 438))
    # Terrain and input safety have separate native/geometry tests. This rig
    # supplies checked routes to isolate the evidence-retention state machine.
    monkeypatch.setattr(navigation, "clear_segment", lambda *a, **kw: True)
    monkeypatch.setattr(scene_input, "clear_route_point", lambda *a: True)
    source, failed, goal = (194, 255), (193, 254), (191, 249)
    position = list(source)
    plans, steps, events = [], [], []
    retreat = [(194, 255), (198, 257), (204, 259), (204, 247), (192, 247), goal]

    def planner(start, end, *, avoid=()):
        plans.append((start, frozenset(avoid)))
        if failed in avoid:
            return retreat[retreat.index(start) :]
        return line_tiles(start, failed) + line_tiles(failed, goal)[1:]

    def waypoint(terrain, path, *a, **kw):
        return (191, 252) if path[0] == source and path[1] == failed else path[1]

    monkeypatch.setattr(navigation, "travel_waypoint", waypoint)

    def living():
        if stop_at_retreat and tuple(position) == (204, 259):
            raise OvernightStopped("Manual stop")
        return {
            "window": {"client_size": [1416, 876]},
            "embedded_controls": {"life": {"position": list(position)}},
        }

    def step(target, *, expected_position):
        steps.append((expected_position, target))
        if target == (191, 252):
            return {"reached": False, "error": "Route movement stopped progressing"}
        if len(steps) > 8:
            pytest.fail("Retreat cleared failed edge and repeated the same corridor")
        position[:] = target
        return {"reached": True}

    loop = SimpleNamespace(
        terrain=SimpleNamespace(
            map_id=1002, travel_path=planner, straight_path=planner
        ),
        route=SimpleNamespace(supplies=SimpleNamespace(arrow_type=1050000)),
        record=lambda event, **kw: events.append(event),
        living=living,
        care=SimpleNamespace(check=lambda h: None, session=None),
        stepper=SimpleNamespace(step_to=step),
    )
    return loop, position, plans, steps, goal, failed


def test_retreat_keeps_failed_edge_until_genuine_route_progress(monkeypatch):
    loop, position, plans, steps, goal, failed = rig(monkeypatch)
    OvernightLoop._travel(loop, goal)
    assert position == list(goal)
    assert steps[:4] == [
        ((194, 255), (191, 252)),
        ((194, 255), (198, 257)),
        ((198, 257), (204, 259)),
        ((204, 259), (204, 247)),
    ]
    assert sum(target == (191, 252) for _, target in steps) == 1
    # The detour remains available. Only after reaching a closer onward tile
    # may obsolete exclusions be discarded; arriving still needs exact memory.
    assert not any(
        start == (204, 259) and failed not in avoid for start, avoid in plans
    )


def test_manual_stop_during_retreat_sends_no_next_step(monkeypatch):
    loop, position, plans, steps, goal, failed = rig(monkeypatch, stop_at_retreat=True)
    with pytest.raises(OvernightStopped, match="Manual stop"):
        OvernightLoop._travel(loop, goal)
    assert steps[-1] == ((198, 257), (204, 259))
