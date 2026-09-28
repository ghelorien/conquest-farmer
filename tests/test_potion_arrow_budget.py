"""A restock short of arrows keeps silver for one arrow pack.

Failure modes (written before the change):
1. Potions take every coin and the arrow pack is unaffordable, so the farmer
   is stranded in town (live 2026-09-27: 766 silver, 12 Painkillers bought,
   46 left, LuckyArrow 200).
2. The cap fires before a safe hunt's potions are carried.
3. The cap fires when arrows are not short.
4. An unknown arrow or potion price invents a reserve.
5. The arrow price comes from anything but a verified purchase of that type.
6. "Short" means only under the return threshold (3 arrows), so a farmer
   with less than one pack is left unable to buy it (live 2026-09-27 15:20:
   53 arrows, 385 silver, 15 cheap potions bought, 193 left for a 200 pack).
7. Potions stop short of the plan for an IronArrow pack (live 2026-09-28
   00:26: 380 IronArrows, 4,262 silver; potions capped at 5 of 23 to keep
   4,800, then the optional pack was deferred for its 3,000 floor), or an
   empty IronArrow quiver is stranded with no silver for its LuckyArrow
   fallback.
8. A top-up counts as optional while the carried arrows last only minutes
   (live 2026-09-28 00:25, Toxic: 184 IronArrows, about four minutes, pack
   deferred and 6,082 banked).
"""

import json
from types import SimpleNamespace as NS

from conquest.overnight import (
    SAFE_HUNT_POTIONS,
    arrow_reserve,
    last_verified_price,
    potion_budget_reached,
)

ROUTE = NS(
    supplies=NS(arrows_return_below=3, healing_return_below=1, arrow_type=1050000)
)


def counts(arrows=1, potions=8, silver=766):
    return {"arrows": arrows, "potions": potions, "free_slots": 20, "silver": silver}


def test_live_case_stops_before_the_arrow_money_is_spent():
    silver, potions, bought = 766, 8, 0
    while not potion_budget_reached(counts(potions=potions, silver=silver), ROUTE, 60, 200):
        silver, potions, bought = silver - 60, potions + 1, bought + 1
    assert silver >= 200 and bought == 9


def test_no_cap_before_a_safe_hunt_of_potions():
    assert not potion_budget_reached(
        counts(potions=SAFE_HUNT_POTIONS - 1, silver=210), ROUTE, 60, 200
    )
    assert potion_budget_reached(
        counts(potions=SAFE_HUNT_POTIONS, silver=210), ROUTE, 60, 200
    )


def test_no_cap_when_arrows_are_not_short():
    assert not potion_budget_reached(counts(arrows=500, silver=210), ROUTE, 60, 200)
    # One full LuckyArrow pack (200) carried is not short.
    assert not potion_budget_reached(counts(arrows=200, silver=210), ROUTE, 60, 200)


def test_less_than_a_pack_keeps_the_pack_price():
    # 6: the live 15:20 restock, Resolutives at 18.
    silver, potions, bought = 385, 5, 0
    while not potion_budget_reached(
        counts(arrows=53, potions=potions, silver=silver), ROUTE, 18, 200
    ):
        silver, potions, bought = silver - 18, potions + 1, bought + 1
    assert silver >= 200 and potions >= SAFE_HUNT_POTIONS and bought == 10


def test_an_empty_quiver_keeps_its_pack_after_five_potions():
    # Live 2026-09-27 18:18 (Toxic): 2 arrows, no potions, Resolutives at 18;
    # nine were bought and the 200-silver pack no longer fit (143 left).
    silver, potions, bought = 305, 0, 0
    while not potion_budget_reached(
        counts(arrows=2, potions=potions, silver=silver), ROUTE, 18, 200
    ):
        silver, potions, bought = silver - 18, potions + 1, bought + 1
    assert SAFE_HUNT_POTIONS == 5
    assert bought == 5 and silver >= 200


def test_the_arrow_reserve_is_one_pack_price_while_short_of_a_pack():
    # Shared by the potion cap and the first return scroll.
    assert arrow_reserve(counts(arrows=53), ROUTE, 200) == 200
    assert arrow_reserve(counts(arrows=200), ROUTE, 200) == 0
    assert arrow_reserve(counts(arrows=53), ROUTE, None) == 0


IRON = NS(
    supplies=NS(arrows_return_below=3, healing_return_below=1, arrow_type=1050001)
)


def test_potions_do_not_wait_for_an_iron_top_up_that_will_not_be_bought():
    # 7: the live 00:26 restock, Painkillers at 60, IronArrows 4,800 a pack.
    silver, potions = 4262, 3
    while potions < 23 and not potion_budget_reached(
        counts(arrows=380, potions=potions, silver=silver), IRON, 60, 4800
    ):
        silver, potions = silver - 60, potions + 1
    assert potions == 23 and silver == 4262 - 20 * 60
    assert arrow_reserve(counts(arrows=380, silver=4262), IRON, 4800) == 0


def test_iron_packs_come_after_the_planned_potions():
    # 7: supply_plan already splits the budget; an optional top-up keeps
    # nothing back, a required refill one LuckyArrow pack (its fallback).
    assert arrow_reserve(counts(arrows=380, silver=9000), IRON, 4800) == 0
    assert arrow_reserve(counts(arrows=2, silver=6000), IRON, 4800) == 200
    # Still never stranded: an empty IronArrow quiver with 305 silver and
    # Resolutives at 18 keeps a LuckyArrow pack after five potions.
    silver, potions = 305, 0
    while not potion_budget_reached(
        counts(arrows=2, potions=potions, silver=silver), IRON, 18, 4800
    ):
        silver, potions = silver - 18, potions + 1
    assert potions == 5 and silver >= 200


def _learned(route_id, arrows_per_min):
    from conquest import supply_plan
    from conquest.discord_notify import write_json

    write_json(
        supply_plan.RATES,
        {"routes": {route_id: {"potions_per_min": 0.3, "arrows_per_min": arrows_per_min}}},
    )


def test_minutes_of_arrows_make_a_top_up_required():
    # 8: Toxic 00:25, 184 IronArrows at ~46 a minute: four minutes of shooting.
    from conquest.overnight import MIN_TOPUP_MINUTES, optional_top_up

    route = NS(id="bandit", supplies=IRON.supplies)
    assert optional_top_up(counts(arrows=184), route)  # unmeasured: optional
    _learned("bandit", 46)
    assert MIN_TOPUP_MINUTES == 10
    assert not optional_top_up(counts(arrows=184), route)
    assert optional_top_up(counts(arrows=460), route)
    # Required: potions keep one LuckyArrow pack back, the fallback tier.
    assert arrow_reserve(counts(arrows=184, silver=6282), route, 4800) == 200
    # LuckyArrows never had a floor.
    assert not optional_top_up(counts(arrows=184), NS(id="bandit", supplies=ROUTE.supplies))


def test_a_required_iron_top_up_is_bought_without_the_floor():
    # 8: the refill buys the pack Toxic left without at 00:25.
    from conquest.overnight import OvernightLoop

    _learned("bandit", 46)
    loop = OvernightLoop.__new__(OvernightLoop)
    from conquest.routes import RouteLibrary

    loop.route = RouteLibrary().load("bandit")
    assert loop.route.supplies.arrow_type == 1050001
    bag = {
        "silver": 6282,
        "capacity": 40,
        "items": [{"uid": 1, "type_id": 1000020, "amount": 1, "slot": 0}],
        "equipped_ammo": {"uid": 9, "type_id": 1050001, "amount": 184, "limit": 1000},
    }
    bought, events = [], []

    def town(action, **fields):
        if action == "supplies":
            return json.loads(json.dumps(bag))
        if action == "shop":
            return {"products": [{"type_id": 1050001, "price": 4800}]}
        if action == "buy":
            bought.append(fields["type_id"])
            bag["silver"] -= 4800
            bag["items"].append({"uid": 2, "type_id": 1050001, "amount": 1000, "slot": 1})
            return {"bought": 1050001, "amount": 1000, "price": 4800, "silver": bag["silver"]}
        raise AssertionError(action)

    loop.town, loop.record = town, lambda event, **fields: events.append(event)
    assert loop.buy_supply(5, 1050001) is True
    assert bought == [1050001] and "optional_purchase_deferred" not in events


def test_unknown_prices_keep_the_old_behaviour():
    assert not potion_budget_reached(counts(silver=210), ROUTE, None, 200)
    assert not potion_budget_reached(counts(silver=210), ROUTE, 60, None)


def test_arrow_price_is_the_newest_verified_purchase_of_that_type(tmp_path):
    log = tmp_path / "events.jsonl"
    rows = [
        {"event": "purchase", "receipt": {"bought": 1050000, "price": 180}},
        {"event": "purchase", "receipt": {"bought": 1000020, "price": 60}},
        {"event": "purchase", "receipt": {"bought": 1050000, "price": 200}},
        {"event": "travel", "receipt": {"bought": 1050000, "price": 999}},
        {"event": "purchase", "receipt": {"bought": 1000020, "price": 60}},
    ]
    log.write_text("\n".join(json.dumps(r) for r in rows) + "\nnot json\n")
    assert last_verified_price(1050000, log) == 200
    assert last_verified_price(1050001, log) is None
    assert last_verified_price(1050000, tmp_path / "missing.jsonl") is None
