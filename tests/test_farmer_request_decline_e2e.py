"""End-to-end: an unapproved trade request deadlocked the farmer (live 2026-09-25).

Live 15:05:42: player "ahmeeed" (uid 1174897) sent the farmer Parasite a trade
request while it hunted on map 1011. The manual session timed out at 15:05:47
(manual_declines reason=timeout) but no decline was ever claimed:
manual_decline_claims has no row for it (nor for the two earlier 1078
declines, 09-19 and 09-23). The fence held the route's per-second
town("supplies") read; overnight.town() retried 80 times, then the route
failed into needs_attention and its finally switched Farming Off, leaving the
farmer idle in the field. An explicit restart at 15:12:50 stopped the farm
for prepare_supplies (control Off) and failed the same way 34 s later.

Root cause, from code:
 * On client 1078 decline_unrelated_request could never press Cancel: it
   required the farmer qualification to carry capability "trade_request"
   (the live 1078 farmer qualification carries only "farmer_delivery"), and
   _cancel_control was pinned to 1074 (request model vtable 0x5C4F30 and
   1074 handler bytes); the 1078 request model is 0x5E0148. The ValueError
   was swallowed into the per-tick, non-persisted observation
   "decline_blocker" and retried forever.
 * The decline gate was the Farming control's enabled flag, which the route
   itself turns Off for every town action (stop_farm, "Stop farming before
   town input"). A request arriving at a town action, at route start, or
   after the route failed could never be declined.
 * The route treated the fence as a failed town read and gave up after its
   80 attempts, which switched Farming Off (needs_attention) and made the
   second gate permanent: fence -> route failure -> Off -> no decline.

Real code under test: MerchantRuntime/ManualRuntime (manual sessions, fence,
process_manual), manual_farmer.observe/controller/presence, MerchantDriver,
decline_unrelated_request with its 1078 cancel locator, the real GuiReader
(window-registry walk, std::string decoding, viewport, hover proof) over a
byte-level fake of the 1078 client memory, trade_driver_1078's request
dialog proof and qualification check, SharedLayoutRevision, InputCoordinator
input_scope, ManualSessionStore SQLite journals, FarmingControl,
OvernightLoop.run/town/living/health/record/stop_farm and TownTrade.__call__'s
manual boundary. Fakes only at boundaries: the canonical 1078 snapshot
decoder (manual_ownership / TradeMemory1078.read), native window geometry,
focus activation, the physical click (which moves a fake cursor, updates the
client's hover memory, runs the real before_press proof and then presses
whatever control is under the cursor), the route's bridge transport and the
bilateral delivery receipt validator. Time is virtual.

Failure modes, written before the fix:
 F1 Request while hunting (Farming On): declined exactly once, about five
    seconds after it appeared, by one press on the memory-proved Cancel
    control (never Accept); the session settles declined_verified; the
    route's town read waits instead of failing and farming continues with
    Farming still On.
 F2 Client 1078: the decline must use the 1078 request-dialog proof and the
    farmer's own 1078 native trade qualification; a 1074-pinned locator or a
    trade_request-only capability strands the request (the live cause).
 F3 Request while the route is at a town action (the route's stop_farm set
    the control Off): the route waits without consuming attempts or failing,
    the decline still runs (a route-owned Off is not a user Off) and the
    town actions then run.
 F4 The exact live sequence: request present, route failed, Farming Off
    without a user stop and no live route: no input, blocker reported; on
    the explicit restart the route's prepare_supplies town action waits, the
    already decline_pending request is declined once, and the route resumes
    without operator action.
 F5 Farming Off by the user (overnight.stop marker) even while a route
    status still looks live: no decline input, session stays
    decline_pending, the observation reports the user Off blocker.
 F6 Global Stop, then F12 held: no decline input; after both clear the
    decline runs once.
 F7 Decline press uncertain (request still displayed after the press): no
    second claim or press on any later tick; the route's wait is bounded
    and ends in needs_attention (attention raised) instead of looping.
 F8 Approved visitor: never declined.
 F9 Unapproved open trade: never pressed; session needs_attention.
 F10 Changed process identity: never pressed; session needs_attention.
 F11 Requester walked out of the scene while its exact request is still
    displayed: still declined, bound to the request's native participant UID.
 F12 The artifact is not repeatable.

The scenario writes <tmp>/<run>/farmer-request-decline.json, re-reads it for
the assertions, and runs twice to prove the artifact is byte-identical.
"""

import copy
import ctypes
import json
import os
import struct
import threading
import time
import zlib
from contextlib import nullcontext
from types import SimpleNamespace as NS

from conquest.control import FarmingControl
from conquest.memory_build_layout import CLIENT_SHA256_1078, READ_LAYOUTS
from conquest.merchants import coordination
from conquest.merchants.coordination import InputCoordinator, install
from conquest.merchants.journal import Journal
from conquest.merchants.runtime import MerchantRuntime

LAYOUT = READ_LAYOUTS[CLIENT_SHA256_1078]
BASE = 0x140000000
HEAD, NODE15, NODE14 = 0x20000000, 0x20000100, 0x20000200
MODEL15, MODEL14 = 0x20001000, 0x20002000
CONTEXT, ARRAY, VIEWPORT = 0x20010000, 0x20020000, 0x20030000
WINDOW, HEAP = 0x20040000, 0x20050000
SEED = 0x5EED
GEOMETRY = (412.0, 300.0, 200.0, 120.0)
BUTTON_Y = 390.0
ACCEPT = (512, 377)  # x + width / 2, button_y - 22 + line / 2
CANCEL = (512, 399)  # the dialog's second button, one row below
VISITOR = {
    "participant": "ahmeeed",
    "participant_uid": 1174897,
    "message": "ahmeeed wishes to trade with you.",
}
READ_ONLY = ("supplies", "shop", "gear", "vendor-status", "warehouse-items")


class Memory:
    def __init__(self):
        self.blocks = {}

    def map(self, address, size):
        self.blocks[address] = bytearray(size)

    def write(self, address, data):
        for start, block in self.blocks.items():
            if start <= address and address + len(data) <= start + len(block):
                block[address - start : address - start + len(data)] = data
                return
        raise AssertionError(f"unmapped write {address:#x}")

    def read_block(self, address, size):
        for start, block in self.blocks.items():
            if start <= address and address + size <= start + len(block):
                return bytes(block[address - start : address - start + size])
        raise OSError(f"unmapped read {address:#x}+{size}")

    def string(self, address, text, heap):
        data = text.encode("utf-8")
        if len(data) <= 15:
            raw = data.ljust(16, b"\0") + struct.pack("<QQ", len(data), 15)
        else:
            self.write(heap, data)
            raw = struct.pack("<Q", heap).ljust(16, b"\0")
            raw += struct.pack("<QQ", len(data), len(data))
        self.write(address, raw)


class Client:
    """Byte-level 1078 GUI memory plus the game rules the scenario needs."""

    def __init__(self, world):
        self.world = world
        self.memory = m = Memory()
        for address, size in (
            (BASE + LAYOUT.gui_registry_rva, 16),
            (BASE + LAYOUT.gui_context_rva, 8),
            (HEAD, 56),
            (NODE15, 56),
            (NODE14, 56),
            (MODEL15, 0x100),
            (MODEL14, 0x100),
            (CONTEXT, 0x4100),
            (ARRAY, 8),
            (VIEWPORT, 0x20),
            (WINDOW, 0x250),
            (HEAP, 0x100),
        ):
            m.map(address, size)
        m.write(BASE + LAYOUT.gui_registry_rva, struct.pack("<QQ", HEAD, 2))
        m.write(HEAD + 8, struct.pack("<Q", NODE15))
        m.write(NODE15, struct.pack("<QQQ", NODE14, 0, HEAD))
        m.write(NODE15 + 32, struct.pack("<IIQ", 15, 0, MODEL15))
        m.write(NODE14, struct.pack("<QQQ", HEAD, 0, HEAD))
        m.write(NODE14 + 32, struct.pack("<IIQ", 14, 0, MODEL14))
        m.write(MODEL15, struct.pack("<Q", BASE + LAYOUT.merchant_confirm_vtable_rva))
        m.write(MODEL14, struct.pack("<Q", BASE + LAYOUT.merchant_trade_vtable_rva))
        m.string(MODEL15 + 0x48, "Trade###Confirm", HEAP)
        m.string(MODEL15 + 0x68, VISITOR["message"], HEAP + 0x40)
        m.string(MODEL15 + 0x88, "Accept", HEAP)
        m.string(MODEL15 + 0xA8, "Cancel", HEAP)
        m.write(BASE + LAYOUT.gui_context_rva, struct.pack("<Q", CONTEXT))
        m.write(CONTEXT + 0x40C0, struct.pack("<Q", ARRAY))
        m.write(ARRAY, struct.pack("<Q", VIEWPORT))
        m.write(VIEWPORT + 0xC, struct.pack("<2f", 1024.0, 768.0))
        m.write(WINDOW + 8, struct.pack("<I", SEED))
        m.write(WINDOW + 0x18, struct.pack("<4f", *GEOMETRY))
        m.write(WINDOW + 0xE8, struct.pack("<2f", GEOMETRY[0] + 192, BUTTON_Y))
        m.write(WINDOW + 0x114, struct.pack("<f", 18.0))
        self.identity = {
            "pid": 18532,
            "creation_time_100ns": 134345064188672222,
            "path": "C:/Program Files/Classic Conquer 2.0/bin/64/ImConquer.exe",
        }
        self.request = None
        self.trade = None
        self.cancel_effective = True
        self.foreground = 0
        self.presses = []

    # -- game state ---------------------------------------------------------
    def show_request(self):
        self.request = dict(VISITOR)
        self.memory.write(MODEL15 + 12, b"\x01")
        self.world.mark("request_shown")

    def close_request(self):
        self.request = None
        self.memory.write(MODEL15 + 12, b"\x00")

    def open_trade(self):
        self.trade = {
            "participant": VISITOR["participant"],
            "participant_uid": VISITOR["participant_uid"],
            "own_items": [],
            "items": [],
            "own_silver": 0,
            "other_silver": 0,
            "accepted": False,
            "other_accepted": False,
        }
        self.memory.write(MODEL14 + 12, b"\x01")

    def windows(self):
        if not self.request:
            return []
        return [
            {"name": "Trade###Confirm", "address": WINDOW, "geometry": list(GEOMETRY)}
        ]

    def snapshot(self):
        return {
            "character": "Parasite",
            "character_uid": 1173490,
            "server": "America",
            "timestamp": time.time(),
            "identity": dict(self.identity),
            "inventory": [
                {
                    "uid": 5001,
                    "type_id": 1050002,
                    "plus": 0,
                    "gem1": 0,
                    "gem2": 0,
                    "quantity": 69,
                    "bound": False,
                    "name": "SpeedArrow",
                }
            ],
            "booth": [],
            "silver": 26156,
            "capacity": 40,
            "booth_open": False,
            "own_booth_uid": 0,
            "hp": 900,
            "map_id": 1011,
            "position": [430, 430],
            "request": copy.deepcopy(self.request),
            "trade": copy.deepcopy(self.trade),
            "reader_build": "1078-canonical-trade",
        }

    # -- input boundary -----------------------------------------------------
    @staticmethod
    def control_at(point):
        for label, (cx, cy) in (("Accept", ACCEPT), ("Cancel", CANCEL)):
            if abs(point[0] - cx) <= 40 and abs(point[1] - cy) <= 9:
                return label
        return None

    def click(self, target, x, y, size, *, layout_guard=None, before_press=None, **_):
        assert tuple(size) == (1024, 768)
        label = self.control_at((x, y)) if self.request else None
        hovered = zlib.crc32(label.encode("utf-8"), SEED) if label else 0
        self.memory.write(CONTEXT + 0x3EC0, struct.pack("<Q", WINDOW if label else 0))
        self.memory.write(CONTEXT + 0x3EF0, struct.pack("<I", hovered))
        if layout_guard:
            layout_guard()
        if before_press:
            before_press()
        self.presses.append({"t": self.world.rel(), "control": label, "point": [x, y]})
        if label == "Cancel" and self.cancel_effective:
            self.close_request()
        elif label == "Accept":
            self.close_request()
            self.open_trade()

    def activate(self, hwnd, identity):
        assert identity == self.identity
        self.foreground = 1
        self.world.activations += 1
        return True


class Adapter:
    def __init__(self, client):
        self.client = client
        self.expected_sha256 = CLIENT_SHA256_1078
        self.modules = [{"name": "ImConquer.exe", "base": BASE, "size": 0x800000}]
        self.read = self.read_block

    @property
    def identity(self):
        return dict(self.client.identity)

    def read_block(self, address, size):
        return self.client.memory.read_block(address, size)

    def assert_identity(self):
        return None


class TradeMemory:
    """Boundary stand-in for TradeMemory1078's canonical decoders only."""

    def __init__(self, observer, *, definitions=None):
        from conquest.merchants.memory import GuiReader

        self.observer = observer
        self.client = observer.adapter.client
        self.session = self.s = observer.adapter
        self.gui = GuiReader.for_session(observer.adapter)

    def read_manual_ownership(self):
        return self.client.snapshot()

    def read(self, *, max_seconds=3, recovery=False, farmer_preflight=False):
        from conquest.merchants.reader_1078 import ObservationUnavailable1078

        snapshot = self.client.snapshot()
        maps = (1002, 1011, 1036) if farmer_preflight else (1036,)
        if snapshot["map_id"] not in maps:
            raise ObservationUnavailable1078("1078 map is not qualified")
        snapshot["windows"] = self.client.windows()
        return snapshot


class World:
    def __init__(self, root, monkeypatch, name):
        self.root = root / name
        self.root.mkdir(parents=True)
        monkeypatch.chdir(self.root)
        self.now = 1790363140.0
        self.start = self.now
        self.marks = {}
        self.activations = 0
        self.keys = set()
        self.blockers = []
        self.client = Client(self)
        self.journal = Journal(self.root / "journal.sqlite3")
        self.guard = InputCoordinator(lambda: True, path=self.root / "input.lock")
        self.guard.owner_allowed = lambda character: character == "Farmer"
        self.runtime = MerchantRuntime(
            NS(identities=list),
            self.guard,
            journal=self.journal,
            market_path=self.root / "market.json",
        )
        self.control = FarmingControl()
        target = NS(
            hwnd=1,
            snapshot=lambda: {
                "client_size": [1024, 768],
                "root_hwnd": 1,
                "foreground": self.client.foreground,
                "minimized": False,
                "pid": self.client.identity["pid"],
            },
        )
        self.observer = NS(
            character="Parasite",
            lock=threading.RLock(),
            adapter=Adapter(self.client),
            operations=NS(target=target),
        )
        self.runtime.configure_manual_farmer(
            lambda: self.observer, self.control.snapshot
        )
        install(self.guard)
        self.patch(monkeypatch)
        self.qualify()
        self.loop = None

    # -- clock --------------------------------------------------------------
    def rel(self):
        return round(self.now - self.start, 2)

    def mark(self, name):
        self.marks.setdefault(name, self.rel())

    def sleep(self, seconds):
        self.now += max(0.0, seconds)

    # -- boundaries ---------------------------------------------------------
    def patch(self, monkeypatch):
        from conquest.layout_revision import SharedLayoutRevision
        from conquest.merchants.driver import MerchantDriver

        monkeypatch.setattr(time, "time", lambda: self.now)
        monkeypatch.setattr(time, "monotonic", lambda: self.now)
        monkeypatch.setattr(time, "sleep", self.sleep)
        monkeypatch.setattr(
            ctypes.windll.user32,
            "GetAsyncKeyState",
            lambda key: 0x8000 if key in self.keys else 0,
        )
        monkeypatch.setattr(
            ctypes.windll.kernel32, "SetThreadExecutionState", lambda flags: 1
        )
        monkeypatch.setattr(
            "conquest.merchants.trade_reader_1078.manual_ownership",
            lambda session, character: self.client.snapshot(),
        )
        monkeypatch.setattr(
            "conquest.merchants.trade_reader_1078.TradeMemory1078", TradeMemory
        )
        monkeypatch.setattr(
            "conquest.merchants.delivery_bridge.source_memory", TradeMemory
        )
        monkeypatch.setattr("conquest.reconnect.login_screen", lambda hwnd: False)
        monkeypatch.setattr("conquest.foreground.foreground_click", self.client.click)
        monkeypatch.setattr(
            "conquest.focus_recovery.activate_client", self.client.activate
        )
        monkeypatch.setattr(
            "conquest.desktop_runtime.physical_coordinates", lambda: nullcontext()
        )
        monkeypatch.setattr(
            "conquest.merchants.trade_driver_1078.validate_receipt", lambda state: state
        )
        self.scene = {"present": True}

        def requester_identity(observer, name):
            if not self.scene["present"]:
                raise ValueError(
                    "Incoming requester UID is absent or ambiguous in the live scene"
                )
            return {"uid": VISITOR["participant_uid"], "name": name, "position": [1, 2]}

        monkeypatch.setattr(
            "conquest.merchants.unrelated_request.requester_identity",
            requester_identity,
        )

        def native(target):
            state = target.snapshot()
            return {**state, "hwnd": 1}, (0, 0), 96, 1, (0, 0, 3840, 2160)

        def layout_revision(driver):
            return SharedLayoutRevision(
                driver.target,
                windows=self.client.windows,
                gui_size=driver.memory.gui.viewport_size,
                manual_active=lambda: False,
                native_reader=native,
                clock=lambda: time.monotonic(),
                sleep=lambda seconds: time.sleep(seconds),
            )

        monkeypatch.setattr(MerchantDriver, "layout_revision", layout_revision)
        from conquest import overnight

        monkeypatch.setattr(overnight, "request", self.bridge)

    def qualify(self):
        from conquest.merchants.trade_driver_1078 import (
            TRADE_MODE_RVA,
            TRADE_MODE_VALUE,
            receipt_digest,
        )

        # Shape of the live 1078 farmer qualification: farmer_delivery only.
        receipt = {
            "intent": {
                "farmer": {"character": "Parasite"},
                "merchant": {"character": "Dutch"},
            }
        }
        path = self.root / ".runtime" / "merchants" / "farmer-delivery-qualified.json"
        path.parent.mkdir(parents=True)
        path.write_text(
            json.dumps(
                {
                    "character": "Parasite",
                    "server": "America",
                    "client_sha256": CLIENT_SHA256_1078,
                    "capabilities": {"farmer_delivery": True},
                    "evidence": "delivery-request-probe-audit/receipt.json",
                    "recipient": {
                        "vtable_rva": 6165200,
                        "uid_offset": 120,
                        "name_offset": 164,
                        "position_offset": 232,
                        "name_format": "inline_utf8",
                        "name_capacity": 64,
                    },
                    "native_trade_layout_revision": 1,
                    "target_mode": {"rva": TRADE_MODE_RVA, "value": TRADE_MODE_VALUE},
                    "trade_receipt_1078": receipt,
                    "trade_receipt_1078_sha256": receipt_digest(receipt),
                }
            ),
            encoding="utf-8",
        )

    # -- the desktop app's farmer observer thread (run_manual_farmer body) ----
    def observe(self, seconds, *, every=0.5, at=None):
        end = self.now + seconds
        while self.now < end:
            if at:
                at(self.rel())
            self.runtime.observe_manual_farmer()
            blocker = self.runtime.manual_farmer_status()["observation"].get(
                "decline_blocker"
            )
            if blocker and blocker not in self.blockers:
                self.blockers.append(blocker)
            time.sleep(every)

    # -- the embedded farmer bridge used by the route process -----------------
    def bridge(self, info, operation, body=None):
        from conquest.town_trade import TownTrade

        assert info == "bridge"
        if operation == "health":
            projection = getattr(coordination, "farmer_fence_projection", None)
            fence = (
                projection()
                if projection
                else {
                    "manual_input_fence": coordination.manual_session_blocked("Farmer")
                }
            )
            return {
                "target": self.client.identity,
                "window": {
                    "hwnd": 1,
                    "root_hwnd": 1,
                    "foreground": 1,
                    "minimized": False,
                },
                "embedded_controls": {
                    "control": self.control.snapshot(),
                    "life": {
                        "dead_candidate": False,
                        "map_id": 1011,
                        "position": [430, 430],
                        "current_hp": 900,
                    },
                    "observed_at": time.time(),
                    "external_execution": False,
                    "manual_mouse": False,
                    **fence,
                },
            }
        if operation == "controls":
            return self.control.update(body)
        if operation == "town":
            action = body["action"]
            if action not in READ_ONLY:
                expiry = body.get("expires_at")
                if not 0 < expiry - time.time() <= 5:
                    raise ValueError("Town input must expire within five seconds")
                if self.control.snapshot()["enabled"]:
                    raise ValueError("Stop farming before town input")
            town = TownTrade.__new__(TownTrade)
            town.observer = self.observer
            town.execute = self.town_execute
            return TownTrade.__call__(town, body)
        raise AssertionError(operation)

    def town_execute(self, body):
        self.town_actions.append([self.rel(), body["action"], body.get("window")])
        if body["action"] == "supplies":
            return {"items": [], "capacity": 40, "silver": 26156}
        return {"closed": body.get("window")}

    def route(self, scenario):
        from conquest.overnight import OvernightLoop

        loop = OvernightLoop.__new__(OvernightLoop)
        loop.info = "bridge"
        loop.care = loop.stepper = None
        loop.identity = None
        loop.deadline = None
        loop.phase = "starting"
        loop.cycles = 0
        loop.town_visit = None
        loop.output = self.root / "reports" / "overnight"
        loop.output.mkdir(parents=True, exist_ok=True)
        loop.stop_path = self.root / ".runtime" / "overnight.stop"
        loop.state = {"pid": os.getpid(), "route": "test", "cycles": 0}
        app = self.root / "reports" / "desktop-farming" / "app-state.json"
        app.parent.mkdir(parents=True, exist_ok=True)
        app.write_text(json.dumps({"worker_info_path": "bridge"}), encoding="utf-8")
        self.loop = loop
        self.town_actions = []
        self.route_result = {}
        loop._run_route = lambda: scenario(loop)
        loop.run()
        events = []
        with (loop.output / "events.jsonl").open(encoding="utf-8") as lines:
            for line in lines:
                row = json.loads(line)
                events.append(
                    [
                        round(row["time"] - self.start, 2),
                        row["event"],
                        row.get("action"),
                    ]
                )
        return {
            "events": events,
            "failed": any(event[1] == "failed" for event in events),
            "town_actions": self.town_actions,
            **self.route_result,
        }

    # -- evidence -----------------------------------------------------------
    def evidence(self, route=None):
        with self.runtime.manual_sessions.db() as db:
            session = db.execute(
                "SELECT phase,reason FROM manual_sessions ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            request = db.execute(
                "SELECT state FROM manual_requests ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
            claims = db.execute("SELECT COUNT(*) FROM manual_decline_claims").fetchone()
            declines = db.execute("SELECT COUNT(*) FROM manual_declines").fetchone()
        journal = self.runtime._manual_get("Farmer", "unrelated_request_decline")
        return copy.deepcopy(
            {
                "marks": self.marks,
                "presses": self.client.presses,
                "claims": claims[0],
                "declines": declines[0],
                "session": dict(session) if session else None,
                "request_state": request[0] if request else None,
                "decline_journal": journal.get("phase") if journal else None,
                "request_visible": self.client.request is not None,
                "trade_open": self.client.trade is not None,
                "fenced": self.guard.manual_session_blocked("Farmer"),
                "focus_activations": self.activations,
                "blockers": self.blockers,
                "route": route,
            }
        )


def hunting(world, *, seconds=30, request_tick=2):
    """The hunt loop's per-second shape: living -> town('supplies') -> sleep."""

    def scenario(loop):
        from conquest.overnight import request

        loop.phase = "hunting"
        request(
            loop.info,
            "controls",
            {"enabled": True, "target_type_ids": [1], "target_ids": []},
        )
        loop.record("hunt_started")
        for tick in range(seconds):
            loop.living()
            if tick == request_tick:
                world.client.show_request()
            loop.town("supplies")
            time.sleep(1)
        world.route_result["farming_enabled_at_end"] = world.control.snapshot()[
            "enabled"
        ]

    return world.route(scenario)


def town_action(world, *, restart=False):
    """prepare_supplies/restock shape: stop_farm -> close panels -> read supplies."""

    def scenario(loop):
        from conquest.overnight import request

        if not restart:
            loop.phase = "hunting"
            request(loop.info, "controls", {"enabled": True})
            loop.record("hunt_started")
            loop.phase = "restocking"
            loop.record("return_required")
        loop.stop_farm()
        if not restart:
            world.client.show_request()
        loop.town("close", window="Shop")
        loop.town("close", window="Inventory")
        loop.town("supplies")
        world.route_result["farming_enabled_at_end"] = world.control.snapshot()[
            "enabled"
        ]

    return world.route(scenario)


def scenario(root, monkeypatch):
    artifact = {}
    try:
        # F1/F2: request while hunting with Farming On.
        world = World(root, monkeypatch, "hunting")
        artifact["hunting"] = world.evidence(hunting(world))

        # F3: request at a town action after the route's own stop_farm.
        world = World(root, monkeypatch, "town_action")
        artifact["town_action"] = world.evidence(town_action(world))

        # F4: the exact live sequence, then the explicit restart.
        world = World(root, monkeypatch, "live_restart")
        world.control.update({"enabled": True})
        world.client.show_request()
        world.observe(2)
        world.control.update({"enabled": False})  # the failed route's finally
        status = world.root / "reports" / "overnight" / "status.json"
        status.parent.mkdir(parents=True)
        status.write_text(
            json.dumps(
                {"pid": os.getpid(), "phase": "needs_attention", "updated_at": 0}
            ),
            encoding="utf-8",
        )
        world.observe(12)
        before = world.evidence()
        world.control.update({"enabled": True})  # explicit_restart (fresh start)
        artifact["live_restart"] = {
            "while_off": {
                key: before[key]
                for key in ("presses", "claims", "request_state", "blockers")
            },
            **world.evidence(town_action(world, restart=True)),
        }

        # F5: Farming Off by the user while a route status still looks live.
        world = World(root, monkeypatch, "user_off")
        stop = world.root / ".runtime" / "overnight.stop"
        stop.parent.mkdir(parents=True, exist_ok=True)
        stop.write_text("Stopped by user: Farming Off", encoding="utf-8")
        status = world.root / "reports" / "overnight" / "status.json"
        status.parent.mkdir(parents=True)
        status.write_text(
            json.dumps(
                {"pid": os.getpid(), "phase": "hunting", "updated_at": world.now}
            ),
            encoding="utf-8",
        )
        world.client.show_request()
        world.observe(15)
        artifact["user_off"] = world.evidence()

        # F6: Global Stop, then F12 held, then both released.
        world = World(root, monkeypatch, "stop_and_f12")
        world.control.update({"enabled": True})
        world.client.show_request()
        world.guard.stop()

        def controls(t):
            if t >= 10 and world.guard.stopped:
                world.guard.resume()
                world.keys.add(0x7B)
            if t >= 15:
                world.keys.discard(0x7B)

        world.observe(30, at=controls)
        artifact["stop_and_f12"] = world.evidence()

        # F7: the press leaves the request displayed (uncertain outcome).
        world = World(root, monkeypatch, "uncertain")
        world.client.cancel_effective = False
        route = hunting(world, seconds=120)
        world.observe(10)
        artifact["uncertain"] = world.evidence(route)

        # F8: approved visitor.
        world = World(root, monkeypatch, "approved")
        world.control.update({"enabled": True})
        world.client.show_request()
        world.observe(1)
        binding = world.runtime.manual_status("Farmer")["approval_binding"]
        world.runtime.approve_manual(binding, operator="Floor")
        world.observe(12)
        artifact["approved"] = world.evidence()

        # F9: unapproved open trade.
        world = World(root, monkeypatch, "open_trade")
        world.control.update({"enabled": True})
        world.client.open_trade()
        world.observe(12)
        artifact["open_trade"] = world.evidence()

        # F10: the process identity changes under a pending request.
        world = World(root, monkeypatch, "identity_changed")
        world.control.update({"enabled": True})
        world.client.show_request()

        def replace(t):
            if t >= 2:
                world.client.identity = {
                    **world.client.identity,
                    "pid": 20001,
                    "creation_time_100ns": 134345064188679999,
                }

        world.observe(12, at=replace)
        artifact["identity_changed"] = world.evidence()

        # F11: the requester left the scene; the exact request is still shown.
        world = World(root, monkeypatch, "requester_left_scene")
        world.control.update({"enabled": True})
        world.scene["present"] = False
        world.client.show_request()
        world.observe(12)
        artifact["requester_left_scene"] = world.evidence()
    finally:
        install(None)
    return artifact


def run(tmp_path, monkeypatch, name):
    with monkeypatch.context() as patch:
        artifact = scenario(tmp_path / name, patch)
    path = tmp_path / name / "farmer-request-decline.json"
    path.write_text(json.dumps(artifact, indent=2, sort_keys=True), encoding="utf-8")
    return path


def cancel_presses(evidence):
    return [press for press in evidence["presses"] if press["control"] == "Cancel"]


def test_unapproved_farmer_request_is_declined_once_and_never_strands_the_route(
    tmp_path, monkeypatch
):
    first = run(tmp_path, monkeypatch, "run1")
    second = run(tmp_path, monkeypatch, "run2")
    artifact = json.loads(first.read_text(encoding="utf-8"))

    # No scenario ever presses Accept or opens a trade by itself.
    for name, evidence in artifact.items():
        assert all(p["control"] != "Accept" for p in evidence["presses"]), name

    # F1/F2: one Cancel press ~5 s after the request, farming continues On.
    hunt = artifact["hunting"]
    shown = hunt["marks"]["request_shown"]
    assert [p["point"] for p in hunt["presses"]] == [list(CANCEL)]
    assert 5 <= hunt["presses"][0]["t"] - shown <= 6.5
    assert hunt["claims"] == 1 and hunt["declines"] == 1
    assert hunt["session"]["phase"] == "declined_verified"
    assert hunt["decline_journal"] == "verified" and not hunt["request_visible"]
    assert not hunt["fenced"] and not hunt["trade_open"]
    route = hunt["route"]
    assert not route["failed"] and route["farming_enabled_at_end"] is True
    names = [event[1] for event in route["events"]]
    assert "town_action_failed" not in names and "manual_request_wait" in names
    # The whole fence (approval, decline, settlement) is one bounded wait.
    assert "town_observation_retry" not in names
    reads_after = [a for a in route["town_actions"] if a[0] > hunt["presses"][0]["t"]]
    assert len(reads_after) >= 10  # the hunt loop kept running afterwards

    # F3: the route's own Off does not block the decline; town actions resume.
    town = artifact["town_action"]
    assert len(cancel_presses(town)) == 1 and town["claims"] == 1
    assert town["session"]["phase"] == "declined_verified"
    assert not town["route"]["failed"]
    assert [a[1:] for a in town["route"]["town_actions"]] == [
        ["close", "Shop"],
        ["close", "Inventory"],
        ["supplies", None],
    ]
    assert town["route"]["town_actions"][0][0] > town["presses"][0]["t"]

    # F4: no input while Off without a live route; the restart resolves it.
    live = artifact["live_restart"]
    assert live["while_off"]["presses"] == [] and live["while_off"]["claims"] == 0
    assert live["while_off"]["request_state"] == "decline_pending"
    assert any("Off" in blocker for blocker in live["while_off"]["blockers"])
    assert len(cancel_presses(live)) == 1 and live["claims"] == 1
    assert live["session"]["phase"] == "declined_verified"
    assert not live["route"]["failed"] and not live["fenced"]
    assert [a[1] for a in live["route"]["town_actions"]] == [
        "close",
        "close",
        "supplies",
    ]

    # F5: user Off is respected and reported.
    off = artifact["user_off"]
    assert off["presses"] == [] and off["claims"] == 0
    assert off["request_state"] == "decline_pending" and off["request_visible"]
    assert any("user" in blocker for blocker in off["blockers"])

    # F6: Global Stop and F12 each withhold input; the decline runs after.
    stop = artifact["stop_and_f12"]
    assert len(stop["presses"]) == 1 and stop["presses"][0]["t"] >= 15
    assert stop["claims"] == 1 and stop["session"]["phase"] == "declined_verified"

    # F7: one claim, one press, never replayed; bounded wait -> attention.
    uncertain = artifact["uncertain"]
    assert len(uncertain["presses"]) == 1 and uncertain["claims"] == 1
    assert uncertain["decline_journal"] == "submitted" and uncertain["request_visible"]
    assert uncertain["route"]["failed"]
    events = uncertain["route"]["events"]
    expired = [e for e in events if e[1] == "manual_request_wait_expired"]
    assert (
        len(expired) == 1 and expired[0][0] - uncertain["marks"]["request_shown"] >= 60
    )
    # A pre-input fence refusal never looks like a possibly-transacted action.
    assert "town_action_failed" not in [e[1] for e in events]

    # F8-F10: approved visitor, open trade and identity change are untouched.
    assert artifact["approved"]["presses"] == []
    assert artifact["approved"]["session"]["phase"] == "manual_active"
    assert artifact["open_trade"]["presses"] == []
    assert artifact["open_trade"]["session"]["phase"] == "needs_attention"
    assert artifact["identity_changed"]["presses"] == []
    assert artifact["identity_changed"]["session"]["phase"] == "needs_attention"

    # F11: declined by the request's native UID although the scene lost it.
    left = artifact["requester_left_scene"]
    assert len(cancel_presses(left)) == 1 and left["claims"] == 1

    # F12: repeatable artifact.
    assert first.read_bytes() == second.read_bytes()
