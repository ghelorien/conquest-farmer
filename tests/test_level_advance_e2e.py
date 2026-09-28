"""The farmer moves up a level zone once it reaches the top of its own.

User rule (2026-09-26): "once you hit 6 you go for the next one etc." A
character at the top level of its zone (Pheasants 1-6 at level 6) hunts the
next zone (Turtledoves), and so on up the saved routes.

Failure modes this module must catch (written before the implementation):

1. At the top level of its zone (6) the farmer keeps hunting Pheasants
   instead of moving to Turtledoves.
2. The farmer leaves a zone before its top level (for example at 5).
3. After moving up at the top level, the next check moves it back down
   (level 6 -> Pheasants again) or moves it a second time.
4. At the top of a zone whose next zone has no saved route (51 -> GiantApe),
   the farmer abandons its route, travels, or stops farming instead of
   continuing the current hunt. (36 -> Ratling had none until 2026-09-27;
   Bandit to Ratling is a same-map move without a scroll home.)
5. A route hold (a held Pheasant route, the overnight Bandit plan) is
   overridden by the top-of-zone rule.
6. A move to another map (26 -> Winged Snakes in Phoenix) happens without
   travelling there first, or a zone without a verified connection (Macaques
   in Ape City) is entered anyway.
7. Level 140, the top of the last zone, raises or selects a zone that does
   not exist.
8. The app is not told about the new route (no route_id control), so the app
   and the route process disagree about the route being hunted.

The end-to-end test drives the route process's level check through a whole
climb with the real presets, route library, towns and verified map
connections (only memory, input and travel are faked) and writes
``level-advance.json``; the scenario runs twice in separate roots and must
produce byte-identical artifacts.
"""

import json
from pathlib import Path

from conquest import leveling_routes, overnight, session_plan, world_travel
from conquest.overnight import OvernightLoop
from conquest.routes import RouteLibrary

CLIMB = (1, 5, 6, 6, 7, 10, 11, 15, 16, 20, 21, 25, 26, 30, 31, 35, 36, 40, 41)
CLIMB += (45, 46, 50, 51, 140)


class Farmer:
    """One live route process: memory level, map, controls and travel."""

    def __init__(self, monkeypatch, route_id, map_id):
        self.level = 1
        self.map_id = map_id
        self.acknowledged = route_id
        self.step = {}
        loop = OvernightLoop.__new__(OvernightLoop)
        loop.route = RouteLibrary().load(route_id)
        loop.auto_level = True
        loop.next_level_check = 0
        loop.last_level = 0
        loop.info = "worker"
        loop.phase = "hunting"
        loop.health = self.health
        loop.record = lambda event, **fields: self.step["events"].append(event)
        loop.stop_farm = lambda: self.step["calls"].append("stop_farm")
        loop.queue_route_optimization = lambda: self.step["calls"].append(
            "queue_route_optimization"
        )
        self.loop = loop
        monkeypatch.setattr(leveling_routes, "read_level", lambda *a: self.level)
        monkeypatch.setattr(world_travel, "travel_to_map", self.travel)
        monkeypatch.setattr(
            "conquest.return_scroll.return_to_town",
            lambda loop: self.step["calls"].append("return_to_town"),
        )
        monkeypatch.setattr(overnight, "request", self.request)
        monkeypatch.setattr(
            overnight, "read_status", lambda path: {"selected_route": self.acknowledged}
        )
        monkeypatch.setattr(overnight, "read_terrain", lambda *a: None)

    def health(self):
        return {"embedded_controls": {"life": {"map_id": self.map_id}}}

    def travel(self, loop, destination):
        self.step["calls"].append(f"travel_to_map:{destination}")
        self.map_id = destination

    def request(self, info, operation, body):
        self.step["calls"].append(f"{operation}:{json.dumps(body, sort_keys=True)}")
        if operation == "controls" and "route_id" in body:
            self.acknowledged = body["route_id"]

    def check(self, level):
        self.level = level
        self.loop.next_level_check = 0
        self.step = {"level": level, "events": [], "calls": []}
        changed = self.loop.select_level_route(self.health())
        return {
            **self.step,
            "changed": changed,
            "route": self.loop.route.id,
            "map_id": self.map_id,
            "app_route": self.acknowledged,
        }


def _hold(root, plan):
    path = Path(root) / "session-plan.json"
    session_plan.write_json(path, plan)
    return path


def _scenario(root, monkeypatch):
    root = Path(root)
    root.mkdir(parents=True)
    monkeypatch.setattr(session_plan, "PLAN", root / "no-plan.json")
    climb = Farmer(monkeypatch, "pheasant", 1002)
    steps = {"climb": [climb.check(level) for level in CLIMB]}

    monkeypatch.setattr(
        session_plan,
        "PLAN",
        _hold(
            root,
            {
                "active": True,
                "mode": "hold_route",
                "route_id": "pheasant",
                "upgrade_maps": [1002],
                "started_at": 123,
            },
        ),
    )
    held = Farmer(monkeypatch, "pheasant", 1002)
    steps["pheasant_hold"] = [held.check(level) for level in (5, 6, 7)]

    monkeypatch.setattr(
        session_plan,
        "PLAN",
        _hold(
            root,
            {
                "active": True,
                "route_id": "bandit",
                "upgrade_maps": [1002, 1011],
                "started_at": 124,
            },
        ),
    )
    night = Farmer(monkeypatch, "bandit", 1011)
    steps["overnight_bandit_hold"] = [night.check(level) for level in (35, 41, 46)]

    path = root / "level-advance.json"
    path.write_text(json.dumps(steps, indent=2, sort_keys=True), encoding="utf-8")
    return path


def _route(step):
    return (step["level"], step["changed"], step["route"], step["map_id"])


def test_level_advance_e2e(tmp_path, monkeypatch):
    first = _scenario(tmp_path / "pc-a", monkeypatch)
    second = _scenario(tmp_path / "pc-b", monkeypatch)
    assert first.read_bytes() == second.read_bytes()
    steps = json.loads(first.read_text(encoding="utf-8"))
    climb = steps["climb"]

    # Modes 1-3, 6 and 7: move exactly at each top level, never early, never back.
    assert [_route(s) for s in climb] == [
        (1, False, "pheasant", 1002),
        (5, False, "pheasant", 1002),
        (6, True, "turtledove", 1002),
        (6, False, "turtledove", 1002),
        (7, False, "turtledove", 1002),
        (10, False, "turtledove", 1002),
        (11, True, "robin", 1002),
        (15, False, "robin", 1002),
        (16, True, "apparition", 1002),
        (20, False, "apparition", 1002),
        (21, True, "poltergeist", 1002),
        (25, False, "poltergeist", 1002),
        (26, True, "wingedsnake", 1011),
        (30, False, "wingedsnake", 1011),
        # The Bandit bracket (and the Ratling bracket's economy refill) hunts
        # the southeast circuit: the whole-field route crosses every
        # BanditKing's ground (2026-09-28).
        (31, True, "bandit-southeast", 1011),
        (35, False, "bandit-southeast", 1011),
        (36, True, "ratling", 1011),
        (40, False, "ratling", 1011),
        (41, True, "firespirit", 1011),
        (45, False, "firespirit", 1011),
        (46, False, "firespirit", 1011),
        (50, False, "firespirit", 1011),
        (51, False, "firespirit", 1011),
        (140, False, "firespirit", 1011),
    ]
    by_level = {}
    for step in climb:
        by_level.setdefault(step["level"], step)
        # Mode 8: every move tells the app, and the app agrees with the route.
        assert step["app_route"] == step["route"]
    assert by_level[6]["calls"] == [
        "stop_farm",
        "travel_to_map:1002",
        'controls:{"route_id": "turtledove"}',
        "queue_route_optimization",
    ]
    # Mode 6: the Phoenix zone is reached by travelling there first, from
    # town (a scroll back; the Conductress walk from the field is too long).
    assert by_level[26]["calls"] == [
        "stop_farm",
        "return_to_town",
        "travel_to_map:1011",
        'controls:{"route_id": "wingedsnake"}',
        "queue_route_optimization",
    ]
    # Ratlings share the Bandits' map: no scroll home, no Conductress.
    assert by_level[36]["calls"] == [
        "stop_farm",
        "travel_to_map:1011",
        'controls:{"route_id": "ratling"}',
        "queue_route_optimization",
    ]
    # Modes 4 and 6: no saved route, or no verified connection, keeps hunting.
    for level in (46, 50, 51, 140):
        assert by_level[level]["calls"] == []
        assert "level_route_pending" in by_level[level]["events"]
    assert all("level_route_changed" in s["events"] for s in climb if s["changed"])

    # Mode 5: holds win over the top-of-zone rule.
    assert [_route(s) for s in steps["pheasant_hold"]] == [
        (5, False, "pheasant", 1002),
        (6, False, "pheasant", 1002),
        (7, False, "pheasant", 1002),
    ]
    assert [_route(s) for s in steps["overnight_bandit_hold"]] == [
        (35, False, "bandit", 1011),
        (41, False, "bandit", 1011),
        (46, False, "bandit", 1011),
    ]
    for hold in (steps["pheasant_hold"], steps["overnight_bandit_hold"]):
        assert "route_hold_active" in hold[0]["events"]
        assert all(step["calls"] == [] for step in hold)
