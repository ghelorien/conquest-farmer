"""End-to-end: hunting with a large merchant backlog -> Phoenix city parking ->
successive exact merchant listing grants -> cap or empty queue -> hunting.

Real code under test: OvernightLoop.hunt/stop_farm/travel/_travel, the
merchants.handoff.service_window hook, merchants.town_batch, safe_reload.park
(require_city), city_travel.city_for with the saved profiles/cities.json, the
saved profiles/merchant-deliveries.json policy, the bandit route profile,
WorkWindows and the route's events.jsonl/status.json writers.

Fakes exist only at process boundaries:
- game process: conquest.worker.request (health, controls, bag reads, the
  in-client farm runner), the movement stepper and TravelCare adapters that
  send input, the memory draw-anchor read, and the installed terrain file (an
  open synthetic grid that carries the saved Phoenix terrain hash);
- merchant app: conquest.merchants.bridge.request, which applies the same
  grant admission rules as the real merchant UI and lists one item 17 seconds
  into a grant that had at least 38 seconds remaining;
- desktop app state file: reports/desktop-farming/app-state.json.

Failure modes this scenario must rule out. The list was written before the
service_window hook and this test; see test_town_batch_refill.py for the
isolated refusal/stop cases.
 F1  A batch starts without a backlog at or above the policy threshold.
 F2  The farmer yields input before verified quiet city parking.
 F3  Field parking is attempted although the batch was admitted.
 F4  Two merchant grants overlap, or a grant is issued before the previous
     release was confirmed.
 F5  A grant exceeds the 45-second listing scope or the batch cap.
 F6  The farmer sends movement or controls input while a merchant holds input.
 F7  A grant is issued without a fresh farmer safety observation (alive, city
     spot, no threat, no damage) immediately before it.
 F8  Only one merchant is served while the other has a backlog.
 F9  The batch never ends (no cap / empty-queue exit).
 F10 Farming is not re-enabled after the batch, or the farmer never returns
     to the hunting boundary.
 F11 A second merchant window starts immediately after the batch (no cooldown).
 F12 Downtime is invisible: batch start/finish events with counts and
     durations are missing from events.jsonl.
 F13 The trace is not repeatable on the deterministic simulated clock.

The test writes <tmp>/<scenario>/town-batch-e2e-trace.json (merchant
commands, farmer inputs, pre-grant safety observations and route events),
re-reads it from disk to assert on it, and runs the scenario twice to prove
the artifact is byte-identical.
"""

import copy
import ctypes
import json
import shutil
import time
from pathlib import Path
from types import SimpleNamespace as NS

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[1]
START = 1_790_000_000.0
TARGET = {"pid": 4242, "creation_time_100ns": 17}
CITY_SPOT = (191, 249)
LISTING_SECONDS = 17
MERCHANT_TICK = 1.0


def phoenix_sha():
    cities = json.loads((REPO / "profiles/cities.json").read_text(encoding="utf-8"))
    return next(c for c in cities["cities"] if c["map_id"] == 1011)["terrain_sha256"]


class Clock:
    def __init__(self):
        self.now = START

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += max(0.0, seconds)


class Trace:
    def __init__(self, clock):
        self.clock, self.rows = clock, []

    def add(self, kind, **fields):
        self.rows.append(
            {"t": round(self.clock.now - START, 3), "kind": kind, **fields}
        )


class Merchants:
    """Merchant app at the bridge boundary; the listing engine is simulated."""

    def __init__(self, clock, trace, game, backlog):
        self.clock, self.trace, self.game = clock, trace, game
        self.backlog = dict(backlog)
        self.names = sorted(backlog)
        self.key = None
        self.grant = None
        self.turn = 0
        self.request_after = START + 30  # Hunting first; the refill comes due.
        self.proof = {}
        self.listed = {name: 0 for name in self.names}
        self.blocker = {name: "waiting_farmer_handoff" for name in self.names}

    def _request(self):
        waiting = [n for n in self.names if self.backlog[n] > 0]
        if waiting and not self.key and not self.grant:
            if self.clock.now >= self.request_after:
                name = waiting[self.turn % len(waiting)]
                self.turn += 1
                self.key = f"merchant-refill:{name}:{round(self.clock.now * 1000)}"
                self.blocker[name] = "waiting_farmer_handoff"

    def _advance(self):
        grant = self.grant
        if not grant or grant["listed"] or not grant["can_list"]:
            return
        if self.clock.now - grant["at"] < LISTING_SECONDS:
            return
        name = grant["character"]
        self.backlog[name] -= 1
        self.listed[name] += 1
        grant["listed"] = True
        self.proof[name] = {
            "request_id": f"booth-list1078-refill-{name}-{self.listed[name]}",
            "verified_at": self.clock.now,
            "character": name,
        }
        self.blocker[name] = "listing_work_budget_insufficient"
        self.trace.add("merchant_listing_verified", character=name)

    def status(self):
        self._advance()
        self._request()
        characters = {}
        for name in self.names:
            characters[name] = {
                "connected": True,
                "pending": [],
                "needs_attention": None,
                "manual_input_fence": False,
                "shop_return": None,
                "recovery_safety": None,
                "qualification": {"foreground_open_booth_listing_1078": True},
                "refill": {
                    "enabled": True,
                    "pending": True,
                    "listing1078_request": None,
                    "last_verified_listing": self.proof.get(name),
                },
                # An emptied queue completes its check (no waiting blocker).
                "foreground_refill_1078": {
                    "blocker": self.blocker[name],
                    "eligible_backlog": self.backlog[name],
                    "backlog_observed_at": self.clock.now,
                }
                if self.backlog[name]
                else {"state": "capacity_checked"},
            }
        return {
            "characters": characters,
            "handoff_requested": self.key,
            "input_owner": None,
            "handoff_active": self.grant is not None,
            "handoff_granted": self.grant is not None,
            "host_request": None,
            "manual_handoff": None,
            "manual_sessions": [],
            "manual_farmer": {"input_fenced": False},
        }

    def __call__(self, body):
        from conquest.merchants.bridge import MerchantRejected

        action = body["action"]
        if action == "status":
            return self.status()
        if action == "handoff-grant":
            control = self.game.control
            remaining = body["expires_at"] - self.clock.now
            # Same admission as the merchant UI and listing validate_grant.
            if (
                body["request_id"] != self.key
                or self.grant is not None
                or body.get("scope") != "listing_1078"
                or body.get("character") != self.key.split(":")[1]
                or body["safe"] is not True
                or body["revision"] != control["revision"]
                or control["enabled"]
                or control["paused"]
                or not 0 < remaining <= 45
                or not self.game.parked_safely()
            ):
                raise MerchantRejected("Farmer must grant a current safe handoff")
            observation = self.game.last_observation
            self.grant = {
                "character": body["character"],
                "at": self.clock.now,
                "expires_at": body["expires_at"],
                "listed": False,
                "can_list": remaining >= 38,
            }
            self.trace.add(
                "merchant_grant",
                character=body["character"],
                seconds=round(remaining, 3),
                expires_t=round(body["expires_at"] - START, 3),
                observation=observation,
            )
            return {"granted": True}
        if action == "handoff-release":
            if body["request_id"] != self.key:
                raise MerchantRejected("Handoff request mismatch")
            self._advance()
            self.trace.add("merchant_release", character=self.key.split(":")[1])
            self.grant = None
            self.key = None
            self.request_after = self.clock.now + MERCHANT_TICK
            return {"released": True}
        raise AssertionError(action)


class Game:
    """Farmer client and its embedded worker at the game-process boundary."""

    def __init__(self, clock, trace, route):
        self.clock, self.trace, self.route = clock, trace, route
        self.position = list(route.hunting_anchor)
        self.hp, self.max_hp = 1400, 1490
        self.control = {"enabled": True, "revision": 5, "paused": False}
        self.visited_city = False
        self.back_at = None
        self.runner_stopped = False
        self.last_observation = None
        self.merchants = None
        anchor = route.hunting_anchor
        # A busy Bandit field: tonight 10-17 nearby threats defeated field parking.
        self.monsters = [
            {"position": [anchor[0] + dx, anchor[1] + dy], "alive": True}
            for dx, dy in ((3, 2), (-4, 5), (6, -3), (-2, -6), (8, 8), (-7, 1))
        ]

    def in_boundary(self):
        left, top, right, bottom = self.route.hunting_boundary
        return left <= self.position[0] <= right and top <= self.position[1] <= bottom

    def _farm_runner(self):
        """The in-client runner: fights in the area, walks back when outside."""
        if not self.control["enabled"] or self.runner_stopped:
            return
        if self.in_boundary():
            if self.visited_city and self.back_at is None:
                self.back_at = self.clock.now
                self.trace.add("runner_back_in_hunting_area", position=self.position)
            if self.back_at is not None and self.clock.now - self.back_at >= 20:
                self.runner_stopped = True  # Bag full: end the scenario.
            return
        goal = self.route.hunting_anchor
        self.position = [
            p + max(-10, min(10, g - p)) for p, g in zip(self.position, goal)
        ]

    def threats(self):
        return sum(
            max(
                abs(m["position"][0] - self.position[0]),
                abs(m["position"][1] - self.position[1]),
            )
            < 24
            for m in self.monsters
        )

    def parked_safely(self):
        return self.threats() == 0 and self.hp >= self.max_hp * 0.6

    def snapshot(self):
        self._farm_runner()
        if tuple(self.position) == CITY_SPOT:
            self.visited_city = True
        stopped = self.runner_stopped and self.control["enabled"]
        self.last_observation = {
            "t": round(self.clock.now - START, 3),
            "position": list(self.position),
            "hp": self.hp,
            "threats": self.threats(),
        }
        return {
            "target": dict(TARGET),
            "profile_id": "farmer-profile",
            "expected_sha256": "client-1078",
            "window": {
                "hwnd": 11,
                "root_hwnd": 11,
                "foreground": 11,
                "minimized": False,
                "client_size": [1036, 793],
            },
            "embedded_controls": {
                "control": {
                    **self.control,
                    "execution_state": "runner_stopped" if stopped else "farming",
                    "note": "Farm runner stopped: inventory_full" if stopped else "",
                },
                "life": {
                    "object_address": 77,
                    "map_id": 1011,
                    "position": list(self.position),
                    "dead_candidate": False,
                    "current_hp": self.hp,
                    "max_hp": self.max_hp,
                },
                "monsters": copy.deepcopy(self.monsters),
                "observations_available": True,
                "observed_at": self.clock.now,
                "external_execution": self.control["enabled"]
                and not self.runner_stopped,
                "manual_mouse": False,
            },
        }

    def grant_active(self):
        return bool(self.merchants and self.merchants.grant)

    def request(self, info, operation, body=None):
        assert info == "fake-worker.json"
        if operation == "health":
            return self.snapshot()
        if operation == "controls":
            self.trace.add(
                "farmer_controls", body=dict(body), grant_active=self.grant_active()
            )
            if "enabled" in body and body["enabled"] != self.control["enabled"]:
                self.control["revision"] += 1
            self.control.update(
                {k: v for k, v in body.items() if k in ("enabled", "paused")}
            )
            return {"ok": True}
        if operation == "town" and body == {"action": "supplies"}:
            supplies = self.route.supplies
            return {
                "items": [
                    {"uid": 1, "type_id": supplies.arrow_type, "amount": 4000},
                    {"uid": 2, "type_id": supplies.healing_type, "amount": 5},
                ],
                "equipped_ammo": {"type_id": supplies.arrow_type, "amount": 900},
                "capacity": 40,
                "silver": 25000,
            }
        raise AssertionError((operation, body))

    def step_to(self, target, *, expected_position=None):
        assert tuple(expected_position) == tuple(self.position)
        self.trace.add(
            "farmer_move",
            source=list(self.position),
            target=list(target),
            grant_active=self.grant_active(),
        )
        self.position = list(target)
        self.clock.now += 0.6
        return {"reached": True}


def run_scenario(root, monkeypatch, backlog):
    """Drive OvernightLoop.hunt() until the in-client runner reports a full bag."""
    from conquest import navigation, overnight, world_travel, city_travel
    from conquest import worker, scene_input
    from conquest.merchants import bridge, handoff
    from conquest.navigation import TerrainMap
    from conquest.routes import RouteLibrary

    root.mkdir()
    shutil.copytree(REPO / "profiles", root / "profiles")
    monkeypatch.chdir(root)  # All relative .runtime/ and reports/ state lands here.
    clock = Clock()
    trace = Trace(clock)
    monkeypatch.setattr(time, "time", clock.time)
    monkeypatch.setattr(time, "monotonic", clock.time)
    monkeypatch.setattr(time, "sleep", clock.sleep)
    # WorkWindows binds the OS clock as a default argument at import time.
    monkeypatch.setitem(
        handoff.WorkWindows.__init__.__kwdefaults__, "clock", clock.time
    )
    monkeypatch.setattr(ctypes.windll.user32, "GetAsyncKeyState", lambda key: 0)
    shutil.copy(REPO / "profiles/merchant-deliveries.json", handoff.POLICY)

    route = RouteLibrary().load("bandit")
    game = Game(clock, trace, route)
    merchants = Merchants(clock, trace, game, backlog)
    game.merchants = merchants
    terrain = TerrainMap(
        map_id=1011,
        width=900,
        height=900,
        blocked=np.zeros((900, 900), dtype=bool),
        source_sha256=phoenix_sha(),
        portals=(),
        excluded_scenes=(),
    )

    def read_terrain(client_root, map_id):
        assert map_id == 1011
        return terrain

    for module in (navigation, overnight, world_travel, city_travel):
        monkeypatch.setattr(module, "read_terrain", read_terrain)
    monkeypatch.setattr(worker, "request", game.request)
    monkeypatch.setattr(overnight, "request", game.request)
    monkeypatch.setattr(bridge, "request", merchants)
    monkeypatch.setattr(scene_input, "memory_player_anchor", lambda *a, **k: (518, 396))
    app_state = root / "reports/desktop-farming/app-state.json"
    app_state.parent.mkdir(parents=True)
    app_state.write_text(
        json.dumps({"worker_info_path": "fake-worker.json", "kills": 0}),
        encoding="utf-8",
    )
    visit = Path(city_travel.VISIT)
    visit.parent.mkdir(parents=True, exist_ok=True)
    visit.write_text(
        json.dumps(
            {
                "identity": TARGET,
                "map_id": 1011,
                "terrain_sha256": phoenix_sha(),
                "completed": True,
            }
        ),
        encoding="utf-8",
    )

    loop = overnight.OvernightLoop.__new__(overnight.OvernightLoop)
    loop.route = route
    loop.terrain = terrain
    loop.info = "fake-worker.json"
    loop.care = NS(check=lambda health: None, session=None)
    loop.stepper = NS(step_to=game.step_to, on_life=None)
    loop.identity = None
    loop.auto_level = False
    loop.next_level_check = 0
    loop.last_level = 0
    loop.phase = "starting"
    loop.cycles = 0
    loop.deadline = None
    loop.first_hunt_seconds = None
    loop.town_visit = None
    loop.output = root / "reports/overnight"
    loop.output.mkdir(parents=True)
    loop.stop_path = root / ".runtime/overnight.stop"
    loop.state = {"pid": 1, "route": route.id, "cycles": 0, "started_at": START}

    assert loop.hunt() is None  # Full bag: the ordinary town-return outcome.
    assert loop.phase == "restocking"

    events = [
        json.loads(line)
        for line in (loop.output / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    kept = (
        "hunt_started",
        "merchant_town_batch_started",
        "merchant_parking_finished",
        "merchant_work_started",
        "merchant_town_batch_finished",
        "merchant_work_finished",
        "merchant_town_batch_refused",
        "merchant_safe_spot",
        "return_required",
    )
    artifact = {
        "scenario": {"backlog": backlog, "start": START},
        "trace": trace.rows,
        "route_events": [
            {
                "t": round(row["time"] - START, 3),
                "event": row["event"],
                **{
                    key: row[key]
                    for key in ("parking", "town_batch", "backlogs", "character")
                    if key in row
                },
            }
            for row in events
            if row["event"] in kept
        ],
        "work_window": json.loads(
            (root / ".runtime/merchant-handoff.json").read_text(encoding="utf-8")
        ),
        "listed": merchants.listed,
        "remaining_backlog": merchants.backlog,
    }
    path = root / "town-batch-e2e-trace.json"
    path.write_text(json.dumps(artifact, indent=2, sort_keys=True), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "backlog,outcome",
    [
        # Live 2026-09-24 queue sizes: more than one ten-minute batch can list.
        ({"Spiritual": 31, "Dutch": 23}, "cap_reached"),
        ({"Spiritual": 7, "Dutch": 5}, "backlog_cleared"),
    ],
)
def test_hunting_backlog_city_batch_returns_to_hunting(
    tmp_path, monkeypatch, backlog, outcome
):
    first = run_scenario(tmp_path / "first", monkeypatch, backlog)
    second = run_scenario(tmp_path / "second", monkeypatch, backlog)
    # F13: deterministic, repeatable artifact.
    assert first.read_bytes() == second.read_bytes()

    artifact = json.loads(first.read_text(encoding="utf-8"))
    trace, events = artifact["trace"], artifact["route_events"]
    names = [row["event"] for row in events]
    grants = [row for row in trace if row["kind"] == "merchant_grant"]
    releases = [row for row in trace if row["kind"] == "merchant_release"]
    inputs = [row for row in trace if row["kind"] in ("farmer_move", "farmer_controls")]

    # F1/F2/F3: admitted batch, city parking verified before any grant, no field park.
    started = events[names.index("merchant_town_batch_started")]
    assert max(started["backlogs"].values()) >= 5
    parking = events[names.index("merchant_parking_finished")]["parking"]
    assert parking["mode"] == "city" and parking["outcome"] == "safe"
    assert "merchant_safe_spot" not in names
    first_grant_t = grants[0]["t"]
    moves = [row for row in trace if row["kind"] == "farmer_move"]
    to_city = [row for row in moves if row["t"] < first_grant_t]
    assert to_city and to_city[-1]["target"] == list(CITY_SPOT)
    assert parking["elapsed_seconds"] > 0

    # F4/F5: strictly alternating grant/release, each within 45 s and the cap.
    order = [
        row["kind"]
        for row in trace
        if row["kind"] in ("merchant_grant", "merchant_release")
    ]
    assert order == ["merchant_grant", "merchant_release"] * len(grants)
    batch_start = started["t"]
    for grant, release in zip(grants, releases):
        assert 38 <= grant["seconds"] <= 45
        assert release["t"] <= grant["expires_t"] <= batch_start + 600

    # F6: the farmer sends no input while a merchant holds input.
    assert inputs and not any(row["grant_active"] for row in inputs)

    # F7: a fresh parked observation precedes every grant.
    for grant in grants:
        seen = grant["observation"]
        assert grant["t"] - seen["t"] <= 1
        assert seen["position"] == list(CITY_SPOT) and seen["threats"] == 0
        assert seen["hp"] >= 1400

    # F8/F9: both merchants served; the batch ended on its cap or empty queue.
    finished = events[names.index("merchant_town_batch_finished")]["town_batch"]
    assert finished["outcome"] == outcome
    assert set(finished["listings"]) == set(backlog) and all(
        finished["listings"].values()
    )
    assert finished["listed"] == sum(artifact["listed"].values()) == len(grants)
    if outcome == "backlog_cleared":
        assert artifact["remaining_backlog"] == {name: 0 for name in backlog}
    else:
        assert sum(artifact["remaining_backlog"].values()) > 0
        assert finished["elapsed_seconds"] <= 600

    # F10: farming re-enabled after the last release; the runner walked back.
    resume = [
        row
        for row in trace
        if row["kind"] == "farmer_controls" and row["body"] == {"enabled": True}
    ]
    assert resume and resume[-1]["t"] >= releases[-1]["t"]
    assert any(row["kind"] == "runner_back_in_hunting_area" for row in trace)
    assert (
        names.index("merchant_town_batch_finished")
        < names.index("merchant_work_finished")
        < names.index("return_required")
    )

    # F11: cooldown; no further merchant grants or parking after the batch.
    assert grants[-1]["t"] < events[names.index("merchant_work_finished")]["t"]
    assert names.count("merchant_town_batch_started") == 1
    window = artifact["work_window"]
    assert window["phase"] == "town_batch_finished"
    assert (
        window["next_check"] - START
        >= events[names.index("merchant_town_batch_finished")]["t"] + 900
    )

    # F12: downtime is visible with counts and durations.
    assert finished["grants"] == len(grants)
    assert finished["parking_seconds"] > 0 and finished["elapsed_seconds"] > 0
    assert finished["backlogs_before"] == started["backlogs"]
