"""A worthless 1-2 arrow remnant counts as sold once it leaves the bag.

Live 2026-09-26 (Back2Classic, LuckyArrow): the recycling sale of a 1-2 arrow
remnant took the remnant but paid 0 silver. The sale proof required a silver
increase, so every remnant sale was "not verified"; the route restarted
itself three times in an hour and stopped at 22:49 on the fourth.

Failure modes this module must catch (written before the implementation):

1. A 1-2 arrow remnant that the shop takes for 0 silver is reported as an
   unverified sale.
2. A remnant sale that did not happen (the remnant is still in the bag) is
   reported as sold.
3. A remnant sale during which silver went down is reported as sold.
4. A larger arrow bundle (3-25 arrows) or ordinary loot is accepted without a
   silver increase: their proof stays strict.
5. One sale issues more than one drag.

The end-to-end test runs the real town sale action against a fake game that
takes or keeps the item and pays some or no silver, and writes
``arrow-remnant-sale.json``; the scenario runs twice in separate roots and
must produce byte-identical artifacts.
"""

import json
from pathlib import Path
from types import SimpleNamespace

from conquest import town_trade
from conquest.memory_inventory import InventorySnapshot, Item
from conquest.town_trade import TownTrade

LUCKY = 1050000
MANA = 1001000  # A mana potion, identified junk: an ordinary loot sale.
CASES = {
    # name: (action, item type, amount, game takes item, silver change)
    "remnant_0_silver": ("sell_partial_arrow", LUCKY, 2, True, 0),
    "remnant_1_silver": ("sell_partial_arrow", LUCKY, 1, True, 1),
    "remnant_kept": ("sell_partial_arrow", LUCKY, 2, False, 0),
    "remnant_silver_lost": ("sell_partial_arrow", LUCKY, 2, True, -5),
    "bundle_0_silver": ("sell_partial_arrow", LUCKY, 20, True, 0),
    "bundle_6_silver": ("sell_partial_arrow", LUCKY, 20, True, 6),
    "loot_0_silver": ("sell", MANA, 1, True, 0),
    "loot_1_silver": ("sell", MANA, 1, True, 1),
}


class Game:
    """The farmer's bag at the shop; one drag applies the shop's response."""

    def __init__(self, type_id, amount, takes, silver_change):
        self.items = [
            Item(uid=7001, type_id=type_id, amount=amount, limit=200, slot=3),
            Item(uid=7002, type_id=LUCKY, amount=200, limit=200, slot=4),
            Item(uid=7003, type_id=LUCKY, amount=200, limit=200, slot=5),
            Item(uid=7004, type_id=LUCKY, amount=200, limit=200, slot=6),
        ]
        self.ammo = Item(uid=7000, type_id=LUCKY, amount=150, limit=200, slot=None)
        self.silver = 400
        self.takes, self.change = takes, silver_change
        self.drags = 0

    def read(self):
        return InventorySnapshot(
            started_at=1.0,
            timestamp=1.0,
            items=tuple(self.items),
            equipped_ammo=self.ammo,
            silver=self.silver,
            capacity=40,
        )

    def drag(self, target, source, destination, size):
        self.drags += 1
        if self.takes:
            self.items = [i for i in self.items if i.uid != 7001]
        self.silver += self.change


def _trade(game, monkeypatch):
    monkeypatch.setattr(town_trade, "foreground_drag", game.drag)
    monkeypatch.setattr(town_trade, "size_for", lambda observer: (1416, 876))
    trade = TownTrade.__new__(TownTrade)
    trade.observer = SimpleNamespace(operations=SimpleNamespace(target="farmer"))
    trade.input_attempted = False
    trade.vendor = lambda vendor_type: SimpleNamespace(entity_id=500 + vendor_type)
    trade.life = lambda *args, **kwargs: None
    trade.inventory = SimpleNamespace(read=game.read)
    window = SimpleNamespace(position=(100.0, 100.0), size=(300.0, 400.0))
    grid = SimpleNamespace(
        size=(407.0, 175.0), scroll=(0.0, 0.0), position=(600.0, 300.0)
    )
    trade.shop = SimpleNamespace(
        read=lambda entity_id: SimpleNamespace(window=window),
        gui=SimpleNamespace(read=lambda name: grid),
    )
    return trade


def _scenario(root, monkeypatch):
    root = Path(root)
    root.mkdir(parents=True)
    results = {}
    for name, (action, kind, amount, takes, change) in CASES.items():
        game = Game(kind, amount, takes, change)
        trade = _trade(game, monkeypatch)
        body = {"action": action, "vendor_type": 5 if kind == LUCKY else 3, "uid": 7001}
        try:
            outcome = {"sold": trade.execute(body)}
        except ValueError as error:
            outcome = {"refused": str(error)}
        results[name] = {**outcome, "drags": game.drags, "silver": game.silver}
    path = root / "arrow-remnant-sale.json"
    path.write_text(json.dumps(results, indent=2, sort_keys=True), encoding="utf-8")
    return path


def test_arrow_remnant_sale_e2e(tmp_path, monkeypatch):
    first = _scenario(tmp_path / "pc-a", monkeypatch)
    second = _scenario(tmp_path / "pc-b", monkeypatch)
    assert first.read_bytes() == second.read_bytes()
    results = json.loads(first.read_text(encoding="utf-8"))
    unverified = "Sale was not verified; no further sale issued"
    sold = {name for name, row in results.items() if "sold" in row}
    # Mode 1: a remnant taken for 0 silver is sold; so is one that pays.
    # Modes 2-4: kept remnants, lost silver, and strict bundles/loot are refused.
    assert sold == {
        "remnant_0_silver",
        "remnant_1_silver",
        "bundle_6_silver",
        "loot_1_silver",
    }
    for name in set(results) - sold:
        assert results[name]["refused"] == unverified, name
    assert results["remnant_0_silver"]["sold"] == {
        "sold": 7001,
        "type_id": LUCKY,
        "plus": None,
        "silver_gained": 0,
    }
    # Mode 5: exactly one drag per sale attempt.
    assert all(row["drags"] == 1 for row in results.values())
