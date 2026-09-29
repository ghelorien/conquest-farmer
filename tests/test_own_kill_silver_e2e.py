"""Pick up silver only from the monsters this farmer kills.

Alex, 2026-09-27: "You're spending too much time picking up silver on the
ground, figure out a way to only pickup the silver you get from the monsters
you kill." The looter walked to every silver drop within 40 tiles, other
players' kills included, and learned ownership only from the client's
rejection message after walking there.

A ground item's creation time is the client's own GetTickCount64 stamp
(actor +0x48; live on Laptop2 it matched Windows uptime: silver 3.4 s and
4.1 s old). A silver drop is ours when it was created from 3 s before to
1.5 s after one of our verified kills (the player's kill counter rose) and
within 5 tiles of the monster we were attacking; Scatter kills that
monster's neighbours too.

Failure modes, written before the code:
1. Silver from another player's kill (no kill of ours near it) is walked to.
2. Our kill's silver is skipped because it appeared just before the kill
   counter confirmed the kill.
3. Silver of a monster Scatter killed beside the aimed target is skipped.
4. An old drop lying at our kill site is taken for ours.
5. Silver created well after our kill, next to its site, is taken for ours.
6. Valuable items (Meteors, + gear) stop being picked up: silver-only rule.
7. Remembered kill sites pile up without bound.
8. A kill with no remembered target tile loses its silver.
9. The target walked at the archer while it shot and died far from where it
   was targeted, so our own silver is judged someone else's (live Toxic
   2026-09-27: 0.96 silver pickups per kill before this rule, 0.2-0.3
   after, and the bank ran down to 94 silver).
"""

import json
from types import SimpleNamespace

from conquest.memory_ground import GroundItem
from conquest.vision import Target
from test_native_farm import setup

KILL = 5_000_000  # client tick (ms) when our kill was verified
SILVER, METEOR = 1090010, 1088001


def farmer(monkeypatch, clock):
    supervisor, _, life, _ = setup(monkeypatch)
    life.dead_candidate = False
    supervisor.observer.adapter = SimpleNamespace(viewport_size=lambda: (1420, 1009))
    monkeypatch.setattr("conquest.scene_input.memory_player_anchor", lambda *a: (950, 600))
    monkeypatch.setattr("conquest.level_goal.collect_silver", lambda: True)
    monkeypatch.setattr("conquest.memory_ground.client_tick_ms", lambda: clock[0])
    return supervisor


def kill(supervisor, clock, tile):
    supervisor.last_target = (
        Target("Apparition", 500, 400, 1.0, 7, 70, tile) if tile else None
    )
    supervisor.position = (10, 10)
    supervisor.finish_target("kill_counter_increased")


def picks(supervisor, drops):
    clicks = []
    supervisor.pending_loot = None
    supervisor.loot_wait_until = 0
    supervisor.ground_items = lambda: tuple(drops)
    supervisor.loot_step(
        SimpleNamespace(silver=0, items=(), capacity=40),
        (10, 10),
        lambda point, **kw: clicks.append(kw.get("drop") or point),
    )
    return [c.uid for c in clicks if isinstance(c, GroundItem)] or clicks


def test_own_kill_silver_e2e(monkeypatch, tmp_path):
    clock = [KILL]
    supervisor = farmer(monkeypatch, clock)
    rows = {}
    # 1: another player's silver, and no kill of ours at all.
    rows["no_kill_of_ours"] = picks(supervisor, [GroundItem(1, 10, SILVER, (12, 10), KILL - 500)])
    kill(supervisor, clock, (11, 10))
    # 2: created 0.4 s before the counter confirmed our kill, beside it.
    rows["own_kill_before_counter"] = picks(
        supervisor, [GroundItem(2, 20, SILVER, (12, 10), KILL - 400)]
    )
    # 3: a Scatter neighbour 4 tiles from the aimed target, 0.3 s after.
    rows["scatter_neighbour"] = picks(
        supervisor, [GroundItem(3, 30, SILVER, (15, 12), KILL + 300)]
    )
    # 4: an old drop lying at our kill site.
    rows["old_drop_at_site"] = picks(
        supervisor, [GroundItem(4, 40, SILVER, (11, 11), KILL - 20_000)]
    )
    # 5: created 5 s after our kill, next to its site.
    rows["later_drop_at_site"] = picks(
        supervisor, [GroundItem(5, 50, SILVER, (11, 11), KILL + 5_000)]
    )
    # 1: someone else's fresh kill far from ours.
    rows["other_players_kill"] = picks(
        supervisor, [GroundItem(6, 60, SILVER, (20, 18), KILL + 100)]
    )
    # 6: valuables keep their rule, whoever killed the monster.
    rows["meteor_anywhere"] = picks(
        # Created a minute before our kill, within VALUABLE_CLICK_TILES.
        supervisor, [GroundItem(7, 70, METEOR, (13, 13), KILL - 60_000)]
    )
    # 8: a kill with no target tile falls back to the farmer's own area.
    clock[0] = KILL + 30_000
    kill(supervisor, clock, None)
    rows["kill_without_target_tile"] = picks(
        supervisor, [GroundItem(8, 80, SILVER, (16, 13), clock[0] - 200)]
    )
    # 7: sites older than the keep window are forgotten.
    clock[0] = KILL + 300_000
    kill(supervisor, clock, (40, 40))
    rows["remembered_sites"] = len(supervisor.kill_sites)
    (tmp_path / "own-kill-silver.json").write_text(
        json.dumps(rows, indent=2, sort_keys=True), encoding="utf-8"
    )
    assert rows == {
        "no_kill_of_ours": [],
        "own_kill_before_counter": [2],
        "scatter_neighbour": [3],
        "old_drop_at_site": [],
        "later_drop_at_site": [],
        "other_players_kill": [],
        "meteor_anywhere": [7],
        "kill_without_target_tile": [8],
        "remembered_sites": 1,
    }


def test_silver_where_the_target_died_after_walking_at_us(monkeypatch):
    # 9
    clock = [KILL]
    supervisor = farmer(monkeypatch, clock)
    supervisor.last_target = Target("Apparition", 500, 400, 1.0, 7, 70, (22, 10))
    # The scene saw it walk from 12 tiles away up to 2 tiles before it died.
    supervisor.note_target_position(
        [SimpleNamespace(entity_id=7, object_address=70, position=(12, 10))]
    )
    supervisor.position = (10, 10)
    supervisor.finish_target("kill_counter_increased")
    assert picks(supervisor, [GroundItem(9, 90, SILVER, (12, 11), KILL + 200)]) == [9]
    # A sighting older than 3 s falls back to the targeting tile.
    clock[0] = KILL + 60_000
    supervisor.last_target = Target("Apparition", 500, 400, 1.0, 8, 80, (22, 10))
    supervisor.note_target_position(
        [SimpleNamespace(entity_id=8, object_address=80, position=(12, 10))]
    )
    clock[0] = KILL + 65_000
    supervisor.finish_target("kill_counter_increased")
    assert picks(supervisor, [GroundItem(10, 100, SILVER, (12, 11), clock[0] - 100)]) == []
    # Another monster's sighting never stands in for our target.
    supervisor.last_target = Target("Apparition", 500, 400, 1.0, 9, 90, (22, 10))
    supervisor.note_target_position(
        [SimpleNamespace(entity_id=3, object_address=30, position=(12, 10))]
    )
    supervisor.finish_target("kill_counter_increased")
    assert picks(supervisor, [GroundItem(11, 110, SILVER, (12, 11), clock[0] - 100)]) == []


def test_unconfirmed_own_silver_is_retried_within_its_window(monkeypatch):
    """11: an escape jump cut the pickup walk short (live 2026-09-27 18:52);
    the former 60 s cooldown outlived the drop's own-kill window."""
    from conquest import native_farm

    clock = [KILL]
    now = [100.0]
    monkeypatch.setattr(native_farm.time, "monotonic", lambda: now[0])
    supervisor = farmer(monkeypatch, clock)
    kill(supervisor, clock, (11, 10))
    drop = GroundItem(15, 150, SILVER, (12, 10), KILL + 200)
    assert picks(supervisor, [drop]) == [15]
    # Still on the ground 1.5 s later: unconfirmed, retried after 5 s.
    now[0] = 101.6
    supervisor.loot_step(
        SimpleNamespace(silver=0, items=(), capacity=40), (10, 10), lambda *a, **k: None
    )
    assert supervisor.pending_loot is None
    assert supervisor.loot_cooldowns[(15, 150)] == 101.6 + native_farm.SILVER_RETRY_SECONDS
    now[0] = 101.6 + native_farm.SILVER_RETRY_SECONDS + 0.1
    assert picks(supervisor, [drop]) == [15]


def test_scatter_kill_silver_anywhere_within_its_reach(monkeypatch):
    """10: Scatter killed a monster in its fan, not the one it aimed at (live
    Toxic 2026-09-27 17:57-18:00: 17 jump-Scatter kills, not one pickup)."""
    from conquest import native_farm
    from conquest.scatter_movement import remember_scatter

    clock = [KILL]
    now = [100.0]
    monkeypatch.setattr(native_farm.time, "monotonic", lambda: now[0])
    monkeypatch.setattr("conquest.scatter_movement.time.monotonic", lambda: now[0])
    supervisor = farmer(monkeypatch, clock)
    supervisor.position = (10, 10)
    remember_scatter(supervisor, [])
    supervisor.position = (20, 10)  # jumped away before the kill registered
    now[0] = 101.5
    supervisor.last_target = Target("Poltergeist", 500, 400, 1.0, 7, 70, (30, 10))
    supervisor.finish_target("kill_counter_increased")
    # In the fan, 7 tiles from the cast and far from the aimed monster: ours.
    assert picks(supervisor, [GroundItem(12, 120, SILVER, (16, 16), KILL + 200)]) == [12]
    # Beyond Scatter reach of the cast: someone else's.
    assert picks(supervisor, [GroundItem(13, 130, SILVER, (10, 21), KILL + 200)]) == []
    # A kill long after the last cast was not Scatter's.
    now[0] = 110.0
    clock[0] = KILL + 60_000
    supervisor.last_target = Target("Poltergeist", 500, 400, 1.0, 8, 80, (30, 10))
    supervisor.finish_target("kill_counter_increased")
    assert picks(supervisor, [GroundItem(14, 140, SILVER, (16, 16), clock[0] - 100)]) == []
