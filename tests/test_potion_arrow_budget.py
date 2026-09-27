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
