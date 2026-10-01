"""Leaving a player killer at once (Back2Classic allows PK).

Suicide died at (450, 347) in the Phoenix Bandit box on 2026-09-30 23:18:
100-200 a hit, then ~820 in under a second, from nothing the farmer targets
or flees. At 02:09 laptop2-ops/hit_logger.py named the hitters, players
"Andria" and "Sesshomaru", with no monster within 15 tiles; the heavy-damage
return left 21 s later.

Failure modes, written before the change:
1. A big hit with a player near and no monster close does not leave the field.
2. Monster hits (a Bandit takes 1-3% of 969 HP; a boss within 15 tiles, or
   a monster near the farmer's level within 9) or small drops trigger a
   flight, or the scene is scanned for players on every check. Harmless
   Bandits beside the farmer hide a PK (they stood at 1-4 tiles on a third
   of the hits).
3. The flight waits for a quiet spot (a player hits through it) or skips the
   ordinary restock afterwards.
"""

from types import SimpleNamespace as NS

import pytest

from conquest import overnight


def loop_with(monkeypatch, players):
    from conquest import player_scan

    scans = []

    def scan(session, position, reach):
        scans.append((position, reach))
        return players

    monkeypatch.setattr(player_scan, "players_near", scan)
    loop = overnight.OvernightLoop.__new__(overnight.OvernightLoop)
    loop.care = NS(session="session")
    return loop, scans


def life(hp, position=(367, 442)):
    return {"current_hp": hp, "max_hp": 969, "position": list(position)}


ANDRIA = [("Andria", 1175985, (359, 436), 8)]


def test_a_big_hit_with_a_player_near_and_no_monster_close_is_a_pk(monkeypatch):
    # 1
    loop, scans = loop_with(monkeypatch, ANDRIA)
    assert loop.player_attack(life(900), {"monsters": []}) == []  # first look
    assert loop.player_attack(life(738), {"monsters": []}) == ANDRIA  # -162
    assert scans == [((367, 442), overnight.PK_PLAYER_TILES)]


@pytest.mark.parametrize(
    "before, after, monsters, players",
    [
        (900, 880, [], ANDRIA),  # a Bandit-sized hit (2%)
        (900, 738, [{"position": [369, 443], "alive": None}], ANDRIA),  # melee
        (900, 738, [], []),  # nobody near: not a player
    ],
)
def test_monster_hits_small_drops_and_empty_scenes_stay(monkeypatch, before, after, monsters, players):
    # 2
    loop, scans = loop_with(monkeypatch, players)
    loop.player_attack(life(before), {"monsters": monsters})
    assert loop.player_attack(life(after), {"monsters": monsters}) == []
    # Small drops and melee monsters never cost a scene scan.
    assert len(scans) == (1 if after < before * 0.9 and not monsters else 0)


def bandit(distance, name="Bandit", level=32):
    return {"name": name, "level": level, "position": [367 + distance, 442], "alive": True}


def test_harmless_monsters_round_the_farmer_do_not_hide_a_pk(monkeypatch):
    # 2026-10-01 02:09:44: 161 HP with a Bandit at 1 tile and a BanditL33 at
    # 8, Andria at 14. Monsters 20+ levels below explain 3% a hit at contact.
    loop, scans = loop_with(monkeypatch, ANDRIA)
    loop.last_level = 61
    scene = {"monsters": [bandit(1), bandit(3), bandit(8, "BanditL33", 33)]}
    loop.player_attack(life(900), scene)
    assert loop.player_attack(life(739), scene) == ANDRIA
    # Four at contact explain a 120 HP drop.
    crowd = {"monsters": [bandit(1)] * 4}
    loop.player_attack(life(900), crowd)
    assert loop.player_attack(life(780), crowd) == [] and len(scans) == 1


@pytest.mark.parametrize(
    "monster, level",
    [
        (bandit(12, "BanditMessenger", 40), 61),  # bosses and elites hit from range
        (bandit(6, "ThunderApe", 57), 61),  # a monster near the farmer's level
        (bandit(6), 0),  # level unknown: every monster can hit
    ],
)
def test_a_monster_that_could_deal_the_hit_is_no_pk(monkeypatch, monster, level):
    loop, scans = loop_with(monkeypatch, ANDRIA)
    loop.last_level = level
    loop.player_attack(life(900), {"monsters": [monster]})
    assert loop.player_attack(life(738), {"monsters": [monster]}) == [] and scans == []


def test_the_flight_reads_the_gate_at_once_then_restocks(monkeypatch):
    # 3
    from conquest import return_scroll

    loop, _ = loop_with(monkeypatch, ANDRIA)
    gates, events, stops = [], [], []
    monkeypatch.setattr(return_scroll, "read_gate", lambda loop, home: gates.append(home) or True)
    loop.route = NS(restock_map_id=1011)
    loop.record = lambda event, **fields: events.append((event, fields.get("reason")))
    loop.stop_farm = lambda: stops.append(1)
    loop.quiet_for_gate = lambda: pytest.fail("a player hits through any quiet-spot wait")
    loop.flee_player(ANDRIA, {"potions": 12})
    assert gates == [1011] and stops == [1] and loop.phase == "restocking"
    assert events == [("player_attack", None), ("return_required", "player_attack")]
