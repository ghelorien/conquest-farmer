"""Back2Classic level-to-Scatter mode: potion tiers, gear trips and the goal stop.

Ways this can fail, written before the code:
1. A level-1 archer is sent out with Painkillers it cannot afford, or none.
2. A poor character buys nothing because the "right" tier is too expensive.
3. The Pharmacist does not stock the chosen tier and the purchase is refused.
4. A cheaper tier the character relies on is sold as junk at the next visit.
5. Switching to a stronger tier strands the old potions as "not potions", so
   the route returns to town at once although the bag still heals.
6. The existing Painkiller farmer (no goal) changes behavior: Stanchers must
   still be junk and Painkiller stays the bought tier.
7. The gear trip fires on the first hunt (wasted walk) or never fires.
8. The goal never stops, or stops before the target level.
9. A route change resets the chosen tier to the YAML Painkiller.
10. Maximum HP is not read from memory (0/None) and a tier is guessed.
"""

import json
from dataclasses import dataclass
from types import SimpleNamespace as NS

import pytest

from conquest import level_goal, potion_tiers
from conquest.town_trade import junk_type


@pytest.fixture(autouse=True)
def runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(level_goal, "GOAL", tmp_path / "level-goal.json")
    monkeypatch.setattr(potion_tiers, "TIER", tmp_path / "healing-tier.json")
    monkeypatch.setattr(level_goal, "_silver_cache", (-float("inf"), False))
    from conquest import session_plan

    monkeypatch.setattr(session_plan, "PLAN", tmp_path / "session-plan.json")
    return tmp_path


@dataclass(frozen=True)
class Item:
    uid: int
    type_id: int
    amount: int = 1


def bag(*items):
    return NS(items=list(items), count=lambda t: sum(i.amount for i in items if i.type_id == t))


# 1, 2, 10
@pytest.mark.parametrize(
    "max_hp,silver,expected",
    [
        (80, 1000, 1000000),  # level 1: a Stancher restores a full bar
        (180, 1000, 1000010),  # Resolutive covers half
        (213, 1000, 1000010),  # level 12: Resolutive, not a Painkiller above max HP
        (420, 1000, 1000020),  # around Scatter level: Painkiller
        (420, 150, 1000010),  # cannot afford 5 Painkillers: strongest affordable
        (420, 10, 1000000),  # broke: cheapest, still leaves with potions
        (1500, 100000, 1002000),  # high level: Panacea (800 >= 750)
    ],
)
def test_tier_fits_max_hp_and_silver(max_hp, silver, expected):
    assert potion_tiers.choose(max_hp, silver, 5) == expected


# 3
def test_only_offered_tiers_are_chosen():
    offered = {1000000, 1000020}
    assert potion_tiers.choose(180, 1000, 5, offered=offered) == 1000020
    with pytest.raises(ValueError, match="no known HP potion"):
        potion_tiers.choose(180, 1000, 5, offered={1050000})


# 10
@pytest.mark.parametrize("max_hp", [0, None, 12.5])
def test_unknown_max_hp_never_guesses(max_hp):
    with pytest.raises(ValueError, match="Maximum HP"):
        potion_tiers.choose(max_hp, 1000, 5)


# 4, 6
def test_junk_follows_the_active_tier():
    # Legacy default: Painkiller active, lower tiers sold as before.
    assert junk_type(1000000) and junk_type(1000010) and not junk_type(1000020)
    assert junk_type(1001000)  # mana stays junk
    potion_tiers.set_active(1000000)
    assert not junk_type(1000000) and not junk_type(1000020)
    potion_tiers.set_active(1000020)
    assert junk_type(1000000) and not junk_type(1000030)


# 5
def test_every_usable_tier_counts_and_heals():
    potion_tiers.set_active(1000010)
    carried = bag(Item(1, 1000000, 1), Item(2, 1000010, 2), Item(3, 1000020, 1))
    assert potion_tiers.count(carried) == 3  # Stancher is below the tier
    assert potion_tiers.count(carried, include=1000000) == 4
    assert potion_tiers.pick(carried, 90).type_id == 1000010
    assert potion_tiers.pick(carried, 400).type_id == 1000020
    assert potion_tiers.pick(bag(Item(1, 1000000)), 50) is None


# 7, 8
def test_goal_gear_trips_and_stop():
    assert level_goal.due(1) is None  # no goal, no change to the old farmer
    level_goal.start(23)
    assert level_goal.due(0) is None
    assert level_goal.due(1) is None  # first verified level only starts counting
    assert level_goal.due(5) is None
    assert level_goal.due(6) == "gear"
    level_goal.mark_reviewed(6)
    assert level_goal.due(10) is None and level_goal.due(11) == "gear"
    assert level_goal.due(23) == "reached"
    level_goal.stop()
    assert level_goal.due(23) is None


def test_goal_rejects_bad_targets_and_clears_route_holds(runtime):
    from conquest import session_plan

    with pytest.raises(ValueError):
        level_goal.start(1)
    (runtime / "session-plan.json").write_text(
        json.dumps(
            {
                "active": True,
                "mode": "save_silver",
                "route_id": "poltergeist",
                "silver_target": 50000,
                "upgrade_maps": [1002],
                "started_at": 1,
            }
        )
    )
    level_goal.start(23)
    assert not json.loads((runtime / "session-plan.json").read_text())["active"]
    assert level_goal.note().startswith("Back2Classic: leveling to 23")


# 1, 3, 9: the Pharmacist step on a fake route loop.
def test_pharmacist_step_switches_route_tier_and_records(runtime):
    from conquest.routes import RouteLibrary

    route = RouteLibrary().load("pheasant")
    events = []
    loop = NS(
        route=route,
        living=lambda: {"embedded_controls": {"life": {"max_hp": 90}}},
        town=lambda action, **_: (
            {"products": products} if action == "shop" else {"silver": 400}
        ),
        record=lambda event, **fields: events.append((event, fields)),
    )
    products = [{"type_id": 1000000}, {"type_id": 1000010}, {"type_id": 1000020}]
    # With or without a goal, the tier follows max HP and price.
    assert level_goal.healing_type(loop) == 1000000
    assert potion_tiers.active_type() == 1000000
    level_goal.start(23)
    assert level_goal.healing_type(loop) == 1000000
    assert loop.route.supplies.healing_type == 1000000
    assert potion_tiers.active_type() == 1000000
    assert events[-1][0] == "healing_tier_selected"


# The whole ladder, level 1 to Scatter, as a repeatable artifact.
def test_e2e_ladder_to_scatter_artifact(runtime):
    from conquest.leveling_routes import presets

    level_goal.start(level_goal.SCATTER_LEVEL)
    rows = presets()
    silver = 200
    timeline = []
    for level in range(1, 30):
        step = level_goal.due(level)
        # Archer HP grows roughly 17 per level from about 60 at level 1.
        max_hp = 60 + 17 * (level - 1)
        bracket = next(r for r in rows if r["levels"][0] <= level <= r["levels"][1])
        row = {"level": level, "hunt": bracket["id"], "step": step}
        if level == 1 or step == "gear":
            row["potion"] = potion_tiers.name(potion_tiers.choose(max_hp, silver, 5))
            level_goal.mark_reviewed(level)
        timeline.append(row)
        if step == "reached":
            break
        silver += 150 * level
    artifact = runtime / "back2classic-ladder.json"
    artifact.write_text(json.dumps(timeline, indent=2))
    trips = [r["level"] for r in timeline if r["step"] == "gear"]
    assert trips == [6, 11, 16, 21]
    assert timeline[-1] == {"level": 23, "hunt": "poltergeist", "step": "reached"}
    assert timeline[0]["potion"] == "Stancher"
    assert [r["potion"] for r in timeline if r["step"] == "gear"][-1] == "Painkiller"


# Alex: "if you are taking significant damage from enemies make sure you jump away"
def test_goal_jumps_away_only_from_significant_damage(monkeypatch):
    from test_native_farm import setup
    from conquest import native_farm

    supervisor, _, _, _ = setup(monkeypatch)
    supervisor.escape_damage_share = level_goal.ESCAPE_DAMAGE_SHARE
    monkeypatch.setattr(native_farm.time, "monotonic", lambda: 100.0)
    supervisor.scene_timestamp = 100.0
    supervisor.escape_monsters = (NS(position=(24, 20)),)
    supervisor.recovery.terrain = NS(walkable=lambda p: True)
    supervisor.last_damage_at = 100.0
    supervisor.damage_events = [(99.5, 0.03), (100.0, 0.04)]  # 7%: keep shooting
    assert supervisor.ranged_escape((20, 20), (0, 0, 50, 50)) is None
    supervisor.damage_events.append((100.0, 0.05))  # 12% in 1.25 s: jump
    assert supervisor.ranged_escape((20, 20), (0, 0, 50, 50)) is not None
    assert supervisor.escape_context["reason"] == "recent_damage"
    # Surrounded by two adjacent monsters still jumps without any damage.
    supervisor.damage_events = []
    supervisor.last_damage_at = -float("inf")
    supervisor.escape_monsters = (NS(position=(21, 20)), NS(position=(20, 21)))
    assert supervisor.ranged_escape((20, 20), (0, 0, 50, 50)) is not None


# Alex: "we need to be able to pickup the money on the ground"
def test_goal_picks_up_dropped_silver_only_while_active(monkeypatch):
    from conquest.memory_ground import GroundItem, wanted_drop

    silver = GroundItem(1, 100000, 1090000, (1, 1))
    assert not wanted_drop(silver)  # the old farmer still ignores silver
    level_goal.start(23)
    monkeypatch.setattr(level_goal, "_silver_cache", (-float("inf"), False))
    assert wanted_drop(silver)
    assert not wanted_drop(GroundItem(2, 100000, 500008, (1, 1), plus=0))
    level_goal.stop()
    monkeypatch.setattr(level_goal, "_silver_cache", (-float("inf"), False))
    assert not wanted_drop(silver)
