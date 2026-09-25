"""Isolated tier-selection and funding rules behind the arrow tier fallback.

The end-to-end restock scenarios are in test_arrow_tier_fallback_e2e.py. These
cover the pure rules the E2E cannot reach cheaply. Failure modes, written first:
 T1  With nothing usable, current_arrow returns a saved default rather than the
     best tier eligible for the level (Lucky 1, Iron 32, Speed 73).
 T2  A usable (>= 3, level-eligible) carried stack loses to the level-best tier.
 T3  Savings mode: with nothing usable, adopt_ammunition leaves the
     savings-configured tier for a SpeedArrow that savings will not buy.
 T4  The restock budget for the selected tier is not priced from the recorded
     live-shop quote of that tier.
 T5  Affordable fallback: when the selected tier cannot be paid for, the
     fallback is not the next lower level-eligible tier the wallet covers
     (Speed -> Iron -> Lucky), picks a tier above the selection or the level,
     uses a price the live shop did not quote, or invents a tier when nothing
     is affordable.
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
    assert b.shopping_budget(route, bag, level=95) == 200 + 2 * 34000 + 3000  # T4
    monkeypatch.setattr("conquest.archer_shop_catalog.catalog", lambda: {"cities": {}})
    with pytest.raises(ValueError, match="not qualified"):
        b.shopping_budget(route, bag, level=95)


def shop(*kinds):
    quotes = {
        LUCKY: {"price": 200, "level": 1},
        IRON: {"price": 4800, "level": 32},
        SPEED: {"price": 34000, "level": 73},
    }
    return [{"type_id": k, "profession": 40, **quotes[k]} for k in kinds]


@pytest.mark.parametrize(
    "level,kind,silver,expected",
    [
        (95, SPEED, 11200, IRON),  # Speed unaffordable: Iron.
        (95, SPEED, 4799, LUCKY),  # Iron unaffordable too: Lucky.
        (95, SPEED, 199, None),  # Nothing affordable: no invented tier.
        (40, IRON, 3200, LUCKY),
        (95, IRON, 100000, LUCKY),  # Never above the selected tier.
        (20, LUCKY, 100000, None),  # Nothing below Lucky.
    ],
)
def test_affordable_fallback_is_next_lower_eligible_tier(level, kind, silver, expected):
    from conquest.arrow_upgrades import fallback_arrow

    product = fallback_arrow(shop(LUCKY, IRON, SPEED), level, kind, silver)  # T5
    assert (product and product["type_id"]) == (expected or None)


def test_affordable_fallback_uses_only_live_shop_quotes():
    from conquest.arrow_upgrades import fallback_arrow

    # Iron is not listed by this Blacksmith: no guessed Iron price.
    assert fallback_arrow(shop(LUCKY, SPEED), 95, SPEED, 11200)["type_id"] == LUCKY
    assert fallback_arrow(shop(SPEED), 95, SPEED, 11200) is None
