"""End-to-end: a leveling LuckyArrow archer went to town every 18 minutes (live 2026-09-27).

Toxic (level 13, BambooBow) killed about 15 Robins a minute and emptied its two
200-arrow LuckyArrow packs in roughly 18 minutes; the 11:26 trip was for arrows
alone (1 left) with 16 potions still carried. The two-pack cap was set for
5,000-arrow SpeedArrow packs (10,000 arrows), where two packs last hours.

Real code under test: arrow_upgrades (pack count and cap), OvernightLoop.buy_supply,
banking.shopping_budget and level_goal. Fakes only at the town bridge.

Failure modes, written before the fix:
 A1 A leveling LuckyArrow archer is refused a third pack, so it leaves town
    with 400 arrows.
 A2 America farmers lose their two-pack rule.
 A3 IronArrow or SpeedArrow packs (1,000 or 5,000 arrows) exceed two packs.
 A4 The restock withdrawal does not fund the extra packs.
 A5 The artifact is not repeatable.
 A6 The route's refill target stays at two packs: adopt_ammunition set
    arrows_restock_to to 400 (live 11:43: one pack bought, 400 arrows).
"""

import json
from types import SimpleNamespace as NS

from conquest import arrow_upgrades, banking, level_goal
from conquest.overnight import OvernightLoop
from conquest.routes import RouteLibrary


def bag(kind, packs):
    return {
        "silver": 5000,
        "items": [
            {"uid": 100 + n, "type_id": kind, "amount": 200, "limit": 200}
            for n in range(packs - 1)
        ]
        + [{"uid": 90, "type_id": 1000010, "amount": 20, "limit": 1}],
        "capacity": 40,
        "equipped_ammo": {"uid": 99, "type_id": kind, "amount": 150, "limit": 200},
    }


def buys(kind, leveling, start=2):
    """Pack purchases the route makes before its cap refuses one."""
    if leveling:
        level_goal.start(23)
    else:
        level_goal.stop()
    state = bag(kind, start)
    bought = []

    def town(action, **fields):
        if action == "supplies":
            return json.loads(json.dumps(state))
        if action == "buy":
            arrow_upgrades.require_arrow_purchase_room(state, fields["type_id"])
            state["items"].append(
                {"uid": 200 + len(bought), "type_id": fields["type_id"], "amount": 200, "limit": 200}
            )
            bought.append(fields["type_id"])
            return {"bought": fields["type_id"], "amount": 200, "price": 200}
        raise AssertionError(action)

    loop = OvernightLoop.__new__(OvernightLoop)
    loop.route = RouteLibrary().load("robin")
    loop.town = town
    loop.record = lambda *a, **k: None
    for _ in range(10):
        if not loop.buy_supply(5, kind):
            break
    return {"packs_after": arrow_upgrades.arrow_pack_count(state), "bought": len(bought)}


def refill_target(leveling):
    """The refill target the route adopts for its current LuckyArrow tier."""
    if leveling:
        level_goal.start(23)
    else:
        level_goal.stop()
    arrows = dict(
        uid=99, type_id=1050000, name="LuckyArrow", level=1, profession=40, sex=0,
        plus=0, gem1=0, gem2=0, attack_min=10, attack_max=10, defense=0, dodge=0,
    )
    state = {"level": 14, "profession": 40, "map_id": 1002, "equipment": {"arrows": arrows}}
    loop = OvernightLoop.__new__(OvernightLoop)
    loop.route = RouteLibrary().load("robin")
    loop.town = lambda action, **fields: bag(1050000, 2) if action == "supplies" else state
    loop.record = lambda *a, **k: None
    loop.adopt_ammunition(state)
    return loop.route.supplies.arrows_restock_to


def scenario(monkeypatch):
    monkeypatch.setattr(banking, "transport_reserve", lambda: 200)
    route = RouteLibrary().load("robin")
    level_goal.start(23)
    leveling_budget = banking.shopping_budget(route, bag(1050000, 2))
    level_goal.stop()
    america_budget = banking.shopping_budget(route, bag(1050000, 2))
    return {
        "leveling_lucky": buys(1050000, True),
        "america_lucky": buys(1050000, False),
        "leveling_iron": buys(1050001, True),
        "leveling_speed": buys(1050002, True),
        "budget": {"leveling": leveling_budget, "america": america_budget},
        "refill_target": {"leveling": refill_target(True), "america": refill_target(False)},
    }


def run(tmp_path, monkeypatch, name):
    with monkeypatch.context() as patch:
        artifact = scenario(patch)
    path = tmp_path / name / "leveling-arrow-packs.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(artifact, indent=2, sort_keys=True), encoding="utf-8")
    return path


def test_leveling_lucky_archer_carries_eight_packs(tmp_path, monkeypatch):
    first = run(tmp_path, monkeypatch, "first")
    second = run(tmp_path, monkeypatch, "second")
    assert first.read_bytes() == second.read_bytes()  # A5
    a = json.loads(first.read_text(encoding="utf-8"))
    # A1: 2 -> 8 LuckyArrow packs (1,600 arrows).
    assert a["leveling_lucky"] == {"packs_after": 8, "bought": 6}
    # A2: America keeps one equipped and one spare.
    assert a["america_lucky"] == {"packs_after": 2, "bought": 0}
    # A3: larger packs keep the two-pack rule even while leveling.
    assert a["leveling_iron"]["packs_after"] == 2
    assert a["leveling_speed"]["packs_after"] == 2
    # A4: the withdrawal covers six more 200-silver packs than America's.
    assert a["budget"]["leveling"] - a["budget"]["america"] == 6 * 200
    # A6: the route refills to eight packs (1,600) while leveling, 400 otherwise.
    assert a["refill_target"] == {"leveling": 1600, "america": 400}
