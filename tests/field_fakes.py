"""Fake game/worker boundary for end-to-end OvernightLoop route scenarios.

Only the authenticated worker bridge (``request``), process memory readers
(inventory/anchor), installed terrain files, keyboard state and the clock are
replaced.  Every route, restock, travel, travel-care and town-visit decision
runs through the real production code.  Nothing here touches a game process,
live journal or file outside the test's tmp_path.
"""

import json
from copy import deepcopy
from types import SimpleNamespace as NS

import numpy as np

HOVER = "Pointer is not over the memory-identified merchant control"
POTION = 1000020
SPEED = 1050002
TARGET = {
    "pid": 18532,
    "path": "C:\\Program Files\\Classic Conquer 2.0\\bin\\64\\ImConquer.exe",
    "creation_time_100ns": 134345064188672222,
    "architecture": "x64",
}
WAREHOUSE = (227, 246)
FIELD = (469, 428)


class ReachedWarehouse(Exception):
    """The fake game ends a scenario at the first warehouse interaction."""


class FakeClock:
    def __init__(self, start):
        self.now = float(start)
        self.mono = 1000.0

    def time(self):
        return self.now

    def monotonic(self):
        return self.mono

    def sleep(self, seconds):
        self.now += seconds
        self.mono += seconds


def terrain(map_id=1011):
    """Open walkable Phoenix map carrying the installed terrain fingerprint."""
    from conquest.city_travel import CITIES
    from conquest.navigation import TerrainMap

    sha = next(
        c["terrain_sha256"]
        for c in json.loads(CITIES.read_text(encoding="utf-8"))["cities"]
        if c["map_id"] == map_id
    )
    return TerrainMap(map_id, 600, 600, np.zeros((600, 600), dtype=bool), sha, (), ())


class FakeGame:
    """One farmer client: life, bag, open panels and a scripted farm runner."""

    def __init__(self, clock, *, position=FIELD, hp=727, max_hp=1000, dead=False):
        self.clock = clock
        self.position = list(position)
        self.map_id = 1011
        self.hp, self.max_hp = hp, max_hp
        self.dead = dead
        self.stamp = 0
        self.control = {"enabled": False, "paused": False, "revision": 1}
        self.runner_note = None
        self.open_panels = set()
        self.close_failures = []  # errors raised by the next Inventory closes
        self.ops = []  # every worker operation, in order
        self.silver = 150
        self.bag_items = [
            {"uid": 700 + n, "type_id": POTION, "amount": 1, "limit": 1, "slot": n}
            for n in range(4)
        ]
        self.ammo = {"uid": 690, "type_id": SPEED, "amount": 400, "limit": 5000}
        self.hunt_empties_ammo = True
        self.revive_at = (191, 250)
        self.end_at = "open-bank"
        self.transactions = []

    # -- memory observations -------------------------------------------------
    def life(self):
        self.stamp += 1
        return {
            "position": list(self.position),
            "map_id": self.map_id,
            "dead_candidate": self.dead,
            "ghost_candidate": self.dead,
            "revive_ready_candidate": self.dead,
            "current_hp": 0 if self.dead else self.hp,
            "max_hp": self.max_hp,
            "status": 0x20 if self.dead else 0,
            "appearance": 0,
            "object_address": 0x7FF0001000,
            "timestamp": self.stamp,
        }

    def health(self):
        return {
            "target": deepcopy(TARGET),
            "profile_id": "farmer",
            "window": {
                "foreground": 1,
                "root_hwnd": 1,
                "hwnd": 1,
                "minimized": False,
                "client_size": [1036, 793],
            },
            "embedded_controls": {
                "control": {
                    **self.control,
                    **(
                        {"execution_state": "runner_stopped", "note": self.runner_note}
                        if self.runner_note and self.control["enabled"]
                        else {}
                    ),
                },
                "manual_mouse": False,
                "manual_input_fence": False,
                "external_execution": False,
                "monsters": [],
                "observed_at": self.clock.time(),
                "life": self.life(),
            },
        }

    def potions(self):
        return sum(i["amount"] for i in self.bag_items if i["type_id"] == POTION)

    def supplies(self):
        return {
            "items": deepcopy(self.bag_items),
            "equipped_ammo": deepcopy(self.ammo),
            "silver": self.silver,
            "capacity": 40,
        }

    def inventory_reader(self):
        def read():
            items = [NS(**i) for i in self.bag_items]
            return NS(
                items=items,
                count=lambda type_id: sum(
                    i.amount for i in items if i.type_id == type_id
                ),
                silver=self.silver,
            )

        return NS(read=read)

    # -- worker boundary -------------------------------------------------------
    def request(self, info, operation, body=None):
        body = dict(body or {})
        body.pop("expires_at", None)
        if operation == "health":
            return self.health()
        self.ops.append({"op": operation, **_summary(body)})
        if operation == "controls":
            if "enabled" in body:
                self.control["enabled"] = body["enabled"]
                self.control["revision"] += 1
                hunting = body["enabled"] and body.get("target_type_ids")
                if hunting and self.hunt_empties_ammo:
                    # The native farm hunts until its Scatter ammo runs out.
                    self.ammo["amount"] = 0
                    self.runner_note = "Farm runner stopped: ammo_unavailable"
                if not body["enabled"]:
                    self.runner_note = None
            if "route_id" in body and getattr(self, "death_return", None):
                # The app's route selection cancels its native death return.
                self.death_return.write_text(json.dumps({"phase": "cancelled"}))
            return {}
        if operation == "revive-click":
            if not self.dead:
                raise ValueError("The inspected ghost state is not present")
            self.dead = False
            self.position = list(self.revive_at)
            self.hp = self.max_hp * 7 // 10
            return {"clicked": True}
        if operation == "route-jump":
            if body["source"] != self.position or self.dead:
                raise ValueError("Route map changed")
            self.position = list(body["destination"])
            distance = max(abs(a - b) for a, b in zip(body["source"], self.position))
            return {"issued": True, "movement": "jump" if distance >= 8 else "run"}
        if operation == "town":
            return self.town(body)
        raise AssertionError(f"Unexpected worker operation {operation} {body}")

    def town(self, body):
        action = body["action"]
        if action == "supplies":
            return self.supplies()
        if action == "gear":
            return {
                "level": 110,
                "equipment": {
                    "arrows": {"uid": 690, "type_id": SPEED, "level": 73},
                },
            }
        if action == "close":
            window = body["window"]
            if window not in self.open_panels:
                return {"closed": True}
            if window == "Inventory" and self.close_failures:
                raise self.close_failures.pop(0)
            self.open_panels.discard(window)
            return {"closed": True}
        if action == "clear-travel-panels":
            if "Inventory" in self.open_panels:
                if self.close_failures:
                    raise self.close_failures.pop(0)
                self.open_panels.discard("Inventory")
                return {"closed_panel": "Inventory"}
            return {"closed_panel": None}
        if action == "consume-healing":
            item = next(i for i in self.bag_items if i["uid"] == body["uid"])
            assert item["type_id"] == POTION and item["amount"] > 0
            self.open_panels.add("Inventory")
            before = self.hp
            item["amount"] -= 1
            self.bag_items = [i for i in self.bag_items if i["amount"] > 0]
            self.hp = min(self.max_hp, self.hp + 300)
            return {
                "consumed": True,
                "uid": body["uid"],
                "type_id": POTION,
                "hp_before": before,
                "hp_after": self.hp,
                "remaining": self.potions(),
            }
        if action == "vendor-status":
            near = max(abs(a - b) for a, b in zip(self.position, WAREHOUSE)) <= 12
            return {"reachable": body.get("vendor_type") == 0 and near}
        if action == "warehouse-locate":
            return {"position": list(WAREHOUSE)}
        if action == self.end_at:
            raise ReachedWarehouse(action)
        raise AssertionError(f"Unexpected town action {body}")


def _summary(body):
    keep = (
        "action",
        "window",
        "enabled",
        "uid",
        "vendor_type",
        "source",
        "destination",
    )
    return {k: body[k] for k in keep if k in body}


def install(monkeypatch, tmp_path, game, clock):
    """Wire the fake boundary into the real modules; return the real loop."""
    from conquest import (
        city_travel,
        overnight,
        protected_withdrawal,
        route_input,
        scene_input,
        travel_care,
        worker,
        world_travel,
    )
    from conquest import restock_town_recovery as recovery
    from conquest.overnight import OvernightLoop
    from conquest.route_input import BridgeJumpStepper
    from conquest.routes import RouteLibrary
    from conquest.town_visit import TownVisit
    from conquest.travel_care import TravelCare

    fake_time = NS(time=clock.time, monotonic=clock.monotonic, sleep=clock.sleep)
    for module in (overnight, travel_care, route_input):
        monkeypatch.setattr(module, "time", fake_time)
    for module in (overnight, travel_care, worker):
        monkeypatch.setattr(module, "request", game.request)
    monkeypatch.setattr(
        overnight.ctypes,
        "windll",
        NS(
            user32=NS(GetAsyncKeyState=lambda key: 0),
            kernel32=NS(SetThreadExecutionState=lambda *_: None),
        ),
        raising=False,
    )
    fake_terrain = terrain()
    monkeypatch.setattr(city_travel, "read_terrain", lambda root, map_id: fake_terrain)
    monkeypatch.setattr(world_travel, "read_terrain", lambda root, map_id: fake_terrain)
    monkeypatch.setattr(scene_input, "memory_player_anchor", lambda *a: (518, 396))
    monkeypatch.setattr(city_travel, "VISIT", tmp_path / "city-visit.json")
    monkeypatch.setattr(
        overnight, "RECOVERY_CHECKPOINT", tmp_path / "death-return.json"
    )
    monkeypatch.setattr(
        protected_withdrawal, "JOURNAL", tmp_path / "protected-withdrawals.sqlite3"
    )
    output = tmp_path / "overnight"
    output.mkdir(exist_ok=True)
    monkeypatch.setattr(recovery, "EVENTS", output / "events.jsonl")
    monkeypatch.setattr(recovery, "MONEY", tmp_path / "bank-transfers.jsonl")
    app_state = tmp_path / "app-state.json"
    app_state.write_text(
        json.dumps({"worker_info_path": "fake-worker.json", "kills": 0}),
        encoding="utf-8",
    )
    monkeypatch.setattr(overnight, "state_path", lambda value: str(app_state))
    # The city was already visited by this process; resume goes straight on.
    city_travel.VISIT.write_text(
        json.dumps(
            {
                "identity": TARGET,
                "map_id": 1011,
                "terrain_sha256": fake_terrain.source_sha256,
                "completed": True,
            }
        ),
        encoding="utf-8",
    )
    loop = OvernightLoop.__new__(OvernightLoop)
    loop.route = RouteLibrary().load("bandit")
    loop.terrain = fake_terrain
    loop.deadline = None
    loop.first_hunt_seconds = None
    loop.identity = None
    loop.auto_level = False
    loop.next_level_check = 0
    loop.last_level = 0
    loop.phase = "starting"
    loop.cycles = 0
    loop.output = output
    loop.stop_path = tmp_path / "overnight.stop"
    loop.state = {"pid": 1, "route": "bandit", "cycles": 0, "updated_at": 0}
    loop.town_visit = TownVisit(
        tmp_path / "town-visit.json",
        profile="farmer",
        clock=clock.time,
        probe=lambda: {"available": True, "cursor": 1, "observed_at": clock.time()},
    )
    care = TravelCare.__new__(TravelCare)
    care.info = "fake-worker.json"
    care.notify = lambda event: loop.record(event.pop("event"), **event)
    care.session = None
    care.layout = None
    care.health_layout = {}
    care.inventory = game.inventory_reader()
    care.pending = None
    care.last_heal = -float("inf")
    care.last_revive = -float("inf")
    care.exact_1078 = True
    care.revive_journal = tmp_path / "travel-revive.json"
    care.revive_state = {}
    care.revive_living_samples = 0
    care.revive_last_sample = None
    loop.info = "fake-worker.json"
    loop.care = care
    loop.stepper = BridgeJumpStepper("fake-worker.json", on_life=care.check)
    return loop


def events(loop):
    path = loop.output / "events.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line]
