"""Restock potions against arrow packs from each route's measured use.

Live 2026-09-27 (Toxic): Apparitions used 0.41 potions and 50 arrows a
minute, Poltergeists 2.3 potions and 50 arrows. The fixed 20 potions and 8
LuckyArrow packs lasted ~32 minutes on the first but ~7 on the second, so
every Poltergeist trip was for potions with most arrows unused.

Failure modes, written before the code:
1. The split ignores the bag: potions crowd out the packs (or the reverse).
2. A short or broken hunt (gear trip, death, restart) teaches wild rates.
3. The plan is set but the Blacksmith review's adopt_ammunition resets the
   arrow target, so the plan never reaches the arrow purchases.
4. Farmers that are not leveling on LuckyArrows change behaviour.
"""

from types import SimpleNamespace as NS

import pytest

from conquest import supply_plan
from conquest.routes import RouteLibrary

POLTERGEIST = {"potions_per_min": 2.3, "arrows_per_min": 50}
APPARITION = {"potions_per_min": 0.41, "arrows_per_min": 50}


def test_split_runs_both_supplies_out_together_within_the_bag():
    # 1: 40 slots - 4 free - 2 scrolls = 34 bag slots, reserve 5.
    minutes, potions, packs = supply_plan.plan(
        POLTERGEIST, bag_slots=34, pack_size=200, max_packs=8, reserve=5
    )
    assert (potions, packs) == (32, 3) and minutes == pytest.approx(11.7, abs=0.1)
    assert potions + packs - 1 == 34  # the equipped pack takes no slot
    # The fixed 20 potions + 8 packs lasted (20 - 5) / 2.3 = 6.5 minutes.
    minutes, potions, packs = supply_plan.plan(
        APPARITION, bag_slots=34, pack_size=200, max_packs=8, reserve=5
    )
    assert (potions, packs) == (27, 8) and minutes == pytest.approx(32)


def test_scarce_silver_is_split_like_the_bag():
    # 15:36: short of silver the restock bought ~30 potions first and paid for
    # 318 arrows, six minutes of shooting on Apparitions.
    budgeted = dict(
        bag_slots=34, pack_size=200, max_packs=8, reserve=5,
        potion_price=60, pack_price=200, carried_potions=5, carried_packs=1,
    )
    minutes, potions, packs = supply_plan.plan(APPARITION, budget=1500, **budgeted)
    assert (potions, packs) == (16, 5) and minutes == pytest.approx(20)
    # Plenty of silver plans exactly as the bag alone does.
    assert supply_plan.plan(APPARITION, budget=100_000, **budgeted)[1:] == (27, 8)
    # Nothing affordable beyond the way-back reserve: keep the fixed targets.
    broke = {**budgeted, "carried_potions": 3}
    assert supply_plan.plan(APPARITION, budget=0, **broke) is None


def test_short_or_broken_hunts_teach_nothing_and_rates_blend():
    # 2
    supply_plan.begin_hunt("poltergeist", {"potions": 32, "arrows": 600}, now=0)
    assert supply_plan.end_hunt("poltergeist", {"potions": 30, "arrows": 590}, now=120) is None
    supply_plan.begin_hunt("poltergeist", {"potions": 32, "arrows": 600}, now=0)
    assert supply_plan.end_hunt("poltergeist", {"potions": 40, "arrows": 100}, now=600) is None
    supply_plan.begin_hunt("apparition", {"potions": 20, "arrows": 1600}, now=0)
    assert supply_plan.end_hunt("poltergeist", {"potions": 5, "arrows": 100}, now=600) is None
    supply_plan.begin_hunt("poltergeist", {"potions": 32, "arrows": 600}, now=0)
    first = supply_plan.end_hunt("poltergeist", {"potions": 12, "arrows": 100}, now=600)
    assert first["potions_per_min"] == 2 and first["arrows_per_min"] == 50
    supply_plan.begin_hunt("poltergeist", {"potions": 32, "arrows": 600}, now=0)
    second = supply_plan.end_hunt("poltergeist", {"potions": 2, "arrows": 200}, now=600)
    assert second["potions_per_min"] == pytest.approx(2.5)  # 3/min blended with 2
    assert second["hunts"] == 2


def loop_for(route, items, capacity=40):
    events = []
    loop = NS(
        route=route,
        town=lambda action, **kw: {
            "items": items,
            "capacity": capacity,
            "silver": 5000,
            "equipped_ammo": {"uid": 99, "type_id": 1050000, "amount": 12},
        },
        record=lambda event, **fields: events.append((event, fields)),
        events=events,
    )
    return loop


def test_balance_sets_this_restocks_targets_and_keeps_them_through_adoption(
    monkeypatch,
):
    # 3: as live, the level goal runs and keeps 5 potions for the way back.
    from conquest import level_goal

    level_goal.start(level_goal.SCATTER_LEVEL)
    monkeypatch.setattr("conquest.equipment.leveling_archer", lambda: True)
    monkeypatch.setattr(
        "conquest.overnight.last_verified_price",
        lambda kind, path=None: {1000020: 60, 1050000: 200}.get(kind),
    )
    route = RouteLibrary().load("poltergeist")
    route = route.model_copy(
        update={"supplies": route.supplies.model_copy(update={"arrow_type": 1050000})}
    )
    # Back from a hunt: 3 potions, no spare pack, one scroll, one kept item.
    items = [
        {"uid": 1, "type_id": 1000020, "amount": 1},
        {"uid": 2, "type_id": 1000020, "amount": 1},
        {"uid": 3, "type_id": 1000020, "amount": 1},
        {"uid": 4, "type_id": 1060020, "amount": 1},
        {"uid": 5, "type_id": 410301, "amount": 1},
    ]
    supply_plan.begin_hunt("poltergeist", {"potions": 32, "arrows": 600}, now=0)
    loop = loop_for(route, items)
    monkeypatch.setattr(supply_plan.time, "time", lambda: 600.0)
    minutes, potions, packs = supply_plan.balance(loop)
    # 40 - 4 free - 1 kept item - 2 scrolls = 33 bag slots.
    assert (potions, packs) == (31, 3)
    assert loop.route.supplies.healing_restock_to == 31
    assert loop.route.supplies.arrows_restock_to == 600
    assert loop.events[-1][0] == "supply_plan"
    # The Blacksmith review's adopt_ammunition must not restore 1,600 arrows.
    from conquest.overnight import OvernightLoop

    real = OvernightLoop.__new__(OvernightLoop)
    real.route, real.record = loop.route, lambda *a, **kw: None
    real.planned_arrows = loop.planned_arrows
    real.town = lambda action, **kw: (
        {"items": items, "equipped_ammo": {"uid": 99, "type_id": 1050000, "amount": 12}}
        if action == "supplies"
        else {}
    )
    monkeypatch.setattr(
        "conquest.arrow_upgrades.current_arrow", lambda *a, **kw: 1050000
    )
    real.adopt_ammunition(state={})
    assert real.route.supplies.arrows_restock_to == 600


def test_farmers_off_lucky_leveling_keep_their_fixed_targets(monkeypatch):
    # 4
    route = RouteLibrary().load("poltergeist")
    supply_plan.begin_hunt("poltergeist", {"potions": 32, "arrows": 600}, now=0)
    monkeypatch.setattr(supply_plan.time, "time", lambda: 600.0)
    monkeypatch.setattr("conquest.equipment.leveling_archer", lambda: False)
    lucky = route.model_copy(
        update={"supplies": route.supplies.model_copy(update={"arrow_type": 1050000})}
    )
    loop = loop_for(lucky, [])
    assert supply_plan.balance(loop) is None
    assert loop.route.supplies == lucky.supplies
    # Iron or Speed packs keep their own two-pack rule even while leveling.
    monkeypatch.setattr("conquest.equipment.leveling_archer", lambda: True)
    iron = route.model_copy(
        update={"supplies": route.supplies.model_copy(update={"arrow_type": 1050001})}
    )
    loop = loop_for(iron, [])
    assert supply_plan.balance(loop) is None
    assert loop.route.supplies == iron.supplies
