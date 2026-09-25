"""Isolated tier-selection and funding rules behind the arrow tier fallback.

The end-to-end restock scenarios are in test_arrow_tier_fallback_e2e.py. These
cover the pure rules the E2E cannot reach cheaply. Failure modes, written first:
 T1  With nothing usable, current_arrow returns a saved default rather than the
     best tier eligible for the level (Lucky 1, Iron 32, Speed 73).
 T2  A usable (>= 3, level-eligible) carried stack loses to the level-best tier.
 T3  Savings mode: with nothing usable, adopt_ammunition leaves the
     savings-configured tier for a SpeedArrow that savings will not buy.
 T4  The restock budget funds an Iron spare or a Speed upgrade while a usable
     lower-tier pack is carried (level 95, Iron pack equipped).
 T5  The restock budget for the selected tier is not priced from the recorded
     live-shop quote of that tier.
"""

import pytest

from conquest.arrow_upgrades import current_arrow

LUCKY, IRON, SPEED = 1050000, 1050001, 1050002


def gear(level, kind=None, uid=7):
    arrows = {}
    if kind:
        arrows = {
            "arrows": {
                "uid": uid,
                "type_id": kind,
                "level": {LUCKY: 1, IRON: 32, SPEED: 73}[kind],
                "profession": 40,
                "attack_min": 10,
                "attack_max": 10,
            }
        }
    return {"level": level, "profession": 41, "equipment": arrows}


@pytest.mark.parametrize(
    "level,expected",
    [
        (1, LUCKY),
        (20, LUCKY),
        (31, LUCKY),
        (32, IRON),
        (40, IRON),
        (72, IRON),
        (73, SPEED),
        (95, SPEED),
    ],
)
def test_nothing_usable_selects_level_best_tier(level, expected):
    remnant = {"uid": 7, "type_id": SPEED if level >= 73 else LUCKY, "amount": 1}
    state = gear(level, remnant["type_id"])
    assert current_arrow(state, equipped_ammo=remnant) == expected  # T1
    assert current_arrow(state, None, [], equipped_ammo=remnant) == expected


def test_usable_lower_tier_is_used_before_the_level_best_tier():
    iron = {"uid": 8, "type_id": IRON, "amount": 1000}
    assert current_arrow(gear(95, IRON, uid=8), equipped_ammo=iron) == IRON  # T2
    assert current_arrow(gear(95, SPEED), reserves=[IRON]) == IRON
    # An explicit default (savings mode) still applies when nothing is usable.
    assert current_arrow(gear(95, SPEED), LUCKY) == LUCKY


def savings_loop(monkeypatch, arrow_type):
    from conquest import savings
    from conquest.overnight import OvernightLoop
    from conquest.routes import RouteLibrary

    monkeypatch.setattr(savings, "savings_plan", lambda: {"mode": "save_silver"})
    loop = OvernightLoop.__new__(OvernightLoop)
    route = RouteLibrary().load("bandit")
    loop.route = route.model_copy(
        update={
            "supplies": route.supplies.model_copy(
                update={"arrow_type": arrow_type, "arrows_restock_to": 2000}
            )
        }
    )
    events = []
    loop.record = lambda event, **fields: events.append((event, fields))
    bag = {
        "items": [],
        "equipped_ammo": {"uid": 7, "type_id": SPEED, "amount": 1},
        "silver": 500,
        "capacity": 40,
    }
    loop.town = lambda action, **fields: bag
    return loop, events


def test_savings_mode_keeps_its_configured_tier_when_nothing_is_usable(monkeypatch):
    loop, events = savings_loop(monkeypatch, LUCKY)
    loop.adopt_ammunition(gear(95, SPEED))  # T3
    assert loop.route.supplies.arrow_type == LUCKY
    assert all(fields["arrow_type"] == LUCKY for _, fields in events)


def budget_route(kind, restock_to):
    from conquest.routes import RouteLibrary

    route = RouteLibrary().load("bandit")
    return route.model_copy(
        update={
            "supplies": route.supplies.model_copy(
                update={
                    "arrow_type": kind,
                    "arrows_restock_to": restock_to,
                    "healing_restock_to": 5,
                }
            )
        }
    )


def potions():
    return [
        {"uid": 20 + i, "type_id": 1000020, "amount": 1, "limit": 1} for i in range(5)
    ]


def test_usable_lower_tier_pack_funds_no_arrows(monkeypatch):
    from conquest import banking as b

    monkeypatch.setattr(b, "transport_reserve", lambda: 200)
    bag = {
        "items": potions(),
        "equipped_ammo": {"uid": 8, "type_id": IRON, "amount": 1000, "limit": 1000},
        "capacity": 40,
        "silver": 200,
    }
    route = budget_route(IRON, 2000)
    assert b.shopping_budget(route, bag, level=95) == 200  # T4
    # Iron is the level-best tier at 72: its one-pack spare is still funded.
    assert b.shopping_budget(route, bag, level=72) == 200 + 4800 + 3000


def test_selected_speed_tier_is_funded_from_its_recorded_shop_price(monkeypatch):
    from conquest import banking as b

    monkeypatch.setattr(b, "transport_reserve", lambda: 200)
    bag = {
        "items": potions(),
        "equipped_ammo": {"uid": 7, "type_id": SPEED, "amount": 1, "limit": 5000},
        "capacity": 40,
        "silver": 200,
    }
    # The recorded Phoenix Blacksmith quote is 34,000 per 5,000-arrow pack.
    route = budget_route(SPEED, 10000)
    assert b.shopping_budget(route, bag, level=95) == 200 + 2 * 34000 + 3000  # T5
    monkeypatch.setattr("conquest.archer_shop_catalog.catalog", lambda: {"cities": {}})
    with pytest.raises(ValueError, match="not qualified"):
        b.shopping_budget(route, bag, level=95)
