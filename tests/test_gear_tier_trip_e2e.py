"""End-to-end: the level goal's gear trips missed the shop tier levels (live 2026-09-27).

The goal sent the farmer to town every GEAR_STEP (5) levels counted from its
first verified level. On Toxic those trips fell at 6, 11 and 16, while Twin
City's archer tiers unlock at 8 (BambooBow), 10 (CopperRing, OxhideBoots), 12
(Dress), 15 (HuntingBow: attack 54-67 against the BambooBow's 10-13, and
DeerskinCoat) and 20. Hunting levels 15 and 16 with the BambooBow wastes most
of each kill. An operator had to set reviewed_level back by hand to trigger
the trips at 12 and 15.

Real code under test: level_goal.due/mark_reviewed, the saved shop catalog
(archer_shop_catalog), equipment.upgrade_reason and category. Fakes only at the
worker's gear read.

Failure modes, written before the fix:
 T1 A newly usable, affordable tier above the worn item (HuntingBow at 15) does
    not send the farmer to town until the fixed five-level step.
 T2 After that trip (reviewed at 15) the same tier keeps triggering trips.
 T3 An unaffordable tier triggers a pointless trip.
 T4 The worn-gear read repeats on every hunt tick instead of once per level.
 T5 Without a saved catalog the five-level step no longer works.
 T6 The artifact is not repeatable.
"""

import copy
import json

from conquest import archer_shop_catalog, level_goal


def product(type_id, name, level, price, attack=(0, 0), defense=0):
    return dict(
        type_id=type_id, name=name, level=level, price=price, profession=0,
        sex=0, attack_min=attack[0], attack_max=attack[1], defense=defense,
        dodge=0,
    )


# Twin City shops as recorded live on Toxic (subset).
CATALOG = {
    "cities": {
        "1002": {
            "5": {"products": [
                product(500005, "BambooBow", 8, 204, (10, 13)),
                product(500015, "HuntingBow", 15, 724, (54, 67)),
                product(500025, "MulberryBow", 20, 1100, (62, 77)),
            ]},
            "1": {"products": [
                product(150015, "CopperRing", 10, 200, (7, 18)),
                product(160015, "OxhideBoots", 10, 400, defense=0),
            ]},
            "4": {"products": [
                product(132315, "Dress", 12, 258, defense=13),
                product(133305, "DeerskinCoat", 15, 780, defense=15),
            ]},
        }
    }
}


def worn(level, bow=("BambooBow", 500005, 8, (10, 13)), armor=("Dress", 132315, 12, 13)):
    def item(name, type_id, item_level, attack=(0, 0), defense=0):
        return dict(
            uid=type_id, type_id=type_id, name=name, level=item_level, profession=0,
            sex=0, plus=0, gem1=0, gem2=0, attack_min=attack[0],
            attack_max=attack[1], defense=defense, dodge=0,
        )

    return {
        "level": level,
        "profession": 40,
        "map_id": 1002,
        "equipment": {
            "bow": item(bow[0], bow[1], bow[2], bow[3]),
            "armor": item(armor[0], armor[1], armor[2], defense=armor[3]),
            "ring": item("CopperRing", 150015, 10, (7, 18)),
            "boots": item("OxhideBoots", 160015, 10),
        },
    }


def run_levels(levels, *, reviewed, silver, catalog=True, bow=None, armor=None):
    reads = []

    def gear_reader(level):
        def read():
            reads.append(level)
            kwargs = {}
            if bow:
                kwargs["bow"] = bow
            if armor:
                kwargs["armor"] = armor
            return worn(level, **kwargs)

        return read

    level_goal.start(23)
    level_goal.mark_reviewed(reviewed)
    steps = []
    for level in levels:
        for _tick in range(3):  # three hunt ticks at each level
            steps.append(
                [level, level_goal.due(level, gear=gear_reader(level), city=1002, silver=silver)]
            )
    return {"steps": steps, "reads": reads}


def scenario(tmp_path, monkeypatch):
    monkeypatch.setattr(archer_shop_catalog, "SEED", tmp_path / "no-seed.json")
    monkeypatch.setattr(archer_shop_catalog, "RUNTIME", tmp_path / "catalog.json")
    archer_shop_catalog.write_json(archer_shop_catalog.RUNTIME, copy.deepcopy(CATALOG))
    level_goal._tier_checks.clear()
    result = {
        # T1/T4: BambooBow and Dress worn, reviewed at 12, 7,000 silver.
        "huntingbow_at_15": run_levels([13, 14, 15], reviewed=12, silver=7000),
    }
    # T2: the trip at 15 was reviewed; 16-19 unlock nothing new.
    level_goal._tier_checks.clear()
    result["after_review"] = run_levels(
        [16, 17, 18, 19], reviewed=15, silver=7000,
        bow=("HuntingBow", 500015, 15, (54, 67)), armor=("DeerskinCoat", 133305, 15, 15),
    )
    # T3: only 500 silver at 20: MulberryBow (1,100) is out of reach.
    level_goal._tier_checks.clear()
    result["unaffordable"] = run_levels(
        [20], reviewed=18, silver=500,
        bow=("HuntingBow", 500015, 15, (54, 67)), armor=("DeerskinCoat", 133305, 15, 15),
    )
    # T5: no saved catalog: the five-level step still sends the farmer.
    level_goal._tier_checks.clear()
    archer_shop_catalog.write_json(archer_shop_catalog.RUNTIME, {})
    result["no_catalog"] = run_levels([14, 16, 17], reviewed=12, silver=7000)
    return result


def run(tmp_path, monkeypatch, name):
    root = tmp_path / name
    root.mkdir()
    with monkeypatch.context() as patch:
        artifact = scenario(root, patch)
    path = root / "gear-tier-trip.json"
    path.write_text(json.dumps(artifact, indent=2, sort_keys=True), encoding="utf-8")
    return path


def test_gear_trips_follow_the_shop_tiers(tmp_path, monkeypatch):
    first = run(tmp_path, monkeypatch, "first")
    second = run(tmp_path, monkeypatch, "second")
    assert first.read_bytes() == second.read_bytes()  # T6
    a = json.loads(first.read_text(encoding="utf-8"))

    # T1: nothing at 13-14, a trip as soon as 15 is verified.
    steps = a["huntingbow_at_15"]["steps"]
    assert [s[1] for s in steps if s[0] < 15] == [None] * 6
    assert [s[1] for s in steps if s[0] == 15] == ["gear"] * 3
    # T4: the worn gear is read once, at 15 (13-14 unlock no tier).
    assert a["huntingbow_at_15"]["reads"] == [15]

    # T2: reviewed at 15, levels 16-19 stay put.
    assert [s[1] for s in a["after_review"]["steps"]] == [None] * 12

    # T3: an unaffordable tier alone never sends the farmer.
    assert [s[1] for s in a["unaffordable"]["steps"]] == [None] * 3

    # T5: the five-level fallback (reviewed 12 -> 17) without a catalog.
    fallback = a["no_catalog"]["steps"]
    assert [s[1] for s in fallback if s[0] in (14, 16)] == [None] * 6
    assert [s[1] for s in fallback if s[0] == 17] == ["gear"] * 3
