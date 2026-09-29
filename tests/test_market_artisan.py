"""Magic Artisan level upgrades in the Market (Alex 2026-09-29).

Alex asked for Toxic's and Suicide's Unique HornBows (500077) to be level
upgraded at the Market's Magic Artisan with Meteors, and "how many meteors
will it cost". Her price is the server's: the bot reads it from her dialog and
confirms only when the carried Meteors cover it.
"""

from types import SimpleNamespace as NS

import pytest

from conquest import market_artisan as m
from conquest import meteor_unpack as u

GREETING = [
    {"kind": 0, "option": 0, "text": "I can upgrade the level or quality of your equipment."},
    {"kind": 1, "option": 0, "text": "Upgrade the level."},
    {"kind": 1, "option": 1, "text": "Upgrade the quality."},
    {"kind": 1, "option": 2, "text": "Just passing by."},
]
ITEMS = [
    {"kind": 0, "option": 0, "text": "Which equipment do you want to upgrade?"},
    {"kind": 1, "option": 0, "text": "Headgear"},
    {"kind": 1, "option": 1, "text": "Weapon"},
    {"kind": 1, "option": 2, "text": "Boots"},
]
PRICE = [
    {"kind": 0, "option": 0, "text": "It will cost you 3 Meteors. Are you sure?"},
    {"kind": 1, "option": 0, "text": "Yes."},
    {"kind": 1, "option": 1, "text": "No, I will think it over."},
]


def test_market_ride_is_the_ape_conductress_market_option():
    plan = m.market_plan(1020)
    assert plan["destination_map"] == 1036 and plan["fare"] == 100
    assert plan["dialogs"][0]["option"] == "Market"
    assert plan["identity"]["model"] == 286 and plan["approach"] == [567, 615]
    assert m.market_plan(1002) is None


def test_market_exit_is_mark_controller_back_home():
    import json
    from pathlib import Path

    from conquest import meteor_banking

    saved = json.loads(Path("profiles/meteor-banking.json").read_text(encoding="utf-8"))
    m.write_json(meteor_banking.POLICY, saved)
    plan = m.exit_plan(1020)
    assert plan["npc"] == "Mark.Controller" and plan["source_map"] == 1036
    assert plan["destination_map"] == 1020 and plan["fare"] == 0


@pytest.mark.parametrize(
    "text, price",
    [
        ("It will cost you 3 Meteors. Are you sure?", 3),
        ("You need 1 Meteor for this upgrade.", 1),
        ("It requires 12 pieces of meteors.", 12),
        ("Meteors required: 4", 4),
        ("It needs 2 MeteorTears.", None),
        ("It costs 3 Meteors or 5 Meteors.", None),
        ("Are you sure?", None),
    ],
)
def test_her_meteor_price_is_read_from_her_words(text, price):
    assert m.meteor_price([{"kind": 0, "option": 0, "text": text}]) == price


def test_the_level_upgrade_and_the_weapon_are_picked_by_their_text():
    assert m.pick(GREETING, ("level",), ("quality",)) == "Upgrade the level."
    assert m.pick(ITEMS, m.SLOT_WORDS["bow"]) == "Weapon"
    assert m.pick(PRICE, m.CONFIRM_WORDS, m.DECLINE_WORDS) == "Yes."
    # Two matching options are never guessed between.
    both = GREETING + [{"kind": 1, "option": 3, "text": "Level up again"}]
    assert m.pick(both, ("level",), ("quality",)) is None


class Artisan:
    """Her dialog, the bag's Meteors and the worn bow, as the worker shows them."""

    def __init__(self, meteors):
        self.meteors = meteors
        self.bow = {"name": "HornBow", "type_id": 500077, "level": 45}
        self.dialog = None
        self.selected = []
        self.events = []

    def record(self, event, **fields):
        self.events.append((event, fields))

    def town(self, action, **kw):
        if action == "service-open":
            self.dialog = GREETING
            return {}
        if action == "service-dialog":
            if self.dialog is None:
                raise ValueError("NPC dialog is absent")
            return {"records": self.dialog}
        if action == "service-select":
            assert kw["records"] == self.dialog
            self.selected.append(kw["option"])
            if kw["option"] == "Upgrade the level.":
                self.dialog = ITEMS
            elif kw["option"] == "Weapon":
                self.dialog = PRICE
            elif kw["option"] == "Yes.":
                self.dialog = None
                self.meteors -= 3
                self.bow = {"name": "QinBow", "type_id": 500087, "level": 50}
            return {}
        if action == "service-close-panel":
            self.dialog = None
            return {"closed": True}
        if action == "supplies":
            return {"items": [{"uid": n, "type_id": m.METEOR, "amount": 1, "limit": 1} for n in range(self.meteors)]}
        if action == "gear":
            return {"level": 50, "equipment": {"bow": dict(self.bow)}}
        raise AssertionError(action)


def test_her_price_is_read_and_nothing_is_confirmed_short_of_meteors():
    loop = Artisan(meteors=0)
    outcome = m.talk(loop, "bow", confirm=True)
    assert outcome["price"] == 3 and outcome["confirmed"] is False
    assert outcome["stopped"] == "Meteors short"
    assert loop.selected == ["Upgrade the level.", "Weapon"]
    assert loop.dialog is None  # closed
    assert loop.events[-1][0] == "artisan_dialog"
    assert len(loop.events[-1][1]["steps"]) == 3


def test_a_price_only_visit_never_confirms():
    loop = Artisan(meteors=20)
    outcome = m.talk(loop, "bow", confirm=False)
    assert outcome["price"] == 3 and not outcome["confirmed"]
    assert "Yes." not in loop.selected and loop.meteors == 20


def test_covered_price_upgrades_the_bow_and_verifies_it():
    loop = Artisan(meteors=10)
    outcome = m.talk(loop, "bow", confirm=True)
    assert outcome["confirmed"] and outcome["after"]["name"] == "QinBow"
    assert loop.meteors == 7 and loop.selected[-1] == "Yes."


def test_an_unexpected_dialog_stops_before_any_choice():
    loop = Artisan(meteors=10)

    def other(action, **kw):
        if action == "service-dialog" and loop.dialog is not None:
            return {"records": [{"kind": 0, "option": 0, "text": "Come back later."}]}
        return Artisan.town(loop, action, **kw)

    loop.town = other
    loop.dialog = None
    outcome = m.talk(loop, "bow", confirm=True)
    assert outcome["stopped"] == "no single level-upgrade option" and loop.selected == []


def test_no_request_no_visit(monkeypatch):
    loop = NS(route=NS(restock_map_id=1020), town=lambda *a, **k: pytest.fail("No visit"))
    assert m.run(loop) is False
    m.write_json(m.REQUEST, {"enabled": True, "slot": "bow", "confirm": True})
    # A bow already at the character's tier cannot go up a level.
    loop = NS(
        route=NS(restock_map_id=1020),
        town=lambda action, **k: {"level": 50, "equipment": {"bow": {"type_id": 500087, "level": 50}}},
    )
    assert m.run(loop) is False


def test_unpacking_receipt_needs_the_scroll_gone_and_ten_meteors():
    from dataclasses import replace

    from conquest.memory_inventory import InventorySnapshot, Item

    scroll = Item(5, u.SCROLL, 1, 1, 3, 0)
    potion = Item(6, 1000030, 1, 1, 4, 0)
    before = InventorySnapshot(1, 1, (scroll, potion), None, 900, 40)
    meteors = tuple(Item(100 + n, u.METEOR, 1, 1, 5 + n, 0) for n in range(10))
    after = replace(before, items=(potion,) + meteors)
    assert u.receipt(before, scroll, after)
    assert not u.receipt(before, scroll, replace(after, items=(potion,) + meteors[:9]))
    assert not u.receipt(before, scroll, replace(after, silver=800))
    assert not u.receipt(before, scroll, replace(after, items=(scroll, potion) + meteors))
