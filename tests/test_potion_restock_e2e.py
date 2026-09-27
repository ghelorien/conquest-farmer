"""Back2Classic restocks keep every HP potion and never strand the farmer.

Live 2026-09-27 on Laptop2 (Suicide, 222 max HP, Back2Classic):
- the Pharmacist tier followed the wallet (Stancher on a poor visit,
  Resolutive on the next) and each visit sold the tier below it as junk;
- 9d3ba06 read the Pharmacist products without a guard, so one failed shop
  read aborted the whole restock (4 tests in test_overnight.py);
- at 200 silver a potion purchase raised "Insufficient funds", three
  restarts spent the hourly budget and the farmer stood in town for 7 h;
- f0afdae's 5-potion field reserve also ran as the final restock check, so a
  restock that could afford only 5 potions raised instead of hunting.

Failure modes, written before the change:
1. A carried potion below today's tier is sold at the Pharmacist or dropped
   in the field.
2. Carried potions of another tier are not counted, so the farmer returns to
   town (or reports potions exhausted) with a bag that still heals.
3. The combat pick and the worker's consume check disagree, so a picked
   potion is refused mid-fight.
4. An America farmer without the level goal changes from its Painkillers.
5. The tier is priced from the client table instead of the live shop, so the
   chosen tier cannot be bought in the planned amount.
6. A failed Pharmacist shop read aborts the whole restock.
7. Running out of silver while buying potions raises and ends the route.
8. After a poor restock the field reserve sends the farmer straight back to
   town, or the final restock check raises although it carries potions.

The scenario runs four Back2Classic restocks and one America restock through
the real OvernightLoop.restock against a simulated Pharmacist, writes
``potion-restock.json`` and must produce the same bytes on a second run.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from conquest import level_goal, overnight, potion_tiers
from conquest.overnight import OvernightLoop, needs_town, supply_counts
from conquest.routes import RouteLibrary
from conquest.town_trade import TownObservationUnavailable, junk_type

STANCHER, RESOLUTIVE, PAINKILLER = 1000000, 1000010, 1000020
LUCKY = 1050000
# Live Back2Classic-style prices: Resolutive dearer than the client table (18).
PRICES = {STANCHER: 5, RESOLUTIVE: 25, PAINKILLER: 60, 1000030: 120, 1002000: 240}
NAMES = {STANCHER: "Stancher", RESOLUTIVE: "Resolutive", PAINKILLER: "Painkiller"}


class Pharmacy:
    """Bag, wallet and the town actions restock uses; one potion per buy."""

    def __init__(self, silver, potions, max_hp, *, shop_failures=0):
        self.silver, self.max_hp = silver, max_hp
        self.shop_failures = shop_failures
        self.next_uid = 100
        self.items = [
            {"uid": 1, "type_id": LUCKY, "amount": 1800, "limit": 2000, "plus": 0, "slot": 0}
        ]
        for kind in potions:
            self.add(kind)
        self.bought, self.sold, self.events = [], [], []

    def add(self, kind):
        self.next_uid += 1
        self.items.append(
            {
                "uid": self.next_uid,
                "type_id": kind,
                "amount": 1,
                "limit": 1,
                "plus": 0,
                "slot": len(self.items),
            }
        )

    def snapshot(self):
        return {
            "items": [dict(i) for i in self.items],
            "equipped_ammo": {"type_id": LUCKY, "amount": 200, "limit": 200},
            "silver": self.silver,
            "capacity": 40,
        }

    def town(self, action, **fields):
        if action == "supplies":
            return self.snapshot()
        if action == "shop":
            if self.shop_failures:
                self.shop_failures -= 1
                raise TownObservationUnavailable("NPC scene changed during observation")
            return {
                "products": [{"type_id": t, "price": p} for t, p in PRICES.items()]
            }
        if action == "buy":
            kind = fields["type_id"]
            price = PRICES[kind]
            if self.silver < price or len(self.items) >= 40:
                raise ValueError("Insufficient funds or inventory room to restock")
            self.silver -= price
            self.add(kind)
            self.bought.append(kind)
            return {"bought": kind, "amount": 1, "price": price, "silver": self.silver}
        if action == "sell":
            item = next(i for i in self.items if i["uid"] == fields["uid"])
            self.items.remove(item)
            self.sold.append(item["type_id"])
            self.silver += 1
            return {"sold": item["type_id"], "silver": self.silver}
        return {"ok": True}

    def potions(self):
        return sorted(i["type_id"] for i in self.items if i["type_id"] in PRICES)


@pytest.fixture
def world(tmp_path, monkeypatch):
    from conquest import banking, equipment, session_plan, world_travel

    monkeypatch.setattr(level_goal, "GOAL", tmp_path / "level-goal.json")
    monkeypatch.setattr(potion_tiers, "TIER", tmp_path / "healing-tier.json")
    monkeypatch.setattr(level_goal, "_silver_cache", (-float("inf"), False))
    monkeypatch.setattr(banking, "fund_restock", lambda loop: None)
    monkeypatch.setattr(banking, "after_shopping", lambda loop, **kw: True)
    monkeypatch.setattr(
        equipment, "EquipmentReview", lambda loop: NS(visit=lambda vendor: None)
    )
    monkeypatch.setattr(session_plan, "upgrade_circuit", lambda loop: False)
    monkeypatch.setattr(world_travel, "travel_to_map", lambda loop, map_id: None)
    monkeypatch.setattr(overnight, "last_verified_price", lambda *a, **k: None)
    return tmp_path


def loop_for(route, pharmacy, level=14):
    loop = OvernightLoop.__new__(OvernightLoop)
    loop.route = route
    loop.cycles = 0
    loop.last_level = level
    loop.travel = lambda target: None
    loop.optional_town_service = lambda: None
    attach(loop, pharmacy)
    return loop


def attach(loop, pharmacy):
    loop.town = pharmacy.town
    loop.record = lambda event, **fields: pharmacy.events.append(event)
    loop.living = lambda: {
        "embedded_controls": {
            "life": {
                "max_hp": pharmacy.max_hp,
                "current_hp": pharmacy.max_hp,
                "map_id": 1002,
                "position": (430, 380),
                "dead_candidate": False,
            }
        }
    }


def visit(loop, pharmacy):
    silver = pharmacy.silver
    carried = pharmacy.potions()
    attach(loop, pharmacy)
    loop.restock()
    counts = supply_counts(pharmacy.snapshot(), loop.route)
    return {
        "silver_before": silver,
        "carried_before": [NAMES.get(t, t) for t in carried],
        "tier": NAMES.get(loop.route.supplies.healing_type),
        "bought": {NAMES[t]: pharmacy.bought.count(t) for t in sorted(set(pharmacy.bought))},
        "sold": [NAMES.get(t, t) for t in pharmacy.sold],
        "potions_after": counts["potions"],
        "silver_after": pharmacy.silver,
        "restocks": loop.cycles,
        "events": sorted(set(e for e in pharmacy.events if "tier" in e)),
    }


@dataclass(frozen=True)
class Item:
    uid: int
    type_id: int
    amount: int = 1
    slot: int = 0


def scenario(root):
    root = Path(root)
    root.mkdir(parents=True)
    level_goal.start(23)
    route = RouteLibrary().load("robin")
    loop = loop_for(route, Pharmacy(0, [], 222))
    rows = {}
    # Modes 1 and 5: 400 silver buys 20 Resolutive at the table price (18)
    # but not at the live 25, so the live-priced tier is Stancher; the three
    # carried Painkillers stay and count.
    first = Pharmacy(400, [PAINKILLER] * 3, 222)
    rows["b2c_live_priced"] = visit(loop, first)
    # Mode 1: a richer visit moves up to Resolutive and keeps the Stanchers.
    richer = Pharmacy(2000, [PAINKILLER] + [STANCHER] * 5, 222)
    rows["b2c_richer"] = visit(loop, richer)
    # Mode 6: the Pharmacist read fails once; the restock keeps its tier.
    blind = Pharmacy(2000, [RESOLUTIVE] * 2, 222, shop_failures=1)
    rows["b2c_shop_read_failed"] = visit(loop, blind)
    # Modes 7 and 8: 30 silver buys six Stanchers, then the wallet is empty.
    broke = Pharmacy(30, [], 222)
    rows["b2c_broke"] = visit(loop, broke)
    departed = rows["b2c_broke"]["potions_after"]
    counts = supply_counts(broke.snapshot(), loop.route)
    # The hunt loop's check: the reserve applies, capped by the departure.
    rows["b2c_broke"]["heads_back_at_departure"] = needs_town(
        counts, loop.route, departed=departed, reserve=True
    )
    rows["b2c_broke"]["heads_back_with_3_left"] = needs_town(
        {**counts, "potions": 3}, loop.route, departed=departed, reserve=True
    )
    rows["b2c_full_trip_heads_back_with_5_left"] = needs_town(
        {**counts, "potions": 5}, loop.route, departed=20, reserve=True
    )
    # Mode 4: an America farmer without the goal keeps buying Painkillers
    # even at 3,000 max HP, and keeps its carried Stanchers.
    level_goal.stop()
    america = Pharmacy(5000, [STANCHER] * 2 + [PAINKILLER], 3000)
    rows["america_no_goal"] = visit(
        loop_for(RouteLibrary().load("robin"), america), america
    )
    rows["america_no_goal"]["heads_back_with_1_potion"] = needs_town(
        {**supply_counts(america.snapshot(), RouteLibrary().load("robin")), "potions": 1},
        RouteLibrary().load("robin"),
        departed=20,
        reserve=True,
    )
    path = root / "potion-restock.json"
    path.write_text(json.dumps(rows, indent=2, sort_keys=True), encoding="utf-8")
    return path


def test_potion_restock_e2e(world):
    first = scenario(world / "pc-a")
    potion_tiers.TIER.unlink(missing_ok=True)
    level_goal.GOAL.unlink(missing_ok=True)
    second = scenario(world / "pc-b")
    assert first.read_bytes() == second.read_bytes()
    rows = json.loads(first.read_text(encoding="utf-8"))

    live = rows["b2c_live_priced"]
    assert live["tier"] == "Stancher" and live["bought"] == {"Stancher": 17}
    assert live["sold"] == [] and live["potions_after"] == 20

    richer = rows["b2c_richer"]
    assert richer["tier"] == "Resolutive" and richer["bought"] == {"Resolutive": 14}
    assert richer["sold"] == [] and richer["potions_after"] == 20

    blind = rows["b2c_shop_read_failed"]
    assert blind["tier"] == "Resolutive" and blind["bought"] == {"Resolutive": 18}
    assert blind["restocks"] and "healing_tier_unchanged" in blind["events"]

    broke = rows["b2c_broke"]
    assert broke["bought"] == {"Stancher": 6} and broke["silver_after"] == 0
    assert broke["restocks"] and broke["potions_after"] == 6
    assert broke["heads_back_at_departure"] is False
    assert broke["heads_back_with_3_left"] is True
    assert rows["b2c_full_trip_heads_back_with_5_left"] is True

    america = rows["america_no_goal"]
    assert america["tier"] == "Painkiller" and america["bought"] == {"Painkiller": 17}
    assert america["sold"] == [] and america["potions_after"] == 20
    assert america["heads_back_with_1_potion"] is False


def test_no_hp_potion_is_junk_and_every_tier_counts_and_heals(world):
    # Modes 1-3 for the field: nothing sold, everything counted, every pick
    # accepted by the worker's consume check.
    potion_tiers.set_active(1002000)
    for kind in potion_tiers.HEALING_POTIONS:
        assert not junk_type(kind)
    assert junk_type(1001000)  # mana stays junk for the archer
    carried = [Item(1, STANCHER, 2, 0), Item(2, RESOLUTIVE, 1, 1), Item(3, PAINKILLER, 1, 2)]
    bag = NS(items=carried, count=lambda t: sum(i.amount for i in carried if i.type_id == t))
    assert potion_tiers.count(bag) == 4
    assert potion_tiers.count(bag, PAINKILLER) == 4
    assert potion_tiers.count(bag, 1060020) == 0  # a non-potion item counts exactly
    assert potion_tiers.pick(bag, 50).type_id == STANCHER
    assert potion_tiers.pick(bag, 90).type_id == RESOLUTIVE
    assert potion_tiers.pick(bag, 400).type_id == PAINKILLER
    from conquest.healing import consume_inventory_potion

    trade = NS(
        life=lambda any_map: NS(current_hp=222, max_hp=222),
        inventory=NS(read=lambda: bag),
    )
    for item in carried:
        assert consume_inventory_potion(trade, item.uid) == {
            "consumed": False,
            "reason": "already_full_health",
        }
