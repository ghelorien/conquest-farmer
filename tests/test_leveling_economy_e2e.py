"""Hunt a bracket back while the wallet cannot pay for the next one.

Live 2026-09-27 (Toxic, level 21-22): Poltergeists cost more silver in
potions, arrows and scrolls than their drops returned and the bank ran down
to 94 silver; a restock then could not pay for arrows.

Failure modes, written before the code:
1. A poor farmer keeps hunting the dearer bracket until it cannot restock.
2. The farmer flaps between brackets around a single threshold.
3. A farmer far past the previous bracket drops to monsters too weak to
   level on (brackets are five levels wide: GRACE_LEVELS=5 now spans the
   whole next bracket, see 6).
4. Farmers without the level goal (America) change routes.
5. The hold is forgotten by a controller restart.
6. A Scatter archer is sent broke to its leveling spot (live 2026-09-27:
   WingedSnakes 5,500-7,300 XP a minute but silver-negative, Poltergeists
   2,400 XP and about +55 silver a minute; standing the hold down once
   Scatter was learned stranded Toxic in Phoenix with 1 silver).
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
    # Back to HIGH (5,000): move on.
    assert leveling_economy.HIGH == 5000
    loop = loop_with(500, stored=4500)
    route, _ = choose(loop, 22)
    assert route.id == "poltergeist" and loop.events == ["economy_hold_ended"]
    # Without a hold, 4,000 is enough to go on.
    loop = loop_with(1000, stored=3000)
    route, _ = choose(loop, 22)
    assert route.id == "poltergeist"


def test_the_hold_spans_the_next_bracket_but_not_farmers_without_the_goal():
    # 3/6: level 31, the top of the WingedSnake bracket, still refills on
    # Poltergeists.
    assert leveling_economy.GRACE_LEVELS == 5
    level_goal.start(level_goal.SCATTER_LEVEL)
    loop = loop_with(10)
    route, _ = choose(loop, 31)
    assert route.id == "poltergeist" and loop.events == ["economy_hold_started"]
    # 4: America farmers keep the bracket rule.
    level_goal.stop()
    loop = loop_with(10)
    route, _ = choose(loop, 22)
    assert route.id == "poltergeist" and loop.events == []


def test_scatter_archer_levels_on_the_dearer_bracket_and_refills(tmp_path, monkeypatch):
    # 6: a learned Scatter no longer stands the hold down.
    from conquest import scatter_training

    monkeypatch.setattr(scatter_training, "STATE", tmp_path / "scatter-training.json")
    write_json(scatter_training.STATE, {"learned_at": 1})
    monkeypatch.setattr(scatter_training, "learned", lambda loop: True)
    level_goal.start(level_goal.SCATTER_LEVEL)
    # Toxic at 19:40: level 28, about 1,500 silver.
    loop = loop_with(1077, stored=423)
    route, _ = choose(loop, 28)
    assert route.id == "poltergeist" and loop.events == ["economy_hold_started"]
    # Refilled: back to the WingedSnakes that level it fastest.
    loop = loop_with(3000, stored=2100)
    route, _ = choose(loop, 28)
    assert route.id == "wingedsnake" and loop.events == ["economy_hold_ended"]


def test_carried_supplies_count_toward_the_wallet(monkeypatch):
    # 19:57: 5,436 silver became 8 arrow packs and potions; silver alone read
    # 1,949 and the hold kept Toxic off the WingedSnakes.
    from conquest import overnight
    from conquest.routes import RouteLibrary

    prices = {1050000: 200, 1000020: 60}
    monkeypatch.setattr(overnight, "last_verified_price", lambda t, path=None: prices.get(t))
    write_json(banking.STATUS, {"stored_silver": 1749})
    items = [
        {"uid": i, "type_id": 1050000, "amount": 200, "limit": 200, "plus": 0, "slot": i}
        for i in range(6)
    ] + [
        {"uid": 100 + i, "type_id": 1000020, "amount": 1, "limit": 1, "plus": 0, "slot": 10 + i}
        for i in range(26)
    ]
    bag = {
        "silver": 200,
        "items": items,
        "equipped_ammo": {"uid": 99, "type_id": 1050000, "amount": 2, "limit": 200},
        "capacity": 40,
    }
    loop = NS(
        town=lambda action, **kw: bag,
        record=lambda event, **fields: None,
        route=RouteLibrary().load("poltergeist"),
    )
    assert leveling_economy.wallet(loop) == 200 + 1749 + 1202 + 26 * 60
    # Unknown prices add nothing (never a guessed value).
    prices.clear()
    assert leveling_economy.wallet(loop) == 200 + 1749


def test_leaving_the_cheaper_bracket_needs_high_but_the_dearer_one_holds_to_low():
    # 2/6: at 3,200 silver (19:40) Toxic on Poltergeists would have gone to
    # the WingedSnakes and been sent straight back below 3,000.
    from conquest.routes import RouteLibrary

    level_goal.start(level_goal.SCATTER_LEVEL)
    loop = loop_with(2800, stored=400)
    loop.route = RouteLibrary().load("poltergeist")
    assert choose(loop, 28)[0].id == "poltergeist"
    loop = loop_with(4700, stored=400)
    loop.route = RouteLibrary().load("poltergeist")
    assert choose(loop, 28)[0].id == "wingedsnake"
    # Already on the WingedSnakes, 3,200 keeps it there.
    loop = loop_with(2800, stored=400)
    loop.route = RouteLibrary().load("wingedsnake")
    assert choose(loop, 28)[0].id == "wingedsnake"


def test_back2classic_archer_past_the_goal_still_holds(monkeypatch):
    # Toxic reached the goal's level 23 at 15:11 and the guard stopped applying.
    level_goal.stop()
    monkeypatch.setattr(level_goal, "back2classic", lambda: True)
    loop = loop_with(134)
    route, _ = choose(loop, 23)
    assert route.id == "apparition" and loop.events == ["economy_hold_started"]
