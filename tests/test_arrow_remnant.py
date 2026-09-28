"""A scrap of arrows never keeps a needed pack out of the bag.

Live 2026-09-28 03:59 (Toxic, level 37): the equipped IronArrow stack (140)
and a 3-arrow stack filled the two-pack limit; the planned pack was refused
and it left town with 143 arrows and 10,664 silver banked.
"""

from types import SimpleNamespace

import pytest

from conquest import overnight
from conquest.overnight import OvernightLoop, blocking_remnant

IRON, LUCKY = 1050001, 1050000


def bag(stacks, equipped, silver=10864):
    return {
        "items": [
            {"uid": 10 + n, "type_id": kind, "amount": amount, "slot": n}
            for n, (kind, amount) in enumerate(stacks)
        ],
        "equipped_ammo": {"uid": 1, "type_id": equipped[0], "amount": equipped[1]},
        "silver": silver,
        "capacity": 40,
    }


@pytest.fixture(autouse=True)
def leveling(monkeypatch):
    from conquest import equipment

    monkeypatch.setattr(equipment, "leveling_archer", lambda: True)


def test_toxics_scrap_is_the_remnant_to_recycle():
    remnant = blocking_remnant(bag([(IRON, 3)], (IRON, 140)), IRON)
    assert remnant["amount"] == 3


@pytest.mark.parametrize(
    "stacks,equipped",
    [
        ([(IRON, 3)], (IRON, 1000)),  # a full pack is carried: keep the limit
        ([(IRON, 300)], (IRON, 140)),  # 300 arrows are no scrap
        ([(IRON, 10)], (IRON, 140)),  # a partial pack keeps its slot (policy)
        ([], (IRON, 140)),  # nothing in the bag to sell
    ],
)
def test_anything_else_keeps_the_pack_limit(stacks, equipped):
    assert blocking_remnant(bag(stacks, equipped), IRON) is None


def test_buy_supply_recycles_the_scrap_then_buys_the_pack(monkeypatch):
    from conquest import savings

    route = SimpleNamespace(
        id="wingedsnake",
        supplies=SimpleNamespace(
            arrow_type=IRON, healing_type=1000020, arrows_return_below=3
        ),
    )
    loop = OvernightLoop.__new__(OvernightLoop)
    loop.route = route
    events, calls = [], []
    loop.record = lambda event, **fields: events.append(event)
    state = {"bag": bag([(IRON, 3)], (IRON, 140))}

    def town(action, **fields):
        calls.append(action)
        if action == "supplies":
            return state["bag"]
        if action == "sell_partial_arrow":
            assert fields["uid"] == 10
            state["bag"] = bag([], (IRON, 140))
            return {"sold": 10, "type_id": IRON, "silver_gained": 3}
        if action == "buy":
            return {"bought": IRON, "amount": 1000, "price": 4800}
        raise AssertionError(action)

    loop.town = town
    monkeypatch.setattr(overnight, "optional_top_up", lambda counts, route: False)
    monkeypatch.setattr(savings, "savings_plan", lambda: None)
    assert loop.buy_supply(5, IRON) is True
    assert calls == ["supplies", "sell_partial_arrow", "supplies", "buy"]
    assert "arrow_purchase_deferred" not in events
    assert events[-1] == "purchase"
