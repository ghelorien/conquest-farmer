"""End-to-end: Twin City arrow purchases never scrolled the shop grid (live 2026-09-27).

Live 10:57 on Laptop1: Toxic's restock equipped a BambooBow, then its
LuckyArrow pack purchase failed with "Product is outside the qualified visible
shop row" and the route restarted. The Twin City Blacksmith lists about forty
products; LuckyArrow sits on row 8 of the five-column grid, below the visible
rows. Equipment purchases scroll the grid (buy-equipment); supply purchases
never did, so every restock that needed arrows failed. The worker's equipment
purchase also kept a flat 3,000 silver even where the leveling review had
reserved less, so a smaller wallet could never buy the reviewed upgrade.

Real code under test: TownTrade.execute ("buy" and "buy-equipment"),
ShopSnapshot.point and verified_read. Fakes only at boundaries: the live shop
grid (its scroll offset follows the mouse wheel), the inventory, the vendor,
read_equipment and the physical click.

Failure modes, written before the fix:
 S1 A supply listed below the visible rows is never bought.
 S2 Scrolling runs the wrong way or past the product, or the click lands on a
    stale cell.
 S3 A product that never becomes visible is clicked anyway or scrolled forever.
 S4 A leveling review's smaller reserve is overridden by the worker's flat
    3,000; without a reserve (America) the 3,000 still applies.
 S5 The artifact is not repeatable.
"""

import json
from types import SimpleNamespace as NS

import pytest

from conquest import town_trade
from conquest.memory_inventory import InventorySnapshot, Item
from conquest.memory_shop import GuiWindow, ShopProduct, ShopSnapshot
from conquest.town_trade import TownTrade

WINDOW = GuiWindow(1, "Shop", (100.0, 100.0), (288.0, 468.0), (0.0, 0.0))
ARROW = 1050000
BAMBOO = 500005


def products():
    rows = [
        ShopProduct(i, 0x1000 + i, 410005 + i * 10, f"Blade{i}", 102, 0, level=5)
        for i in range(38)
    ]
    rows.append(
        ShopProduct(38, 0x2000, BAMBOO, "BambooBow", 204, 0, level=8, profession=40,
                    attack_min=10, attack_max=13)
    )
    rows.append(ShopProduct(39, 0x2001, 500015, "HuntingBow", 724, 0, level=15))
    rows.append(
        ShopProduct(40, 0x3000, ARROW, "LuckyArrow", 200, 0, level=1, profession=40,
                    attack_min=10, attack_max=10)
    )
    return tuple(rows)


class Shop:
    """The live Blacksmith grid; each wheel tick scrolls 40 pixels."""

    def __init__(self, horizontal=0.0):
        self.scroll = 0.0
        self.horizontal = horizontal
        self.ticks = []

    def snapshot(self):
        grid = GuiWindow(
            2, "Shop/##Grid", (120.0, 138.0), (248.0, 400.0), (self.horizontal, self.scroll)
        )
        return ShopSnapshot(77, products(), WINDOW, grid)

    def read(self, entity_id):
        assert entity_id == 77
        return self.snapshot()

    def wheel(self, target, point, ticks, expected_size=None):
        self.ticks.append(ticks)
        self.scroll = min(max(0.0, self.scroll - 40 * ticks), 560.0)


class Bag:
    def __init__(self, silver):
        self.silver = silver
        self.items = [Item(10, ARROW, 150, 200, 0)]
        self.next_uid = 100

    def read(self):
        return InventorySnapshot(1.0, 1.0, tuple(self.items), None, self.silver, 40)


def trade(monkeypatch, shop, bag, clicks):
    monkeypatch.setattr(town_trade, "size_for", lambda observer: (1024, 768))
    monkeypatch.setattr("conquest.foreground.foreground_scroll", shop.wheel)
    t = TownTrade.__new__(TownTrade)
    t.observer = NS(operations=NS(target="client"))
    npc = NS(entity_id=77)
    t.vendor = lambda vendor_type: npc
    t.shop = shop
    t.inventory = bag
    t.life = lambda: NS(dead_candidate=False)
    t.input_attempted = False

    def click(point, button, before_press=None):
        # A buy right-click must be hover-checked over the Shop first.
        assert button != "right" or before_press is not None
        snap = shop.snapshot()
        cell = next(
            (p for p in snap.products if _visible(snap, p) and snap.point(p) == tuple(point)),
            None,
        )
        clicks.append([list(point), button, cell.name if cell else None, shop.scroll])
        if cell is not None and bag.silver >= cell.price:
            bag.silver -= cell.price
            bag.next_uid += 1
            bag.items.append(Item(bag.next_uid, cell.type_id, 200 if cell.type_id == ARROW else 1, 200, len(bag.items)))

    t.click = click
    return t


def _visible(snap, product):
    try:
        snap.point(product)
    except ValueError:
        return False
    return True


def outcome(monkeypatch, body, *, silver=5000, horizontal=0.0):
    shop, bag, clicks = Shop(horizontal), Bag(silver), []
    t = trade(monkeypatch, shop, bag, clicks)
    monkeypatch.setattr(
        "conquest.equipment.read_equipment",
        lambda observer: {
            "level": 11,
            "profession": 40,
            "map_id": 1002,
            "equipment": {
                "bow": dict(uid=11, type_id=500301, name="LuckyBow", level=1, profession=40,
                            sex=0, plus=0, gem1=0, gem2=0, attack_min=3, attack_max=5,
                            defense=0, dodge=0),
                "armor": dict(uid=12, type_id=132504, name="Coat", level=1, profession=0,
                              sex=0, plus=0, gem1=0, gem2=0, attack_min=0, attack_max=0,
                              defense=2, dodge=0),
            },
        },
    )
    try:
        result = t.execute(body)
        error = None
    except ValueError as e:
        result, error = None, str(e)
    return {
        "result": result,
        "error": error,
        "clicks": clicks,
        "wheel": shop.ticks,
        "silver": bag.silver,
    }


def scenario(monkeypatch):
    return {
        # S1/S2: LuckyArrow on row 8 of the Twin City Blacksmith.
        "arrow_below": outcome(
            monkeypatch, {"action": "buy", "vendor_type": 5, "type_id": ARROW}
        ),
        # S3: a horizontally scrolled grid never qualifies a cell.
        "never_visible": outcome(
            monkeypatch,
            {"action": "buy", "vendor_type": 5, "type_id": ARROW},
            horizontal=16.0,
        ),
        # S4: a leveling reserve of 600 with 1,000 carried buys the 204 bow.
        "leveling_reserve": outcome(
            monkeypatch,
            {"action": "buy-equipment", "vendor_type": 5, "type_id": BAMBOO, "reserve": 600},
            silver=1000,
        ),
        # S4: without a reserve (America) the flat 3,000 still applies.
        "flat_reserve": outcome(
            monkeypatch,
            {"action": "buy-equipment", "vendor_type": 5, "type_id": BAMBOO},
            silver=1000,
        ),
    }


def run(tmp_path, monkeypatch, name):
    with monkeypatch.context() as patch:
        artifact = scenario(patch)
    path = tmp_path / name / "shop-scroll-purchase.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(artifact, indent=2, sort_keys=True), encoding="utf-8")
    return path


def test_supply_and_equipment_purchases_scroll_to_the_product(tmp_path, monkeypatch):
    first = run(tmp_path, monkeypatch, "first")
    second = run(tmp_path, monkeypatch, "second")
    assert first.read_bytes() == second.read_bytes()  # S5
    a = json.loads(first.read_text(encoding="utf-8"))

    # S1/S2: scrolled down only, then one right-click on the LuckyArrow cell.
    arrow = a["arrow_below"]
    assert arrow["error"] is None and arrow["result"]["bought"] == ARROW
    assert arrow["wheel"] and all(t < 0 for t in arrow["wheel"])
    assert [c[2] for c in arrow["clicks"]] == ["LuckyArrow"]
    assert arrow["clicks"][0][1] == "right" and arrow["silver"] == 5000 - 200

    # S3: bounded scrolling, no click.
    never = a["never_visible"]
    assert never["clicks"] == [] and never["error"]
    assert len(never["wheel"]) <= 20

    # S4: the route's reserve is honoured; America keeps 3,000.
    assert a["leveling_reserve"]["result"]["bought"] == BAMBOO
    assert a["leveling_reserve"]["silver"] == 1000 - 204
    assert a["flat_reserve"]["clicks"] == []
    assert "budget" in a["flat_reserve"]["error"]
