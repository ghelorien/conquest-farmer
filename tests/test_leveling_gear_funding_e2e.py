"""End-to-end: a leveling Back2Classic archer never bought a better bow (live 2026-09-27).

Live 10:27 on Laptop1: Toxic reached level 11 still wearing the level-1
LuckyBow (attack 3-5), although the Twin City Blacksmith sells a BambooBow
(level 8, 204 silver, attack 10-13). The restock withdrew only supply money,
the equipment review refused every item with "Keeping silver for supplies" (a
flat 3,000-silver reserve), and the same visit then banked 2,934 silver
(6,907 stored). The starter Coat 132504 also carries a dye colour (hundreds
digit 5) that no Twin City armorer sells, so every armor upgrade was refused
with "Armor form does not match".

Real code under test: banking.fund_restock and shopping_budget,
equipment.EquipmentReview.visit, choose_upgrades, review_slots and
upgrade_reason, arrow_upgrades.review_arrows and level_goal. Fakes only at the
town bridge (gear, bag, shop, warehouse, buy and equip receipts).

Failure modes, written before the fix:
 G1 A leveling archer with its silver banked never funds or buys an
    affordable, level-eligible bow at a required restock.
 G2 A starter armor in an unsold dye colour blocks every armor upgrade.
 G3 The leveling reserve must still keep what the rest of the visit buys
    (missing arrow packs and fares): a bow that would eat into it is refused.
 G4 America farmers keep the flat 3,000 reserve, the colour rule and
    supply-only withdrawals.
 G5 The artifact is not repeatable.

The scenario writes <tmp>/<run>/leveling-gear-funding.json, re-reads it for
the assertions, and runs twice to prove the artifact is byte-identical.
"""

import copy
import json
from types import SimpleNamespace as NS

from conquest import banking, equipment, level_goal
from conquest.routes import RouteLibrary

LUCKY_BOW = dict(
    uid=11, type_id=500301, name="LuckyBow", level=1, profession=40, sex=0,
    plus=0, gem1=0, gem2=0, attack_min=3, attack_max=5, defense=0, dodge=0,
)
COAT = dict(
    uid=12, type_id=132504, name="Coat", level=1, profession=0, sex=0,
    plus=0, gem1=0, gem2=0, attack_min=0, attack_max=0, defense=2, dodge=0,
)
ARROWS = dict(
    uid=13, type_id=1050000, name="LuckyArrow", level=1, profession=40, sex=0,
    plus=0, gem1=0, gem2=0, attack_min=10, attack_max=10, defense=0, dodge=0,
)


def product(type_id, name, level, price, attack=(0, 0), defense=0):
    return dict(
        type_id=type_id, name=name, level=level, price=price, profession=0,
        sex=0, attack_min=attack[0], attack_max=attack[1], defense=defense,
        dodge=0,
    )


# Twin City shop stock as recorded live (Toxic's archer-shop-catalog.json).
SHOPS = {
    5: [
        product(500005, "BambooBow", 8, 204, (10, 13)),
        product(500015, "HuntingBow", 15, 724, (54, 67)),
        product(500301, "LuckyBow", 1, 0, (3, 5)),
        product(1050000, "LuckyArrow", 1, 200, (10, 10)),
    ],
    4: [
        product(132305, "Coat", 1, 10, defense=2),
        product(132315, "Dress", 12, 258, defense=13),
        product(132415, "Dress", 12, 258, defense=13),
        product(133305, "DeerskinCoat", 15, 780, defense=15),
    ],
}


class Town:
    """The embedded worker's town surface for one character visit."""

    def __init__(self, level, carried, stored, spare_packs):
        self.level = level
        self.silver = carried
        self.stored = stored
        self.equipment = {"bow": dict(LUCKY_BOW), "armor": dict(COAT), "arrows": dict(ARROWS)}
        self.items = [
            {"uid": 100 + n, "type_id": 1050000, "amount": 200, "limit": 200}
            for n in range(spare_packs)
        ] + [{"uid": 90, "type_id": 1000010, "amount": 20, "limit": 1}]
        self.calls = []
        self.next_uid = 500

    def bag(self):
        return {
            "silver": self.silver,
            "items": copy.deepcopy(self.items),
            "capacity": 40,
            "equipped_ammo": {"type_id": 1050000, "amount": 150, "limit": 200},
        }

    def __call__(self, action, **fields):
        self.calls.append([action, fields.get("vendor_type"), fields.get("type_id")])
        if action == "gear":
            return {
                "level": self.level,
                "profession": 40,
                "map_id": 1002,
                "equipment": copy.deepcopy(self.equipment),
            }
        if action == "supplies":
            return self.bag()
        if action == "shop":
            return {"products": copy.deepcopy(SHOPS[fields["vendor_type"]])}
        if action == "buy-equipment":
            item = next(p for p in SHOPS[fields["vendor_type"]] if p["type_id"] == fields["type_id"])
            assert self.silver >= item["price"], "worker refuses an unaffordable buy"
            self.silver -= item["price"]
            self.next_uid += 1
            self.items.append({"uid": self.next_uid, "type_id": item["type_id"], "amount": 1, "limit": 1, "item": item})
            return {"uid": self.next_uid, "price": item["price"]}
        if action == "equip":
            new = next(i for i in self.items if i["uid"] == fields["uid"])
            self.items.remove(new)
            slot = equipment.category(new["type_id"])
            self.equipment[slot] = {
                **{k: v for k, v in new["item"].items() if k != "price"},
                "uid": new["uid"], "plus": 0, "gem1": 0, "gem2": 0,
            }
            return {"equipped": new["uid"], "slot": slot}
        if action in ("close", "open"):
            return {}
        raise AssertionError(action)


def visit(tmp_path, monkeypatch, name, *, leveling, level, carried, stored, spare_packs, vendors):
    root = tmp_path / name
    root.mkdir(parents=True)
    monkeypatch.setattr(equipment, "state_path", lambda value: str(root / value))
    from conquest import archer_shop_catalog

    monkeypatch.setattr(archer_shop_catalog, "RUNTIME", root / "archer-shop-catalog.json")
    monkeypatch.setattr(
        banking,
        "policy",
        lambda: {"enabled": True, "transport_reserve": 200, "withdraw_essentials": True},
    )
    if leveling:
        level_goal.start(23)
    else:
        level_goal.stop()
    banking.write_json(banking.STATUS, {"stored_silver": stored})
    town = Town(level, carried, stored, spare_packs)
    transfers = []

    def open_warehouse(loop):
        return {"silver": town.silver, "stored_silver": town.stored}

    def transfer(loop, direction, amount):
        assert direction == "withdraw" and 0 < amount <= town.stored
        town.stored -= amount
        town.silver += amount
        transfers.append(amount)

    monkeypatch.setattr(banking, "open_warehouse", open_warehouse)
    monkeypatch.setattr(banking, "transfer", transfer)
    monkeypatch.setattr(banking, "close_warehouse", lambda loop: None)
    events = []
    loop = NS(
        route=RouteLibrary().load("pheasant"),
        town=town,
        record=lambda event, **fields: events.append([event, fields.get("upgrades"), fields.get("slots")]),
    )
    banking.fund_restock(loop)
    for vendor in vendors:
        equipment.EquipmentReview(loop).visit(vendor)
    reviews = [e for e in events if e[0] == "equipment_review"]
    return {
        "withdrawn": transfers,
        "bow": town.equipment["bow"]["name"],
        "armor": town.equipment["armor"]["name"],
        "silver_after": town.silver,
        "upgrades": [e[1] for e in reviews],
        "bow_reasons": [e[2]["bow"]["reasons"] for e in reviews if e[2]],
        "armor_reasons": [e[2]["armor"]["reasons"] for e in reviews if e[2]],
        "equipped": [e[0] for e in events if e[0] == "equipment_upgraded"],
    }


def scenario(tmp_path, monkeypatch):
    return {
        # G1: Toxic at level 11 with 200 carried and 6,907 banked.
        "leveling_bow": visit(
            tmp_path, monkeypatch, "leveling_bow", leveling=True, level=11,
            carried=200, stored=6907, spare_packs=1, vendors=[5],
        ),
        # G2: level 12 at the Armorer with the dye-5 starter Coat.
        "leveling_armor": visit(
            tmp_path, monkeypatch, "leveling_armor", leveling=True, level=12,
            carried=200, stored=6907, spare_packs=1, vendors=[4],
        ),
        # G3: nothing banked; one arrow pack still to buy on this visit.
        "reserve_kept": visit(
            tmp_path, monkeypatch, "reserve_kept", leveling=True, level=11,
            carried=600, stored=0, spare_packs=0, vendors=[5],
        ),
        # G4: the same bags for an America farmer (no goal, no B2C profile).
        "america_bow": visit(
            tmp_path, monkeypatch, "america_bow", leveling=False, level=11,
            carried=200, stored=6907, spare_packs=1, vendors=[5],
        ),
        "america_armor": visit(
            tmp_path, monkeypatch, "america_armor", leveling=False, level=12,
            carried=10000, stored=0, spare_packs=1, vendors=[4],
        ),
    }


def run(tmp_path, monkeypatch, name):
    with monkeypatch.context() as patch:
        artifact = scenario(tmp_path / name, patch)
    path = tmp_path / name / "leveling-gear-funding.json"
    path.write_text(json.dumps(artifact, indent=2, sort_keys=True), encoding="utf-8")
    return path


def test_leveling_archer_funds_and_buys_level_gear(tmp_path, monkeypatch):
    first = run(tmp_path, monkeypatch, "first")
    second = run(tmp_path, monkeypatch, "second")
    assert first.read_bytes() == second.read_bytes()  # G5
    a = json.loads(first.read_text(encoding="utf-8"))

    # G1: banked silver funds the review and the BambooBow is equipped.
    bow = a["leveling_bow"]
    assert bow["withdrawn"] == [6907]
    assert bow["bow"] == "BambooBow" and bow["upgrades"] == [["BambooBow"]]
    assert bow["silver_after"] == 200 + 6907 - 204

    # G2: a Dress replaces the dye-5 Coat at level 12.
    armor = a["leveling_armor"]
    assert armor["armor"] == "Dress"
    assert not any("Armor form does not match" in r for r in armor["armor_reasons"][0])

    # G3: the missing arrow pack and fares stay reserved.
    kept = a["reserve_kept"]
    assert kept["bow"] == "LuckyBow" and kept["withdrawn"] == []
    assert "Keeping silver for supplies" in kept["bow_reasons"][0]

    # G4: America keeps supply-only funding, the 3,000 reserve and colour rule.
    america = a["america_bow"]
    assert america["withdrawn"] == [] and america["bow"] == "LuckyBow"
    assert "Keeping silver for supplies" in america["bow_reasons"][0]
    assert a["america_armor"]["armor"] == "Coat"
    assert "Armor form does not match" in a["america_armor"]["armor_reasons"][0]
