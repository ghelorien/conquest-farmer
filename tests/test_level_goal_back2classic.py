"""Back2Classic level-to-Scatter mode: potion tiers, gear trips and the goal stop.

Ways this can fail, written before the code:
1. A level-1 archer is sent out with Painkillers it cannot afford, or none.
2. A poor character buys nothing because the "right" tier is too expensive.
3. The Pharmacist does not stock the chosen tier and the purchase is refused.
4. A cheaper tier the character relies on is sold as junk at the next visit.
5. Switching to a stronger tier strands the old potions as "not potions", so
   the route returns to town at once although the bag still heals.
6. The existing Painkiller farmer (America, no goal) changes behavior:
   Painkiller stays the bought tier whatever its maximum HP.
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


# 4
def test_no_hp_potion_is_junk_whatever_the_active_tier():
    for active in (1000000, 1000020, 1002000):
        potion_tiers.set_active(active)
        assert not any(junk_type(t) for t in potion_tiers.HEALING_POTIONS)
    assert junk_type(1001000)  # mana stays junk


# 5
def test_every_tier_counts_and_heals():
    potion_tiers.set_active(1000010)
    carried = bag(Item(1, 1000000, 1), Item(2, 1000010, 2), Item(3, 1000020, 1))
    assert potion_tiers.count(carried) == 4  # a Stancher below the tier still heals
    assert potion_tiers.count(carried, include=1000000) == 4
    assert potion_tiers.pick(carried, 60).type_id == 1000000
    assert potion_tiers.pick(carried, 90).type_id == 1000010
    assert potion_tiers.pick(carried, 400).type_id == 1000020
    assert potion_tiers.pick(bag(Item(1, 1000000)), 50).type_id == 1000000
    assert potion_tiers.pick(bag(Item(1, 1050000)), 50) is None


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
    products = [
        {"type_id": 1000000, "price": 5},
        {"type_id": 1000010, "price": 18},
        {"type_id": 1000020, "price": 60},
    ]
    # 6: an America farmer without the goal keeps the route Painkiller.
    assert level_goal.healing_type(loop) == 1000020
    assert loop.route.supplies.healing_type == 1000020
    assert not potion_tiers.TIER.exists() and events == []
    # With the goal the tier follows max HP and the live price.
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


# Alex: "As soon as you get attacked by damage that is over 1% of max hp jump
# away and start attacking back don't tank a few hits before jumping."
def test_a_hit_over_one_percent_of_max_hp_calls_for_a_jump(monkeypatch):
    from dataclasses import replace
    from test_native_farm import Life, setup
    from conquest import native_farm

    supervisor, _, _, _ = setup(monkeypatch)
    clock = [100.0]
    monkeypatch.setattr(native_farm.time, "monotonic", lambda: clock[0])
    life = Life(current_hp=300, max_hp=300, dead_candidate=False, position=(20, 20))
    supervisor.note_health(life)
    supervisor.note_health(replace(life, current_hp=297))  # exactly 1%: no
    supervisor.note_health(replace(life, current_hp=300))  # healing: no
    assert supervisor.last_damage_at == -float("inf")
    clock[0] = 101.0
    supervisor.note_health(replace(life, current_hp=296))  # 1.3%: a hit
    assert supervisor.last_damage_at == 101.0
    # The very next escape check jumps.
    supervisor.scene_timestamp = 101.0
    supervisor.escape_monsters = (NS(position=(21, 20)),)
    supervisor.recovery.terrain = NS(walkable=lambda p: True)
    assert supervisor.ranged_escape((20, 20), (0, 0, 50, 50)) is not None
    # A death is not a hit to jump from.
    clock[0] = 110.0
    supervisor.note_health(replace(life, current_hp=0, dead_candidate=True))
    assert supervisor.last_damage_at == 101.0


# It replaced the former 10%-of-max-HP bar, which a typical 9% Apparition hit
# never reached (live 2026-09-27: 43% of hits got no jump).
def test_jumps_away_on_the_first_hit(monkeypatch):
    from test_native_farm import setup
    from conquest import native_farm

    supervisor, _, _, _ = setup(monkeypatch)
    monkeypatch.setattr(native_farm.time, "monotonic", lambda: 100.0)
    supervisor.scene_timestamp = 100.0
    supervisor.escape_monsters = (NS(position=(24, 20)),)
    supervisor.recovery.terrain = NS(walkable=lambda p: True)
    # Not hit yet, one monster 4 tiles away: keep shooting.
    assert supervisor.ranged_escape((20, 20), (0, 0, 50, 50)) is None
    # The first hit, however small, jumps at once to 6+ tiles from it.
    supervisor.last_damage_at = 100.0
    landing = supervisor.ranged_escape((20, 20), (0, 0, 50, 50))
    assert max(abs(landing[0] - 24), abs(landing[1] - 20)) >= 6
    assert supervisor.escape_context["reason"] == "recent_damage"
    # A hit already answered by a jump does not trigger another one.
    supervisor.escape_damage_consumed_at = 100.0
    assert supervisor.ranged_escape((20, 20), (0, 0, 50, 50)) is None
    # Surrounded by two adjacent monsters still jumps without any damage.
    supervisor.last_damage_at = -float("inf")
    supervisor.escape_monsters = (NS(position=(21, 20)), NS(position=(20, 21)))
    assert supervisor.ranged_escape((20, 20), (0, 0, 50, 50)) is not None


# Near a map edge the camera stops following and the player is not drawn at
# the viewport centre; a landing judged from the centre then clicks the HUD
# and the dispatch refuses it (Suicide, 2026-09-27 14:06).
def test_escape_landings_are_judged_from_the_players_screen_anchor(monkeypatch):
    from test_native_farm import setup
    from conquest import native_farm
    from conquest.viewport import clear_scene

    supervisor, _, _, _ = setup(monkeypatch)
    monkeypatch.setattr(native_farm.time, "monotonic", lambda: 100.0)
    supervisor.scene_timestamp = 100.0
    supervisor.escape_monsters = (NS(position=(21, 20)),)
    supervisor.recovery.terrain = NS(walkable=lambda p: True)
    supervisor.last_damage_at = 100.0
    size = native_farm.size_for(supervisor.observer)
    anchor = (518, 330)  # drawn 66 px above the centre

    def click(landing):
        dx, dy = landing[0] - 20, landing[1] - 20
        return round(anchor[0] + (dx - dy) * 32), round(anchor[1] + (dx + dy) * 16)

    centred = supervisor.ranged_escape((20, 20), (0, 0, 50, 50))
    assert not clear_scene(click(centred), size)  # what the old choice clicked
    landing = supervisor.ranged_escape((20, 20), (0, 0, 50, 50), anchor=anchor)
    assert clear_scene(click(landing), size)
    assert max(abs(landing[0] - 21), abs(landing[1] - 20)) >= 6


# Alex: "When doing jump scatter you can't let enemies ever attack you, if
# enemies are within 1 tile of you you gotta jump scatter".
def test_jump_scatter_jumps_before_a_single_adjacent_monster_can_hit(monkeypatch):
    from test_native_farm import setup
    from conquest import native_farm

    supervisor, _, _, _ = setup(monkeypatch)
    monkeypatch.setattr(native_farm.time, "monotonic", lambda: 100.0)
    supervisor.scene_timestamp = 100.0
    supervisor.escape_monsters = (NS(position=(21, 20)),)  # one, not yet hitting
    supervisor.recovery.terrain = NS(walkable=lambda p: True)
    # Single-shot farming waits for a hit or a second adjacent monster.
    assert supervisor.ranged_escape((20, 20), (0, 0, 50, 50)) is None
    landing = supervisor.ranged_escape((20, 20), (0, 0, 50, 50), adjacent_trigger=1)
    assert landing is not None and max(abs(landing[0] - 21), abs(landing[1] - 20)) >= 6
    assert supervisor.escape_context["reason"] == "enemies_within_one_tile"
    # Two tiles away is not yet contact.
    supervisor.escape_monsters = (NS(position=(22, 20)),)
    assert supervisor.ranged_escape((20, 20), (0, 0, 50, 50), adjacent_trigger=1) is None


# Poltergeists hit from two tiles: in 61 of 100 hits (Toxic 2026-09-27
# 16:30-16:50) no monster stood within one tile and the archer had not moved.
def test_jump_scatter_leaves_before_a_monster_is_within_hitting_reach(monkeypatch):
    from test_native_farm import setup
    from conquest import native_farm

    supervisor, _, _, _ = setup(monkeypatch)
    monkeypatch.setattr(native_farm.time, "monotonic", lambda: 100.0)
    supervisor.scene_timestamp = 100.0
    supervisor.recovery.terrain = NS(walkable=lambda p: True)
    reach = native_farm.JUMP_SCATTER_REACH
    assert reach == 3
    for distance in (2, 3):
        supervisor.escape_monsters = (NS(position=(20 + distance, 20)),)
        landing = supervisor.ranged_escape(
            (20, 20), (0, 0, 50, 50), adjacent_trigger=1, reach=reach
        )
        assert landing is not None, distance
        assert max(abs(landing[0] - 20 - distance), abs(landing[1] - 20)) >= 6
        assert supervisor.escape_context["reason"] == "enemies_within_reach"
        assert supervisor.escape_context["nearest_enemy"] == distance
    # Four tiles out it keeps shooting.
    supervisor.escape_monsters = (NS(position=(24, 20)),)
    assert (
        supervisor.ranged_escape((20, 20), (0, 0, 50, 50), adjacent_trigger=1, reach=reach)
        is None
    )


def test_jump_scatter_escape_keeps_the_pack_inside_scatter_range(monkeypatch):
    # 18:45-18:56: the farthest landing often left the pack out of range and
    # 40% of escapes went over 1.5 s without a cast.
    from test_native_farm import setup
    from conquest import native_farm

    supervisor, _, _, _ = setup(monkeypatch)
    monkeypatch.setattr(native_farm.time, "monotonic", lambda: 100.0)
    supervisor.scene_timestamp = 100.0
    supervisor.recovery.terrain = NS(walkable=lambda p: True)
    # One Poltergeist at reach, the rest of the pack six tiles east.
    supervisor.escape_monsters = tuple(
        NS(position=p) for p in ((23, 20), (26, 19), (27, 21), (28, 20))
    )
    reach = native_farm.JUMP_SCATTER_REACH

    def pack_in_range(landing, radius=8):
        return sum(
            reach < max(abs(landing[0] - m.position[0]), abs(landing[1] - m.position[1])) <= radius
            for m in supervisor.escape_monsters
        )

    farthest = supervisor.ranged_escape(
        (20, 20), (0, 0, 50, 50), adjacent_trigger=1, reach=reach
    )
    kiting = supervisor.ranged_escape(
        (20, 20), (0, 0, 50, 50), adjacent_trigger=1, reach=reach, scatter_range=8
    )
    assert pack_in_range(farthest) == 0
    assert pack_in_range(kiting) >= 2
    # Still never within the attacker's reach, and 6+ tiles from it.
    assert min(
        max(abs(kiting[0] - m.position[0]), abs(kiting[1] - m.position[1]))
        for m in supervisor.escape_monsters
    ) > reach
    assert max(abs(kiting[0] - 23), abs(kiting[1] - 20)) >= 6


def test_scatter_landing_keeps_every_monster_beyond_hitting_reach():
    import numpy as np
    from conquest.navigation import TerrainMap
    from conquest.native_farm import JUMP_SCATTER_REACH
    from conquest.scatter_movement import scatter_landing

    terrain = TerrainMap(1002, 100, 100, np.zeros((100, 100), dtype=bool), "", (), ())
    supervisor = NS(recovery=NS(terrain=terrain))
    pack = [NS(world_position=(62 + dx, 50 + dy), current_hp=100) for dx, dy in ((0, 0), (1, 1), (2, -1))]
    landing = scatter_landing(supervisor, pack, (50, 50), (20, 20, 80, 80), 8)
    assert landing is not None
    nearest = min(
        max(abs(landing[0] - t.world_position[0]), abs(landing[1] - t.world_position[1]))
        for t in pack
    )
    assert JUMP_SCATTER_REACH < nearest <= 8


# A crowd can leave no landing 6+ tiles from every monster within 12 tiles:
# still jump clear of the monster hitting us instead of tanking it.
def test_crowded_hit_still_jumps_clear_of_the_attacker(monkeypatch):
    from test_native_farm import setup
    from conquest import native_farm

    supervisor, _, _, _ = setup(monkeypatch)
    monkeypatch.setattr(native_farm.time, "monotonic", lambda: 100.0)
    supervisor.scene_timestamp = 100.0
    # Only the line west is open; every west landing is 5 tiles from (10, 15).
    supervisor.recovery.terrain = NS(walkable=lambda p: p[1] == 20 and p[0] <= 20)
    supervisor.escape_monsters = (NS(position=(21, 20)), NS(position=(10, 15)))
    supervisor.last_damage_at = 100.0
    landing = supervisor.ranged_escape((20, 20), (0, 0, 50, 50))
    assert landing[1] == 20 and 20 - landing[0] >= 8
    assert supervisor.escape_context["crowded"] is True
    # Without the hit, one adjacent monster is no reason to leave.
    supervisor.last_damage_at = -float("inf")
    assert supervisor.ranged_escape((20, 20), (0, 0, 50, 50)) is None
    # A landing beside another monster is no escape at all.
    supervisor.last_damage_at = 100.0
    supervisor.escape_monsters = (NS(position=(21, 20)), NS(position=(10, 18)))
    supervisor.recovery.terrain = NS(walkable=lambda p: p[1] == 20 and 8 <= p[0] <= 20)
    assert supervisor.ranged_escape((20, 20), (0, 0, 50, 50)) is None


# 2026-09-28 00:19 (Toxic, level 36, Bandits): 19 monsters in view and no
# landing with fewer of them nearby, so it kept shooting a Bandit two tiles
# away above ESCAPE_LOW_HP until three hits in 1.5 s left 25%.
def test_jump_scatter_leaves_a_pack_no_landing_thins_out(monkeypatch):
    from test_native_farm import setup
    from conquest import native_farm

    supervisor, _, _, _ = setup(monkeypatch)
    monkeypatch.setattr(native_farm.time, "monotonic", lambda: 100.0)
    supervisor.scene_timestamp = 100.0
    reach = native_farm.JUMP_SCATTER_REACH
    # Only the line west is open, and every west landing has (8, 16) four
    # tiles away: none has fewer monsters nearby than the one in reach.
    supervisor.recovery.terrain = NS(walkable=lambda p: p[1] == 20 and p[0] <= 20)
    supervisor.escape_monsters = (NS(position=(22, 20)), NS(position=(8, 16)))
    kwargs = dict(adjacent_trigger=1, reach=reach)
    assert supervisor.ranged_escape((20, 20), (0, 0, 50, 50), **kwargs) is None
    landing = supervisor.ranged_escape((20, 20), (0, 0, 50, 50), scatter_range=8, **kwargs)
    assert landing is not None and landing[1] == 20
    assert max(abs(landing[0] - 22), abs(landing[1] - 20)) >= 6
    assert min(
        max(abs(landing[0] - m.position[0]), abs(landing[1] - m.position[1]))
        for m in supervisor.escape_monsters
    ) > reach
    assert supervisor.escape_context["crowded"] is True
    assert supervisor.escape_context["reason"] == "enemies_within_reach"
    # Every landing within another monster's reach: stay rather than jump
    # beside it.
    supervisor.escape_monsters = (NS(position=(22, 20)), NS(position=(10, 18)))
    assert supervisor.ranged_escape((20, 20), (0, 0, 50, 50), scatter_range=8, **kwargs) is None
    # Nothing within reach: keep shooting.
    supervisor.escape_monsters = (NS(position=(24, 20)), NS(position=(8, 16)))
    assert supervisor.ranged_escape((20, 20), (0, 0, 50, 50), scatter_range=8, **kwargs) is None


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
    # A Back2Classic character keeps funding itself after the goal ends.
    monkeypatch.setattr(level_goal, "back2classic", lambda: True)
    monkeypatch.setattr(level_goal, "_silver_cache", (-float("inf"), False))
    assert wanted_drop(silver)


def test_early_heals_and_jumping_away_outlive_the_goal_on_back2classic(monkeypatch):
    # The goal ends at 23; the 70% heal must not fall to the route's 40%.
    assert level_goal.HEAL_BELOW == 0.7
    monkeypatch.setattr(level_goal, "back2classic", lambda: False)
    assert not level_goal.protections()
    level_goal.start(23)
    assert level_goal.protections()
    level_goal.stop()
    assert not level_goal.protections()  # America without the goal: unchanged
    monkeypatch.setattr(level_goal, "back2classic", lambda: True)
    assert level_goal.protections()
