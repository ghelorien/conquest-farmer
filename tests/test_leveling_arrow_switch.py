"""A leveling archer moves up to IronArrows with LuckyArrows still carried.

Live 2026-09-27 22:52-22:55 (Suicide, level 32, Bandits, release 66e2727): it
came in for potions (1 left) with 415 LuckyArrows and 8,694 silver. The
Blacksmith review chose IronArrows, then "arrow_upgrade_deferred: two-pack
purchase cap reached": the cap counted the LuckyArrow stacks. The refill
topped the LuckyArrows back up to 1,209. Potions ran out before arrows, so the
LuckyArrows were never used up and IronArrows never came (Alex, 22:4x: "Make
sure to use the upgraded arrows too").

Failure modes, written before the change:
1. Carried LuckyArrow stacks keep a leveling archer from buying IronArrows.
2. The better tier loses its own cap (more than two IronArrow packs).
3. LuckyArrow refills stop counting carried IronArrow stacks (the eight-pack
   limit covers every tier).
4. America farmers lose the two-pack rule across tiers.
5. With the IronArrows spent in the field, the farmer walks to town although
   it still carries LuckyArrows.
6. The field fallback hides a real shortage (potions, no other usable tier)
   or runs after the farmer was scrolled home.
"""

import copy
from types import SimpleNamespace as NS

import pytest

from conquest import arrow_upgrades, banking, level_goal
from conquest.discord_notify import write_json
from conquest.overnight import OvernightLoop
from conquest.routes import RouteLibrary

LUCKY, IRON, PAINKILLER = 1050000, 1050001, 1000020
PRICES = {LUCKY: 200, IRON: 4800, 1050002: 34000}


def arrows(kind, attack, uid=99):
    return dict(
        uid=uid, type_id=kind, name=arrow_upgrades.NORMAL_ARROWS[kind],
        level=1 if kind == LUCKY else 32, profession=40, sex=0, plus=0, gem1=0,
        gem2=0, attack_min=attack, attack_max=attack, defense=0, dodge=0,
    )


def gear(kind=LUCKY):
    return {
        "level": 32,
        "profession": 41,
        "map_id": 1011,
        "equipment": {"arrows": arrows(kind, 10 if kind == LUCKY else 50)},
    }


def stack(uid, kind, amount, slot):
    limit = 200 if kind == LUCKY else 1000
    return {"uid": uid, "type_id": kind, "amount": amount, "limit": limit, "plus": 0, "slot": slot}


def bag(*, lucky=(200, 200), equipped=(LUCKY, 15), iron=(), potions=5, silver=8694):
    items = [stack(10 + n, LUCKY, a, n) for n, a in enumerate(lucky)]
    items += [stack(30 + n, IRON, a, 10 + n) for n, a in enumerate(iron)]
    items += [
        {"uid": 50 + n, "type_id": PAINKILLER, "amount": 1, "limit": 1, "plus": 0, "slot": 20 + n}
        for n in range(potions)
    ]
    kind, amount = equipped
    return {
        "silver": silver,
        "items": items,
        "capacity": 40,
        "equipped_ammo": {**stack(99, kind, amount, None), "slot": None},
    }


@pytest.fixture
def leveling(monkeypatch):
    monkeypatch.setattr(arrow_upgrades, "arrow_pack_price", PRICES.get)
    write_json(banking.STATUS, {"stored_silver": 0})
    level_goal.start(level_goal.SCATTER_LEVEL)
    yield
    level_goal.stop()


def test_lower_tier_stacks_do_not_count_against_a_better_tier(leveling, monkeypatch):
    carried = bag(lucky=(200,) * 7)
    # The 15-arrow quiver is a remnant (a quarter of a 200-arrow pack or less).
    assert arrow_upgrades.arrow_pack_count(carried) == 7
    # 1: seven LuckyArrow packs leave room for IronArrows.
    assert arrow_upgrades.arrow_pack_count(carried, IRON) == 0
    arrow_upgrades.require_arrow_purchase_room(carried, IRON)
    # 2: LEVELING_IRON_PACKS IronArrow packs are the IronArrow limit.
    carried["items"] += [
        stack(30 + n, IRON, 1000, 30 + n)
        for n in range(arrow_upgrades.LEVELING_IRON_PACKS)
    ]
    with pytest.raises(ValueError, match="maximum packs"):
        arrow_upgrades.require_arrow_purchase_room(carried, IRON)
    # 3: a LuckyArrow refill counts every tier against its eight packs.
    assert arrow_upgrades.arrow_pack_count(carried, LUCKY) == 7 + arrow_upgrades.LEVELING_IRON_PACKS
    with pytest.raises(ValueError, match="maximum packs"):
        arrow_upgrades.require_arrow_purchase_room(carried, LUCKY)
    # 4: America farmers keep one equipped pack and one spare across tiers.
    level_goal.stop()
    lucky_only = bag(lucky=(200,))
    assert arrow_upgrades.arrow_pack_count(lucky_only, IRON) == 2
    with pytest.raises(ValueError, match="maximum packs"):
        arrow_upgrades.require_arrow_purchase_room(lucky_only, IRON)


class Town:
    """The town bridge: bag, gear and the Blacksmith (worker guards kept)."""

    def __init__(self, carried, state, life_map=1011):
        self.bag, self.state, self.calls = carried, state, []
        self.life_map = life_map

    def __call__(self, action, **fields):
        self.calls.append((action, fields))
        if action == "supplies":
            return copy.deepcopy(self.bag)
        if action == "gear":
            return copy.deepcopy(self.state)
        if action in ("close", "open"):
            return {}
        if action == "shop":
            return {"products": copy.deepcopy(PRODUCTS)}
        if action == "buy":
            # The worker's guard (town_trade) before any shop input.
            arrow_upgrades.require_arrow_purchase_room(self.bag, fields["type_id"])
            price = PRICES[fields["type_id"]]
            assert self.bag["silver"] >= price
            self.bag["silver"] -= price
            self.bag["items"].append(stack(70 + len(self.calls), fields["type_id"], 1000, 39))
            return {"bought": fields["type_id"], "amount": 1000, "price": price}
        if action == "equip-arrows":
            item = next(i for i in self.bag["items"] if i["uid"] == fields["uid"])
            self.bag["items"].remove(item)
            old = self.bag["equipped_ammo"]
            if old and old["amount"]:
                self.bag["items"].append({**old, "slot": 38})
            self.bag["equipped_ammo"] = {**item, "slot": None}
            self.state["equipment"]["arrows"] = arrows(item["type_id"], 50, item["uid"])
            return {"equipped": item["uid"]}
        raise AssertionError(action)


def loop_with(town):
    loop = OvernightLoop.__new__(OvernightLoop)
    loop.route = RouteLibrary().load("bandit")
    loop.town = town
    loop.events = []
    loop.record = lambda event, **fields: loop.events.append((event, fields))
    loop.health = lambda: {"embedded_controls": {"life": {"map_id": town.life_map}}}
    return loop


PRODUCTS = [
    {**arrows(LUCKY, 10), "price": 200},
    {**arrows(IRON, 50), "price": 4800},
]


def test_the_live_restock_buys_ironarrows_and_keeps_the_luckyarrows(leveling):
    # 1: 22:54, 415 LuckyArrows (15 equipped, two packs), 8,694 silver.
    town = Town(bag(), gear())
    loop = loop_with(town)
    arrow_upgrades.review_arrows(loop, PRODUCTS, town("gear"), town.bag["silver"])
    assert [f["type_id"] for a, f in town.calls if a == "buy"] == [IRON]
    assert town.bag["equipped_ammo"]["type_id"] == IRON
    assert loop.route.supplies.arrow_type == IRON
    # The LuckyArrows stay carried as the fallback, and the refill now tops up
    # IronArrows only (a second pack keeps 3,000 silver: none here).
    assert sum(i["amount"] for i in town.bag["items"] if i["type_id"] == LUCKY) == 415
    assert "arrow_upgrade_deferred" not in [e for e, _ in loop.events]
    assert loop.buy_supply(5, loop.route.supplies.arrow_type) is False
    assert [f["type_id"] for a, f in town.calls if a == "buy"] == [IRON]


def test_spent_ironarrows_hunt_on_with_the_carried_luckyarrows(leveling):
    # 5: two IronArrows left, 415 LuckyArrows and ten potions carried.
    town = Town(bag(lucky=(200, 200, 15), equipped=(IRON, 2), potions=10), gear(IRON))
    loop = loop_with(town)
    assert loop.route.supplies.arrow_type == IRON
    assert loop.carried_arrow_fallback() is True
    assert loop.route.supplies.arrow_type == LUCKY
    assert ("arrow_fallback", IRON) in [
        (e, f.get("previous_arrow_type")) for e, f in loop.events
    ]
    assert not [a for a, _ in town.calls if a in ("buy", "open", "equip-arrows")]


@pytest.mark.parametrize(
    "case, carried, life_map",
    [
        # Potions ran out with IronArrows left: that trip is for potions.
        ("iron_left", dict(lucky=(200, 200, 15), equipped=(IRON, 600), potions=0), 1011),
        # The combat loop read a TwinCityGate out of a losing fight.
        ("scrolled_home", dict(lucky=(200, 200, 15), equipped=(IRON, 2), potions=10), 1002),
        ("nothing_else", dict(lucky=(), equipped=(IRON, 2), potions=10), 1011),
        # One potion is the Bandit route's trip reserve.
        ("potions_at_reserve", dict(lucky=(200, 200, 15), equipped=(IRON, 2), potions=1), 1011),
    ],
)
def test_the_fallback_never_hides_a_town_trip(leveling, case, carried, life_map):
    # 6
    town = Town(bag(**carried), gear(IRON), life_map)
    loop = loop_with(town)
    assert loop.carried_arrow_fallback() is False
    assert "arrow_fallback" not in [e for e, _ in loop.events]


class Done(Exception):
    pass


def test_the_route_loop_hunts_on_instead_of_restocking(leveling, monkeypatch):
    # 5 end to end through OvernightLoop._run_route, unrelated work stubbed.
    from conquest import (
        city_travel,
        manual_storage_recovery,
        merchant_loop_acceptance,
        meteor_banking,
        restock_cash_tail,
        restock_restart,
        restock_town_recovery,
        scatter_training,
        storage_overflow,
        town_visit,
        urgent_town_recovery,
    )
    from conquest.merchants import delivery_journey, delivery_operation

    for module, name, value in [
        (delivery_journey, "reconcile_pending_scroll", lambda loop: None),
        (delivery_journey, "pending", lambda: False),
        (delivery_operation, "guard_protected_assets", lambda: None),
        (manual_storage_recovery, "resume", lambda loop: None),
        (meteor_banking, "pending", lambda: False),
        (storage_overflow, "pending", lambda: False),
        (merchant_loop_acceptance, "cycle_pending", lambda: False),
        (town_visit, "resume_verified_tail", lambda loop: None),
        (urgent_town_recovery, "resume_claimed", lambda loop: None),
        (restock_town_recovery, "resume_claimed", lambda loop: None),
        (restock_town_recovery, "resume_pre_admission_tail", lambda loop: None),
        (restock_cash_tail, "resume", lambda loop: None),
        (restock_restart, "resume", lambda loop: None),
        (city_travel, "ensure_city_visit", lambda loop: None),
        (scatter_training, "due", lambda loop: False),
    ]:
        monkeypatch.setattr(module, name, value)
    town = Town(bag(lucky=(200, 200, 15), equipped=(IRON, 600), potions=10), gear(IRON))
    loop = loop_with(town)
    loop.resume_settled_town_work = lambda: None
    loop.town_visit = NS(require_town_work_complete=lambda: None)
    loop.resume_on_route_map = lambda: None
    loop.prepare_supplies = lambda: None
    loop.select_level_route = lambda: False
    hunts = []

    def hunt():
        hunts.append(loop.route.supplies.arrow_type)
        if len(hunts) == 1:
            town.bag["equipped_ammo"]["amount"] = 2  # IronArrows spent
        else:
            # LuckyArrows spent too: now the town trip.
            town.bag["items"] = [i for i in town.bag["items"] if i["type_id"] != LUCKY]
        return None

    def restock():
        raise Done

    loop.hunt, loop.restock = hunt, restock
    with pytest.raises(Done):
        OvernightLoop._run_route(loop)
    assert hunts == [IRON, LUCKY]
