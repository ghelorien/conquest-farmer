"""Leveling or farming: what the combat loop puts first.

Alex 2026-09-28 22:05: "There should be a leveling mode where you are more
scared of dying etc. Now since we are a lot stronger there should be a
farming mode where your highest priority is maximum kills per hour." And:
"When you are in farming mode it should be high value item > killing >
minimizing damage taken > exp per hour."

Leveling (the default) keeps every escape rule written while the farmers were
weak: jump at any hit over 1% of max HP (native_farm.ESCAPE_DAMAGE_SHARE) and
before any monster is within JUMP_SCATTER_REACH. On FireSpirits with the new
gear those rules made ~18 jumps a minute, mostly from one monster two or three
tiles off that had not hit (Suicide, 2026-09-28 17:59-18:01), each a lost cast.

Farming keeps shooting through ordinary contact. It still jumps for a boss (a
death is the worst loss of kills), for a hit of FARM_JUMP_DAMAGE_SHARE of max
HP or more, for any hit below FARM_JUMP_HP, and when FARM_SURROUNDED monsters
stand at contact, and it heals from FARM_HEAL_BELOW. Valuable drops come first
in both modes (NativeFarmSupervisor.combat_loot_step); the route, and so the
experience, is the user's pick. The one lower-priority walk left is silver
while a Back2Classic bank is under level_goal.SILVER_FLOOR, which only exists
so an empty wallet cannot stall the farmer in town.

Tanking costs potions: turn farming on only with a bank that pays for them.
"""

import time
from pathlib import Path

from conquest.character_context import state_path
from conquest.discord_notify import read_json, write_json

PATH = Path(state_path(".runtime/farm-mode.json"))
MODES = ("leveling", "farming")
FARM_JUMP_DAMAGE_SHARE = 0.1
FARM_JUMP_HP = 0.55
FARM_SURROUNDED = 3
FARM_HEAL_BELOW = 0.6
# The combat loop asks every observation; the file is re-read this often.
CACHE_SECONDS = 2
_cache = (-float("inf"), "leveling")


def mode():
    """The saved mode, "leveling" unless farming was chosen."""
    global _cache
    now = time.monotonic()
    if now - _cache[0] >= CACHE_SECONDS:
        value = read_json(PATH).get("mode")
        _cache = (now, value if value in MODES else "leveling")
    return _cache[1]


def farming():
    return mode() == "farming"


def set_mode(value):
    global _cache
    if value not in MODES:
        raise ValueError(f"Unknown farm mode {value!r}; use leveling or farming")
    write_json(PATH, {"mode": value, "set_at": time.time()})
    _cache = (-float("inf"), value)


def jump_worthy_hit(lost_hp, current_hp, max_hp, leveling_share):
    """Whether a hit calls for an escape jump in the current mode."""
    if not max_hp or lost_hp <= 0:
        return False
    if farming():
        return lost_hp >= FARM_JUMP_DAMAGE_SHARE * max_hp or current_hp < FARM_JUMP_HP * max_hp
    return lost_hp > leveling_share * max_hp


def escape_trigger(jump_scatter, looting, jump_scatter_reach):
    """(adjacent_trigger, reach) for NativeFarmSupervisor.ranged_escape.

    Leveling jump-Scatter leaves before any monster can hit (Alex
    2026-09-27: "When doing jump scatter you can't let enemies ever attack
    you"); farming leaves only when FARM_SURROUNDED monsters are at contact.
    """
    if farming():
        return FARM_SURROUNDED, 1
    return (
        1 if jump_scatter else 2,
        jump_scatter_reach if jump_scatter and not looting else 1,
    )


def heal_threshold(threshold):
    """Farming takes more hits, so it drinks from FARM_HEAL_BELOW."""
    return max(threshold, FARM_HEAL_BELOW) if farming() else threshold
