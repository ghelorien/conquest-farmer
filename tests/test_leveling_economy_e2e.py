"""Hunt a bracket back while the wallet cannot pay for the next one.

Live 2026-09-27 (Toxic, level 21-22): Poltergeists cost more silver in
potions, arrows and scrolls than their drops returned and the bank ran down
to 94 silver; a restock then could not pay for arrows.

Failure modes, written before the code:
1. A poor farmer keeps hunting the dearer bracket until it cannot restock.
2. The farmer flaps between brackets around a single threshold.
3. A farmer far past the previous bracket drops to monsters too weak to
   level on.
4. Farmers without the level goal (America) change routes.
5. The hold is forgotten by a controller restart.
"""

from types import SimpleNamespace as NS

from conquest import banking, level_goal, leveling_economy
from conquest.discord_notify import write_json
from conquest.leveling_routes import bracket, desired_route


def loop_with(silver, stored=0):
    write_json(banking.STATUS, {"stored_silver": stored})
    events = []
    return NS(
        town=lambda action, **kw: {"silver": silver, "items": [], "capacity": 40},
        record=lambda event, **fields: events.append(event),
        events=events,
    )


def choose(loop, level):
    route, entry = desired_route(22 if level == 22 else level)
    return leveling_economy.economy_route(loop, level, route, entry)


def test_poor_farmer_hunts_the_cheaper_bracket_until_the_wallet_recovers():
    level_goal.start(level_goal.SCATTER_LEVEL)
    # 1: level 22 would hunt Poltergeists; 94 carried + 0 banked.
    loop = loop_with(94)
    route, entry = choose(loop, 22)
    assert route.id == "apparition" and entry == bracket(21)
    assert loop.events == ["economy_hold_started"]
    # 2: 4,000 is above LOW but below HIGH: still held (no flapping).
    loop = loop_with(1000, stored=3000)
    route, _ = choose(loop, 22)
    assert route.id == "apparition" and loop.events == []
    # 5: a restarted controller still holds (state on disk).
    assert leveling_economy.read_json(leveling_economy.HOLD)["active"] is True
    # Back to 8,000: move on.
    loop = loop_with(500, stored=7500)
    route, _ = choose(loop, 22)
    assert route.id == "poltergeist" and loop.events == ["economy_hold_ended"]
    # Without a hold, 4,000 is enough to go on.
    loop = loop_with(1000, stored=3000)
    route, _ = choose(loop, 22)
    assert route.id == "poltergeist"


def test_no_hold_far_past_the_previous_bracket_or_without_the_goal():
    # 3: level 24 is more than two levels past Apparition's top (21).
    level_goal.start(level_goal.SCATTER_LEVEL)
    loop = loop_with(10)
    route, _ = choose(loop, 24)
    assert route.id == "poltergeist" and loop.events == []
    # 4: America farmers keep the bracket rule.
    level_goal.stop()
    loop = loop_with(10)
    route, _ = choose(loop, 22)
    assert route.id == "poltergeist" and loop.events == []
