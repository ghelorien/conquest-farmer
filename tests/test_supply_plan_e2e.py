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
4. Farmers that are not leveling change behaviour.
5. A leveling IronArrow restock keeps the route's fixed potions (live
   2026-09-27 22:54: bandit.yaml's 5 after a restart; Suicide came back on
   one and died walking home at 23:12 with none left), or plans more than
   IronArrow's two packs.
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


def test_arrow_limited_hunts_leave_the_spare_bag_for_loot():
    # WingedSnakes with jump-Scatter (20:11-20:28): no potion in 17 minutes,
    # ~43 arrows a minute. Eight packs end the hunt at ~37 minutes; the old
    # plan still filled the bag with 27 potions (5 free slots on Suicide).
    wingedsnake = {"potions_per_min": 0.05, "arrows_per_min": 43}
    minutes, potions, packs = supply_plan.plan(
        wingedsnake, bag_slots=34, pack_size=200, max_packs=8, reserve=5
    )
    assert packs == 8 and minutes == pytest.approx(37.2, abs=0.1)
    assert potions == supply_plan.MIN_HUNT_POTIONS  # 5 + ceil(0.05 * 37.2 * 2) = 9
    # 34 - 7 spare packs - 10 potions: 17 more slots for loot than before.
    assert 34 - (packs - 1) - potions == 17
    # A measured need above the floor is kept, twice over: 5 + ceil(0.2 * 37.2 * 2).
    thirsty = {"potions_per_min": 0.2, "arrows_per_min": 43}
    assert supply_plan.plan(
        thirsty, bag_slots=34, pack_size=200, max_packs=8, reserve=5
    )[1] == 5 + 15


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


@pytest.mark.parametrize(
    "silver, expected", [(9000, (13, 1)), (20000, (30, 3)), (60000, (28, 5))]
)
def test_a_leveling_ironarrow_restock_is_planned_within_what_silver_pays(
    monkeypatch, silver, expected
):
    # 5
    from conquest import level_goal
    from conquest.discord_notify import write_json

    level_goal.start(level_goal.SCATTER_LEVEL)
    monkeypatch.setattr("conquest.equipment.leveling_archer", lambda: True)
    monkeypatch.setattr(
        "conquest.overnight.last_verified_price",
        lambda kind, path=None: {1000020: 60}.get(kind),
    )
    monkeypatch.setattr(
        "conquest.arrow_upgrades.arrow_pack_price", {1050000: 200, 1050001: 4800}.get
    )
    route = RouteLibrary().load("bandit")
    assert route.supplies.arrow_type == 1050001 and route.supplies.healing_restock_to == 5
    # Suicide on Bandits: 0.274 potions and 48.8 arrows a minute.
    write_json(
        supply_plan.RATES,
        {"routes": {"bandit": {"potions_per_min": 0.274, "arrows_per_min": 48.768}}},
    )
    # Back on one potion with a scroll, two LuckyArrow packs kept as the
    # fallback (their slots are taken) and a spent IronArrow remnant equipped
    # (recycled, not a paid pack).
    items = [
        {"uid": 1, "type_id": 1000020, "amount": 1},
        {"uid": 2, "type_id": 1060020, "amount": 1},
        {"uid": 3, "type_id": 1050000, "amount": 200},
        {"uid": 4, "type_id": 1050000, "amount": 200},
    ]
    loop = NS(
        route=route,
        town=lambda action, **kw: {
            "items": items,
            "capacity": 40,
            "silver": silver,
            "equipped_ammo": {"uid": 99, "type_id": 1050001, "amount": 2},
        },
        record=lambda event, **fields: None,
    )
    minutes, potions, packs = supply_plan.balance(loop)
    # 9,000 silver pays one 4,800 pack: ~20 minutes of IronArrows, potions
    # 1 + ceil(0.274 * 20.5 * 2) = 13. 20,000 pays three: ~62 minutes, the
    # bag's 30 slots of potions. 60,000 pays LEVELING_IRON_PACKS (five): the
    # bag then holds 28 potions, ~98 minutes.
    assert (potions, packs) == expected
    assert loop.route.supplies.healing_restock_to == potions
    assert loop.route.supplies.arrows_restock_to == packs * 1000
    assert loop.planned_arrows == {1050001: packs * 1000}
