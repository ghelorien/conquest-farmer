"""Leveling mode keeps the cautious escape rules; farming mode keeps shooting.

Alex 2026-09-28 22:05: a leveling mode "more scared of dying", and a farming
mode where "high value item > killing > minimizing damage taken > exp per
hour". On FireSpirits the leveling rules made ~18 escape jumps a minute,
mostly from one monster two or three tiles off that had not hit.

Failure modes, written before the change:
1. With no file (or a bad one) the farmer is not in leveling mode.
2. Farming still jumps at a 2% hit, or leveling stops jumping at one.
3. Farming ignores a big hit or a hit taken at low HP.
4. Farming still jumps from one monster within JUMP_SCATTER_REACH, or
   leveling jump-Scatter stops doing so.
5. Farming does not drink earlier than leveling.
6. A boss near still has to make both modes jump (ranged_escape unchanged).

Alex 2026-10-01 05:5x, of farming's three-at-contact trigger: "Now you're
jumping a bit too late ... you just need a very tiny buffer when they get
melee range then you jump back and keep attacking", and "When you are flying
you can be super bold and go in huge packs as you cannot be attacked by
melee monsters". Failure modes, written before that change:
7. Farming waits for monsters at contact, or jumps for one three tiles off.
8. While flying an ordinary crowd still calls for a jump, or a boss no
   longer does.
"""

from types import SimpleNamespace as NS
import json
import time

import pytest

from conquest import farm_mode
from conquest.native_farm import ESCAPE_DAMAGE_SHARE, JUMP_SCATTER_REACH, NativeFarmSupervisor


@pytest.fixture
def mode_file(tmp_path, monkeypatch):
    path = tmp_path / "farm-mode.json"
    monkeypatch.setattr(farm_mode, "PATH", path)
    monkeypatch.setattr(farm_mode, "_cache", (-float("inf"), "leveling"))
    return path


def test_leveling_is_the_default_and_bad_values_fall_back(mode_file):
    # 1
    assert farm_mode.mode() == "leveling"
    mode_file.write_text(json.dumps({"mode": "berserk"}), encoding="utf-8")
    farm_mode._cache = (-float("inf"), "leveling")
    assert farm_mode.mode() == "leveling"
    with pytest.raises(ValueError, match="Unknown farm mode"):
        farm_mode.set_mode("berserk")


def test_set_mode_is_saved_and_read_back(mode_file):
    farm_mode.set_mode("farming")
    assert json.loads(mode_file.read_text(encoding="utf-8"))["mode"] == "farming"
    assert farm_mode.farming()
    farm_mode.set_mode("leveling")
    assert not farm_mode.farming()


def supervisor():
    s = NativeFarmSupervisor.__new__(NativeFarmSupervisor)
    s.last_health_position = None
    s.last_damage_at = -float("inf")
    s.defend_until = 0
    return s


def hit(s, before, after, max_hp=681):
    s.last_health_position = None
    s.last_damage_at = -float("inf")
    s.note_health(NS(position=(10, 10), current_hp=before, max_hp=max_hp, dead_candidate=False))
    s.note_health(NS(position=(11, 10), current_hp=after, max_hp=max_hp, dead_candidate=False))
    return s.last_damage_at > -float("inf")


def test_a_small_hit_is_a_jump_only_while_leveling(mode_file):
    # 2
    s = supervisor()
    small = round(0.02 * 681)
    assert hit(s, 681, 681 - small)
    farm_mode.set_mode("farming")
    assert not hit(s, 681, 681 - small)


def test_farming_still_jumps_for_a_big_hit_or_a_hit_at_low_hp(mode_file):
    # 3
    farm_mode.set_mode("farming")
    s = supervisor()
    assert hit(s, 681, 681 - round(0.12 * 681))
    assert hit(s, 360, 350)  # 51% after the hit: under FARM_JUMP_HP
    assert not hit(s, 681, 670)


def test_escape_triggers_follow_the_mode(mode_file):
    # 4, 7, 8
    assert farm_mode.escape_trigger(True, False, JUMP_SCATTER_REACH) == (1, JUMP_SCATTER_REACH)
    assert farm_mode.escape_trigger(True, True, JUMP_SCATTER_REACH) == (1, 1)
    assert farm_mode.escape_trigger(False, False, JUMP_SCATTER_REACH) == (2, 1)
    fly = farm_mode.escape_trigger(True, False, JUMP_SCATTER_REACH, flying=True)
    assert fly == (farm_mode.FLY_TRIGGER, 1)
    farm_mode.set_mode("farming")
    assert farm_mode.FARM_REACH == 2 < JUMP_SCATTER_REACH
    assert farm_mode.escape_trigger(True, False, JUMP_SCATTER_REACH) == (1, farm_mode.FARM_REACH)
    # A pickup walk is cut short only by a monster at contact.
    assert farm_mode.escape_trigger(True, True, JUMP_SCATTER_REACH) == (1, 1)
    assert farm_mode.escape_trigger(True, False, JUMP_SCATTER_REACH, flying=True) == fly


def test_farming_drinks_from_sixty_percent(mode_file):
    # 5
    assert farm_mode.heal_threshold(0.4) == 0.4
    farm_mode.set_mode("farming")
    assert farm_mode.heal_threshold(0.4) == farm_mode.FARM_HEAL_BELOW
    assert farm_mode.heal_threshold(0.75) == 0.75  # an approach tops up higher


def test_the_apps_mode_picker_saves_the_mode(mode_file):
    from conquest.desktop_app import DesktopApp

    app = DesktopApp.__new__(DesktopApp)
    notes = []
    app.memory_text = NS(set=notes.append)
    app.farm_mode_labels = {"leveling": "Leveling · careful", "farming": "Farming · max kills"}
    app.farm_mode_text = NS(get=lambda: "Farming · max kills")
    app.select_farm_mode()
    assert farm_mode.farming() and "maximum kills" in notes[-1]
    app.farm_mode_text = NS(get=lambda: "Leveling · careful")
    app.select_farm_mode()
    assert not farm_mode.farming()


def escape_supervisor(monsters):
    s = NativeFarmSupervisor.__new__(NativeFarmSupervisor)
    s.escape_ready_at = 0
    s.scene_timestamp = time.monotonic()
    s.escape_monsters = monsters
    s.last_damage_at = -float("inf")
    s.escape_damage_consumed_at = -float("inf")
    return s


def jumps(s, trigger, reach, monkeypatch, **kwargs):
    """Whether the real ranged_escape gate lets this scene through to the
    landing search (None means no jump was called for)."""

    def landings_reached(*args, **kwargs):
        raise RuntimeError("landing search reached")

    monkeypatch.setattr("conquest.navigation.native_movement_delta", landings_reached)
    s.recovery = NS(terrain=NS(walkable=lambda tile: True))
    s.observer = None
    try:
        assert (
            s.ranged_escape(
                (10, 10), (0, 0, 40, 40), adjacent_trigger=trigger, reach=reach, **kwargs
            )
            is None
        )
    except RuntimeError as error:
        assert str(error) == "landing search reached"
        return True
    return False


def test_a_monster_far_below_the_archer_is_no_reason_to_jump(mode_file, monkeypatch):
    # Alex 2026-10-01 06:3x sent Toxic (L62) to the Bandits (L32-36): "goal is
    # minimum of 100 kpm". Every jump from a Bandit is a lost cast.
    from conquest.native_farm import HARMLESS_LEVELS

    farm_mode.set_mode("farming")
    trigger, reach = farm_mode.escape_trigger(True, False, JUMP_SCATTER_REACH)
    harmless = 62 - HARMLESS_LEVELS
    bandits = [NS(name="Bandit", level=33, position=(11, 10)), NS(name="Bandit", level=34, position=(10, 11))]
    assert not jumps(escape_supervisor(bandits), trigger, reach, monkeypatch, harmless_level=harmless)
    # Without the character's level, as before: they call for the jump.
    assert jumps(escape_supervisor(bandits), trigger, reach, monkeypatch)
    # A monster within 20 levels still does, and a boss of any level does.
    snake = [NS(name="Snakeman", level=62, position=(11, 10))]
    assert jumps(escape_supervisor(snake), trigger, reach, monkeypatch, harmless_level=harmless)
    king = [NS(name="BanditKing", level=40, position=(14, 10))]
    assert jumps(escape_supervisor(king), trigger, reach, monkeypatch, harmless_level=harmless)
    # A hit worth a jump still is one.
    hit = escape_supervisor(bandits)
    hit.last_damage_at = time.monotonic()
    assert jumps(hit, trigger, reach, monkeypatch, harmless_level=harmless)


def test_farming_jumps_a_tile_short_of_melee(mode_file, monkeypatch):
    # 4, 7, against the real ranged_escape gate
    farm_mode.set_mode("farming")
    trigger, reach = farm_mode.escape_trigger(True, False, JUMP_SCATTER_REACH)
    three_off = escape_supervisor([NS(name="Snakeman", position=(13, 10))])
    assert not jumps(three_off, trigger, reach, monkeypatch)
    two_off = escape_supervisor([NS(name="Snakeman", position=(12, 10))])
    assert jumps(two_off, trigger, reach, monkeypatch)


def test_a_boss_near_still_needs_a_jump_in_farming_mode(mode_file, monkeypatch):
    # 6: the gate lets the boss through to the landing search.
    s = escape_supervisor([NS(name="WingedSnakeKing", position=(14, 10))])
    farm_mode.set_mode("farming")
    trigger, reach = farm_mode.escape_trigger(True, False, JUMP_SCATTER_REACH)
    assert jumps(s, trigger, reach, monkeypatch)


@pytest.mark.parametrize("mode", farm_mode.MODES)
def test_flying_ignores_an_ordinary_crowd_but_not_a_boss(mode_file, monkeypatch, mode):
    # 8
    farm_mode.set_mode(mode)
    trigger, reach = farm_mode.escape_trigger(True, False, JUMP_SCATTER_REACH, flying=True)
    crowd = [NS(name="Snakeman", position=(10 + dx, 10 + dy)) for dx in (-1, 0, 1) for dy in (-1, 1)]
    assert not jumps(escape_supervisor(crowd), trigger, reach, monkeypatch)
    king = NS(name="SnakemanKing", position=(14, 10))
    assert jumps(escape_supervisor(crowd + [king]), trigger, reach, monkeypatch)
