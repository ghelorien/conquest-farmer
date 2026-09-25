"""End-to-end: required restock visit -> ammunition tier selection -> funding ->
Blacksmith arrow purchase, for a character whose carried arrows are unusable.

Live incident 2026-09-25 05:05 (level-95 Bandit farmer): the equipped SpeedArrow
stack had one arrow (Scatter needs three) and there was no Speed reserve.
adopt_ammunition found no usable tier, fell back to the route's saved
arrow_type (IronArrow), and the visit funded and bought one IronArrow pack. The
remnant plus the new Iron pack then hit the two-pack cap.

Real code under test: OvernightLoop.prepare_supplies -> adopt_ammunition
(arrow_upgrades.current_arrow) -> needs_town -> restock -> return_scroll,
world_travel.travel_to_map, banking.fund_restock (shopping_budget,
open_warehouse, transfer) -> pharmacist refill -> recycle_small_arrows ->
EquipmentReview.visit(5) (review_arrows / choose_arrow_upgrade) -> buy_supply
-> banking.after_shopping; the saved profiles (routes, cities, archer catalog)
and the route's events.jsonl/status.json writers.

Fakes exist only at process boundaries:
- game process: conquest.overnight.request (health, controls and every "town"
  memory read / input). It applies the same pre-input purchase checks as the
  worker (two-pack cap, silver and bag room) and sells only the prices listed
  in the recorded archer shop catalog;
- movement input (OvernightLoop.travel) and the installed terrain file;
- merchant app: conquest.merchants.bridge.request is not running (OSError).

Failure modes, written before the fix:
 F1  Level 95, only an unusable SpeedArrow remnant (1 arrow) equipped, no
     reserve: the route's saved IronArrow default is adopted and bought instead
     of SpeedArrow (the live incident).
 F2  The route's saved default overrides the level-best tier whenever nothing
     usable is carried (turtledove's LuckyArrow at level 40; Bandit's
     IronArrow at level 20, where Iron is not even level-eligible).
 F3  Level 40 with nothing usable does not select IronArrow.
 F4  Level 20 with nothing usable selects anything but LuckyArrow.
 F5  Level 95 with a usable IronArrow pack: the Iron pack is sold or
     discarded. (Buying/funding a SpeedArrow upgrade on a required visit when
     there is pack room is the existing, unchanged behaviour.)
 F6  Carrying two or more packs (live aftermath: Iron 1000 in the bag plus the
     equipped Speed remnant): any arrow purchase.
 F7  The withdrawal budget is priced from the route default instead of the
     selected tier, so the selected pack is unaffordable at the shop.
 F8  The level-best tier is unaffordable (wallet plus stored silver below its
     price; the worker refuses before input): the restock stops in town
     instead of buying the next lower affordable eligible tier (Speed -> Iron
     -> Lucky), or the fallback is not recorded as an event.
 F8b The level-best tier is affordable: a fallback tier is bought instead.
 F9  Remnants: the equipped remnant is sold, a bag remnant survives a required
     visit, or a usable pack is recycled.
 F10 The artifact is not repeatable.

Each scenario writes <tmp>/<run>/<scenario>/arrow-tier-e2e.json (game inputs,
route events and final carried arrows), is re-read from disk for the
assertions, and is run twice to prove the artifact is byte-identical.
"""

import ctypes
import json
import shutil
import time
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

REPO = Path(__file__).resolve().parents[1]
START = 1_790_000_000.0
TARGET = {"pid": 4343, "creation_time_100ns": 23}
LUCKY, IRON, SPEED, POTION = 1050000, 1050001, 1050002, 1000020
NAMES = {LUCKY: "LuckyArrow", IRON: "IronArrow", SPEED: "SpeedArrow"}
# Physical pack sizes (inventory limits) verified in live purchases.
PACK = {LUCKY: 200, IRON: 1000, SPEED: 5000}


class Clock:
    def __init__(self):
        self.now = START

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += max(0.0, seconds)


def shop_products(map_id):
    """Blacksmith arrows exactly as the live shop reader recorded them."""
    catalog = json.loads(
        (REPO / "profiles/archer-shop-catalog.json").read_text(encoding="utf-8")
    )
    products = catalog["cities"][str(map_id)]["5"]["products"]
    return [
        {
            "type_id": p["type_id"],
            "name": p["name"],
            "price": p["price"],
            "level": p["level"],
            "profession": p.get("profession") or 40,
            "attack_min": p.get("attack_min") or 0,
            "attack_max": p.get("attack_max") or 0,
        }
        for p in products
        if p["type_id"] in NAMES
    ]


class Game:
    """Farmer client plus embedded worker at the game-process boundary."""

    def __init__(self, clock, scenario):
        self.clock = clock
        self.map_id = scenario["map_id"]
        self.level = scenario["level"]
        self.silver = scenario["silver"]
        self.stored = scenario["stored"]
        self.products = shop_products(self.map_id)
        self.next_uid = 100
        self.items = [dict(i) for i in scenario["bag"]]
        for _ in range(scenario["potions"]):
            self.items.append(self._new(POTION, 1, 1))
        self.equipped = dict(scenario["equipped"]) if scenario["equipped"] else None
        self.control = {"enabled": True, "revision": 3, "paused": False}
        self.trace = []

    def _new(self, kind, amount, limit):
        self.next_uid += 1
        return {
            "uid": self.next_uid,
            "type_id": kind,
            "amount": amount,
            "limit": limit,
            "plus": 0,
            "slot": None,
        }

    def add(self, kind, **fields):
        self.trace.append(
            {"t": round(self.clock.now - START, 3), "kind": kind, **fields}
        )

    def arrows(self):
        rows = [
            {"type_id": i["type_id"], "amount": i["amount"]}
            for i in self.items
            if i["type_id"] in NAMES
        ]
        return {
            "bag": sorted(rows, key=lambda r: (r["type_id"], r["amount"])),
            "equipped": None
            if not self.equipped
            else {
                "type_id": self.equipped["type_id"],
                "amount": self.equipped["amount"],
            },
        }

    def product(self, kind):
        return next(p for p in self.products if p["type_id"] == kind)

    def gear(self):
        arrows = {}
        if self.equipped:
            p = self.product(self.equipped["type_id"])
            arrows = {
                "arrows": {
                    "uid": self.equipped["uid"],
                    "type_id": p["type_id"],
                    "name": p["name"],
                    "level": p["level"],
                    "profession": p["profession"],
                    "attack_min": p["attack_min"],
                    "attack_max": p["attack_max"],
                }
            }
        return {
            "level": self.level,
            "profession": 41,
            "map_id": self.map_id,
            "equipment": arrows,
        }

    def supplies(self):
        return json.loads(
            json.dumps(
                {
                    "items": self.items,
                    "equipped_ammo": self.equipped,
                    "capacity": 40,
                    "silver": self.silver,
                }
            )
        )

    def pack_count(self):
        return sum(
            1 for i in self.items if i["type_id"] in NAMES and i["amount"] > 0
        ) + int(bool(self.equipped and self.equipped["amount"] > 0))

    def health(self):
        return {
            "target": dict(TARGET),
            "window": {
                "hwnd": 5,
                "root_hwnd": 5,
                "foreground": 5,
                "minimized": False,
            },
            "embedded_controls": {
                "control": dict(self.control),
                "life": {
                    "map_id": self.map_id,
                    "position": [200, 230],
                    "dead_candidate": False,
                    "current_hp": 1500,
                    "max_hp": 1500,
                },
                "observed_at": self.clock.now,
                "external_execution": False,
                "manual_mouse": False,
            },
        }

    def request(self, info, operation, body=None):
        assert info == "fake-worker.json"
        self.clock.now += 0.05
        if operation == "health":
            return self.health()
        if operation == "controls":
            self.add("controls", body=dict(body))
            self.control.update(
                {k: v for k, v in body.items() if k in ("enabled", "paused")}
            )
            return {"ok": True}
        assert operation == "town", operation
        body = {k: v for k, v in body.items() if k != "expires_at"}
        action = body.pop("action")
        if action == "supplies":
            return self.supplies()
        if action == "gear":
            return self.gear()
        if action == "shop":
            vendor = body["vendor_type"]
            if vendor == 5:
                return {"products": [dict(p) for p in self.products]}
            if vendor == 3:
                return {
                    "products": [{"type_id": POTION, "name": "Painkiller", "price": 60}]
                }
            return {"products": []}
        if action in ("close", "open-bank", "warehouse-open"):
            return {}
        if action == "open":
            self.add("open", vendor_type=body["vendor_type"])
            return {"opened": body["vendor_type"]}
        if action == "vendor-status":
            return {"reachable": True}
        if action == "warehouse-locate":
            return {"position": [210, 240]}
        if action == "warehouse-money":
            return {"silver": self.silver, "stored_silver": self.stored}
        if action == "warehouse-items":
            return {"items": []}
        if action in ("warehouse-money-withdraw", "warehouse-money-deposit"):
            amount = body["amount"]
            sign = 1 if action.endswith("withdraw") else -1
            assert 0 < amount <= (self.stored if sign > 0 else self.silver)
            self.silver += sign * amount
            self.stored -= sign * amount
            self.add(action, amount=amount, silver=self.silver, stored=self.stored)
            return {
                "verified": True,
                "amount": amount,
                "silver": self.silver,
                "stored_silver": self.stored,
            }
        if action == "sell_partial_arrow":
            item = next(i for i in self.items if i["uid"] == body["uid"])
            assert item["type_id"] in NAMES
            self.items.remove(item)
            self.add(
                "sell_partial_arrow",
                type=NAMES[item["type_id"]],
                amount=item["amount"],
            )
            return {"sold": body["uid"], "verified": True}
        if action == "equip-arrows":
            item = next(i for i in self.items if i["uid"] == body["uid"])
            self.items.remove(item)
            if self.equipped and self.equipped["amount"] > 0:
                self.items.append(self.equipped)
            self.equipped = item
            self.add("equip-arrows", type=NAMES[item["type_id"]], amount=item["amount"])
            return {"equipped": body["uid"], "verified": True}
        if action == "buy":
            kind = body["type_id"]
            if body["vendor_type"] == 3:
                assert kind == POTION
                price = 60
            else:
                assert body["vendor_type"] == 5 and kind in NAMES
                # Same pre-input refusals as town_trade's worker purchase.
                if self.pack_count() >= 2:
                    raise ValueError(
                        "Arrow purchase blocked: already carrying two or more packs"
                    )
                price = self.product(kind)["price"]
            if self.silver < price or len(self.items) >= 40:
                self.add("buy_refused", type=NAMES.get(kind, kind), price=price)
                raise ValueError("Insufficient funds or inventory room to restock")
            self.silver -= price
            if kind == POTION:
                self.items.append(self._new(POTION, 1, 1))
            else:
                self.items.append(self._new(kind, PACK[kind], PACK[kind]))
            self.add(
                "buy",
                type=NAMES.get(kind, "Painkiller"),
                price=price,
                silver=self.silver,
            )
            return {"bought": kind, "price": price, "verified": True}
        raise AssertionError((action, body))


SCENARIOS = {
    # F1/F7: the live 05:05 state.
    "live_speed_remnant_l95": {
        "route": "bandit",
        "map_id": 1011,
        "level": 95,
        "equipped": {"uid": 7, "type_id": SPEED, "amount": 1, "limit": 5000},
        "bag": [],
        "potions": 5,
        "silver": 1200,
        "stored": 200000,
    },
    # F5/F6: the live state after the incident (Iron pack plus Speed remnant).
    "live_aftermath_iron_pack_and_speed_remnant_l95": {
        "route": "bandit",
        "map_id": 1011,
        "level": 95,
        "equipped": {"uid": 7, "type_id": SPEED, "amount": 1, "limit": 5000},
        "bag": [
            {
                "uid": 8,
                "type_id": IRON,
                "amount": 1000,
                "limit": 1000,
                "plus": 0,
                "slot": 1,
            }
        ],
        "potions": 0,
        "silver": 50000,
        "stored": 200000,
    },
    # F5/F9: after the reload the Iron pack is equipped and the remnant is in
    # the bag; a potion-triggered visit with ample silver for the (existing)
    # SpeedArrow upgrade.
    "usable_iron_equipped_l95": {
        "route": "bandit",
        "map_id": 1011,
        "level": 95,
        "equipped": {"uid": 8, "type_id": IRON, "amount": 1000, "limit": 1000},
        "bag": [
            {
                "uid": 7,
                "type_id": SPEED,
                "amount": 1,
                "limit": 5000,
                "plus": 0,
                "slot": 1,
            }
        ],
        "potions": 0,
        "silver": 50000,
        "stored": 200000,
    },
    # The next arrow restock once that Iron pack is spent.
    "iron_spent_l95": {
        "route": "bandit",
        "map_id": 1011,
        "level": 95,
        "equipped": {"uid": 8, "type_id": IRON, "amount": 2, "limit": 1000},
        "bag": [],
        "potions": 5,
        "silver": 1200,
        "stored": 200000,
    },
    # F2/F3: turtledove saves LuckyArrow; level 40 must buy IronArrow.
    "level40_lucky_route_iron_remnant": {
        "route": "turtledove",
        "map_id": 1002,
        "level": 40,
        "equipped": {"uid": 8, "type_id": IRON, "amount": 2, "limit": 1000},
        "bag": [],
        "potions": 5,
        "silver": 1200,
        "stored": 50000,
    },
    # F2/F4: Bandit saves IronArrow; level 20 cannot use it.
    "level20_iron_route_lucky_remnant": {
        "route": "bandit",
        "map_id": 1011,
        "level": 20,
        "equipped": {"uid": 9, "type_id": LUCKY, "amount": 1, "limit": 200},
        "bag": [],
        "potions": 5,
        "silver": 1200,
        "stored": 50000,
    },
    # F8: level 40 cannot pay for IronArrow either: LuckyArrow.
    "insufficient_silver_for_iron_l40": {
        "route": "turtledove",
        "map_id": 1002,
        "level": 40,
        "equipped": {"uid": 8, "type_id": IRON, "amount": 2, "limit": 1000},
        "bag": [],
        "potions": 5,
        "silver": 1200,
        "stored": 2000,
    },
    # F8: not enough silver anywhere for a SpeedArrow pack.
    "insufficient_silver_for_speed_l95": {
        "route": "bandit",
        "map_id": 1011,
        "level": 95,
        "equipped": {"uid": 7, "type_id": SPEED, "amount": 1, "limit": 5000},
        "bag": [],
        "potions": 5,
        "silver": 1200,
        "stored": 10000,
    },
}

KEPT_EVENTS = (
    "ammunition_selected",
    "arrow_purchase_deferred",
    "arrow_upgrade_deferred",
    "arrow_upgrade_buying",
    "arrows_upgraded",
    "arrow_tier_fallback",
    "optional_purchase_deferred",
    "silver_withdraw",
    "silver_deposit",
    "restock_complete",
    "supplies_ready",
)


def run_scenario(root, monkeypatch, name):
    from conquest import banking, overnight, world_travel
    from conquest.merchants import bridge
    from conquest.routes import RouteLibrary

    scenario = SCENARIOS[name]
    root.mkdir(parents=True)
    shutil.copytree(REPO / "profiles", root / "profiles")
    monkeypatch.chdir(root)  # Relative .runtime/ and reports/ state lands here.
    clock = Clock()
    monkeypatch.setattr(time, "time", clock.time)
    monkeypatch.setattr(time, "monotonic", clock.time)
    monkeypatch.setattr(time, "sleep", clock.sleep)
    monkeypatch.setattr(ctypes.windll.user32, "GetAsyncKeyState", lambda key: 0)
    banking.CONFIG.write_text(
        json.dumps(
            {"enabled": True, "withdraw_essentials": True, "transport_reserve": 200}
        ),
        encoding="utf-8",
    )
    game = Game(clock, scenario)
    monkeypatch.setattr(overnight, "request", game.request)

    def installed_terrain(client_root, map_id):
        return NS(map_id=map_id)

    monkeypatch.setattr(world_travel, "read_terrain", installed_terrain)

    def merchant_app(body):
        raise OSError("merchant app is not running")

    monkeypatch.setattr(bridge, "request", merchant_app)
    app_state = root / "reports/desktop-farming/app-state.json"
    app_state.parent.mkdir(parents=True)
    app_state.write_text(
        json.dumps({"worker_info_path": "fake-worker.json"}), encoding="utf-8"
    )

    loop = overnight.OvernightLoop.__new__(overnight.OvernightLoop)
    loop.route = RouteLibrary().load(scenario["route"])
    saved_arrow = loop.route.supplies.arrow_type
    loop.terrain = NS(map_id=scenario["map_id"])
    loop.info = "fake-worker.json"
    loop.care = NS(check=lambda health: None, session=None)
    loop.stepper = None
    loop.identity = None
    loop.phase = "hunting"
    loop.cycles = 0
    loop.deadline = None
    loop.town_visit = None
    loop.output = root / "reports/overnight"
    loop.output.mkdir(parents=True)
    loop.stop_path = root / ".runtime/overnight.stop"
    loop.state = {"pid": 1, "route": loop.route.id, "cycles": 0}

    def travel(destination, **fields):  # Movement input boundary.
        game.add("travel", destination=list(destination))
        clock.now += 1

    loop.travel = travel
    before = game.arrows()
    error = None
    try:
        loop.prepare_supplies()
    except ValueError as exc:
        error = str(exc)
    events = [
        json.loads(line)
        for line in (loop.output / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    artifact = {
        "scenario": name,
        "inputs": {
            **{k: v for k, v in scenario.items() if k not in ("bag", "equipped")},
            "route_saved_arrow": NAMES[saved_arrow],
            "carried_arrows": before,
        },
        "trace": game.trace,
        "route_events": [
            {
                "t": round(row["time"] - START, 3),
                "event": row["event"],
                **{
                    key: row[key]
                    for key in (
                        "activity",
                        "arrow_type",
                        "arrow_packs",
                        "amount",
                        "price",
                        "silver",
                    )
                    if key in row
                },
            }
            for row in events
            if row["event"] in KEPT_EVENTS
        ],
        "error": error,
        "final": {
            "arrows": game.arrows(),
            "silver": game.silver,
            "stored": game.stored,
            "selected_arrow": NAMES[loop.route.supplies.arrow_type],
            "arrows_restock_to": loop.route.supplies.arrows_restock_to,
            "cycles": loop.cycles,
        },
    }
    path = root / name / "arrow-tier-e2e.json"
    path.parent.mkdir()
    path.write_text(json.dumps(artifact, indent=2, sort_keys=True), encoding="utf-8")
    return path


def arrow_buys(trace):
    return [
        row["type"]
        for row in trace
        if row["kind"] == "buy" and row["type"] in NAMES.values()
    ]


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_required_visit_buys_level_best_or_next_affordable_tier(
    tmp_path, monkeypatch, name
):
    first = run_scenario(tmp_path / "first", monkeypatch, name)
    second = run_scenario(tmp_path / "second", monkeypatch, name)
    # F10: deterministic, repeatable artifact.
    assert first.read_bytes() == second.read_bytes()

    artifact = json.loads(first.read_text(encoding="utf-8"))
    trace, events, final = (
        artifact["trace"],
        artifact["route_events"],
        artifact["final"],
    )
    buys = arrow_buys(trace)
    sold = [row for row in trace if row["kind"] == "sell_partial_arrow"]
    withdrawn = sum(
        row["amount"] for row in trace if row["kind"] == "warehouse-money-withdraw"
    )
    selected = [e["arrow_type"] for e in events if e["event"] == "ammunition_selected"]
    fallbacks = [e for e in events if e["event"] == "arrow_tier_fallback"]
    # F9: nothing usable is ever recycled; only 1-2 arrow bag remnants.
    assert all(row["amount"] < 3 for row in sold)
    # F8: every required visit completes with usable arrows.
    assert artifact["error"] is None and final["cycles"] == 1
    # F8b: a fallback happens only in the unaffordable scenarios.
    assert bool(fallbacks) == name.startswith("insufficient_silver")

    if name == "live_speed_remnant_l95":
        # F1: SpeedArrow, never the route's saved IronArrow.
        assert selected == [SPEED] and final["selected_arrow"] == "SpeedArrow"
        assert buys == ["SpeedArrow"]
        assert final["arrows_restock_to"] == 10000
        # F7: the visit withdrew enough for the live SpeedArrow price.
        assert 1200 + withdrawn >= 34000 + 3000
        # F9: the equipped remnant is kept; the cap then stops a second pack.
        assert not sold
        assert final["arrows"]["equipped"] == {"type_id": SPEED, "amount": 1}
        assert any(e["event"] == "arrow_purchase_deferred" for e in events)
        assert artifact["error"] is None and final["cycles"] == 1
    elif name == "live_aftermath_iron_pack_and_speed_remnant_l95":
        # F5/F6: adopt the Iron pack, keep it, buy no arrows at all.
        assert final["selected_arrow"] == "IronArrow"
        assert buys == [] and not sold
        assert final["arrows"] == artifact["inputs"]["carried_arrows"]
        assert artifact["error"] is None and final["cycles"] == 1
    elif name == "usable_iron_equipped_l95":
        # F5: the usable Iron pack is kept (moved to the bag as the spare) when
        # the existing upgrade buys and equips SpeedArrow. F9: remnant recycled.
        assert selected[0] == IRON and final["selected_arrow"] == "SpeedArrow"
        assert buys == ["SpeedArrow"]
        assert [(r["type"], r["amount"]) for r in sold] == [("SpeedArrow", 1)]
        assert final["arrows"] == {
            "bag": [{"type_id": IRON, "amount": 1000}],
            "equipped": {"type_id": SPEED, "amount": 5000},
        }
    elif name == "iron_spent_l95":
        assert final["selected_arrow"] == "SpeedArrow"
        assert buys == ["SpeedArrow"]
        assert final["arrows"]["equipped"]["type_id"] == SPEED
        assert artifact["error"] is None and final["cycles"] == 1
    elif name == "level40_lucky_route_iron_remnant":
        # F2/F3: the saved LuckyArrow default does not win at level 40.
        assert final["selected_arrow"] == "IronArrow"
        assert buys == ["IronArrow"]
        assert artifact["error"] is None and final["cycles"] == 1
    elif name == "level20_iron_route_lucky_remnant":
        # F2/F4: Iron needs level 32; LuckyArrow is the only eligible tier.
        assert final["selected_arrow"] == "LuckyArrow"
        assert buys == ["LuckyArrow"]
        assert artifact["error"] is None and final["cycles"] == 1
    elif name == "insufficient_silver_for_speed_l95":
        # F8: all stored silver is withdrawn, the worker refuses SpeedArrow
        # before input, and the visit buys IronArrow as the old code did.
        assert selected == [SPEED] and withdrawn == 10000
        assert [r["type"] for r in trace if r["kind"] == "buy_refused"] == [
            "SpeedArrow"
        ]
        assert buys == ["IronArrow"]
        assert [(f["arrow_type"], f["price"]) for f in fallbacks] == [(IRON, 4800)]
        assert final["selected_arrow"] == "IronArrow"
        assert final["arrows_restock_to"] == 2000
    elif name == "insufficient_silver_for_iron_l40":
        # F8: Iron unaffordable at level 40: LuckyArrow.
        assert selected == [IRON] and withdrawn == 2000
        assert buys == ["LuckyArrow"]
        assert [(f["arrow_type"], f["price"]) for f in fallbacks] == [(LUCKY, 200)]
        assert final["selected_arrow"] == "LuckyArrow"
    else:  # pragma: no cover
        raise AssertionError(name)
    # No scenario ever buys a tier above the character's level.
    levels = {"LuckyArrow": 1, "IronArrow": 32, "SpeedArrow": 73}
    assert all(levels[b] <= artifact["inputs"]["level"] for b in buys)
