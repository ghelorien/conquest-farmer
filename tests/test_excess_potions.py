"""Spare potions are sold back at the Pharmacist down to the supply plan.

2026-09-30 09:26 (Suicide, thunderape-nw): rates learned from heavy-damage
hunts planned 33 potions; the buffed hunt then used none, and the 32 left held
the bag room of the arrow packs and ApeCityGates (2,141 arrows, ~15 minutes).
"""

from types import SimpleNamespace as NS

from conquest.overnight import EXCESS_POTION_SLACK, OvernightLoop

AMRITA = 1000030
TWIN_GATE = 1060020


class Bag:
    def __init__(self, potions, target):
        self.items = [{"uid": i, "type_id": AMRITA, "amount": 1} for i in range(potions)]
        self.items.append({"uid": 900, "type_id": TWIN_GATE, "amount": 1})
        self.route = NS(supplies=NS(healing_type=AMRITA, healing_restock_to=target))
        self.events, self.sold = [], []

    def town(self, action, **kw):
        if action == "supplies":
            return {"items": list(self.items), "silver": 0, "capacity": 40}
        assert action == "sell-potion" and kw["vendor_type"] == 3
        item = next(i for i in self.items if i["uid"] == kw["uid"])
        self.items.remove(item)
        self.sold.append(item["type_id"])
        return {"sold": item["type_id"], "silver_gained": 40}

    def record(self, event, **fields):
        self.events.append((event, fields))


def test_spare_potions_are_sold_down_to_the_plan():
    bag = Bag(32, 18)
    assert OvernightLoop.sell_excess_potions(bag) == 32 - 18 - EXCESS_POTION_SLACK
    assert set(bag.sold) == {AMRITA}  # the gate stays
    assert sum(i["type_id"] == AMRITA for i in bag.items) == 18 + EXCESS_POTION_SLACK
    assert bag.events[-1][0] == "excess_potions_sold"


def test_nothing_is_sold_within_the_slack():
    bag = Bag(18 + EXCESS_POTION_SLACK, 18)
    assert OvernightLoop.sell_excess_potions(bag) == 0
    assert not bag.events


def test_a_refused_sale_keeps_the_rest():
    bag = Bag(32, 18)
    real = bag.town

    def refuse(action, **kw):
        if action == "sell-potion":
            raise ValueError("Sale was not verified")
        return real(action, **kw)

    bag.town = refuse
    assert OvernightLoop.sell_excess_potions(bag) == 0
    assert [e for e, _ in bag.events] == ["excess_potion_sale_refused"]
    assert len(bag.items) == 33
