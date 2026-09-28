"""A nearly empty equipped quiver does not hold one of the two pack slots.

Live 2026-09-28 10:09 (Suicide, level 38, IronArrows): the route hunts until
ammo_unavailable, so it walked home with 2 arrows equipped. The supply plan,
which ignores stacks under 3 arrows, planned 2 packs; the purchase cap counted
the 2-arrow quiver as the equipped pack and refused the second, so Suicide
left town with 1,002 arrows (~23 minutes at 44 a minute) for a trip planned
at 49 minutes, and banked the 8,199 silver meant for it. Unlike a bag remnant
(blocking_remnant), an equipped quiver cannot be sold to free the slot.

Failure modes, written before the change:
1. An equipped quiver of REMNANT_ARROWS or fewer counts as a pack and refuses
   the planned second pack.
2. A real partial equipped pack (more than REMNANT_ARROWS) stops counting, so
   a third pack is bought.
3. buy_supply refuses the second pack in Suicide's live bag.
"""

from types import SimpleNamespace

import pytest

from conquest import overnight
from conquest.arrow_upgrades import (
    REMNANT_ARROWS,
    arrow_pack_count,
    require_arrow_purchase_room,
)
from conquest.overnight import OvernightLoop

IRON = 1050001


def bag(stacks, equipped):
    return {
        "items": [
            {"uid": 10 + n, "type_id": IRON, "amount": amount, "slot": n}
            for n, amount in enumerate(stacks)
        ],
        "equipped_ammo": {"uid": 1, "type_id": IRON, "amount": equipped},
        "silver": 8399,
        "capacity": 40,
    }


@pytest.fixture(autouse=True)
def leveling(monkeypatch):
    from conquest import equipment

    monkeypatch.setattr(equipment, "leveling_archer", lambda: True)


@pytest.mark.parametrize("equipped", [1, 2, REMNANT_ARROWS])
def test_an_equipped_remnant_is_not_a_pack(equipped):
    # 1
    live = bag([1000], equipped)
    assert arrow_pack_count(live, IRON) == 1
    require_arrow_purchase_room(live, IRON)


@pytest.mark.parametrize("equipped", [REMNANT_ARROWS + 1, 140, 1000])
def test_a_partial_equipped_pack_still_counts(equipped):
    # 2
    live = bag([1000], equipped)
    assert arrow_pack_count(live, IRON) == 2
    with pytest.raises(ValueError, match="maximum packs"):
        require_arrow_purchase_room(live, IRON)


def test_suicides_second_pack_is_bought(monkeypatch):
    # 3
    from conquest import savings

    route = SimpleNamespace(
        id="bandit-southeast",
        supplies=SimpleNamespace(
            arrow_type=IRON, healing_type=1000020, arrows_return_below=3
        ),
    )
    loop = OvernightLoop.__new__(OvernightLoop)
    loop.route = route
    events, calls = [], []
    loop.record = lambda event, **fields: events.append(event)

    def town(action, **fields):
        calls.append(action)
        if action == "supplies":
            return bag([1000], 2)
        if action == "buy":
            return {"bought": IRON, "amount": 1000, "price": 4800}
        raise AssertionError(action)

    loop.town = town
    monkeypatch.setattr(overnight, "optional_top_up", lambda counts, route: False)
    monkeypatch.setattr(savings, "savings_plan", lambda: None)
    assert loop.buy_supply(5, IRON) is True
    assert calls == ["supplies", "buy"]
    assert "arrow_purchase_deferred" not in events
