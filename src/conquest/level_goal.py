"""Back2Classic: level a fresh archer to a target level (Scatter), then park.

The goal rides on automatic leveling (leveling_routes picks the monster for
the level). On top of it this mode:
- sends the farmer to town every GEAR_STEP levels so the normal restock
  reviews and buys gear upgrades, even when supplies are not yet exhausted;
- picks the Pharmacist potion that fits the character's maximum HP;
- stops in town at the target level and reports it.
"""

import time
from pathlib import Path

from conquest.character_context import state_path
from conquest.discord_notify import read_json, write_json

GOAL = Path(state_path(".runtime/level-goal.json"))
# Scatter is learned at level 23 on retail-style Classic servers; the target is
# stored with the goal so the UI can change it if the server differs.
SCATTER_LEVEL = 23
GEAR_STEP = 5
# Combat heal threshold while the goal runs (share of max HP).
HEAL_BELOW = 0.6
# Jump away from monsters when this share of max HP was lost within the
# last 1.25 s; smaller hits keep the attack going. Being surrounded by two
# or more adjacent monsters still triggers a jump on its own.
ESCAPE_DAMAGE_SHARE = 0.1


def goal():
    data = read_json(GOAL)
    return data if data.get("active") else None


def back2classic():
    """Whether the selected character plays on Back2Classic (farming only)."""
    from conquest.character_context import current
    from conquest.client_attachment import FARMING_ONLY_SERVERS

    context = current()
    return bool(context and context.profile.server in FARMING_ONLY_SERVERS)


_silver_cache = (-float("inf"), False)


def collect_silver():
    """Pick up dropped silver while the goal runs or on Back2Classic, where a
    character funds itself from drops at every level (checked every 2 s)."""
    global _silver_cache
    now = time.monotonic()
    if now - _silver_cache[0] >= 2:
        _silver_cache = (now, bool(goal()) or back2classic())
    return _silver_cache[1]


def start(target_level=SCATTER_LEVEL):
    if type(target_level) is not int or not 2 <= target_level <= 140:
        raise ValueError("Target level must be between 2 and 140")
    from conquest import session_plan

    if session_plan.active_plan():
        # A route hold or savings plan would pin one route; leveling needs
        # the automatic level brackets.
        session_plan.resume_leveling()
    write_json(
        GOAL,
        {
            "active": True,
            "target_level": target_level,
            "started_at": time.time(),
            "reviewed_level": None,
        },
    )


def stop():
    data = read_json(GOAL)
    if data.get("active"):
        data.update(active=False, ended_at=time.time())
        write_json(GOAL, data)


def note():
    data = goal()
    if not data:
        return None
    return f"Back2Classic: leveling to {data['target_level']}, gear check every {GEAR_STEP} levels"


def due(level):
    """What the hunt should do at this verified level: None, "gear" or "reached"."""
    data = goal()
    if not data or type(level) is not int or level <= 0:
        return None
    if level >= data["target_level"]:
        return "reached"
    reviewed = data.get("reviewed_level")
    if reviewed is None:
        # Start counting from the first verified level; starter gear is fine.
        mark_reviewed(level)
        return None
    if level >= reviewed + GEAR_STEP:
        return "gear"
    return None


def mark_reviewed(level):
    """Every restock reviews gear; remember the level it happened at."""
    data = goal()
    if not data or type(level) is not int or level <= 0:
        return
    if data.get("reviewed_level") != level:
        data["reviewed_level"] = level
        write_json(GOAL, data)


def healing_type(loop):
    """Choose and activate the potion tier while the Pharmacist is open.

    Back2Classic farmers (and the level goal) pick by maximum HP and the
    Pharmacist's live prices: a fixed Painkiller can restore more than a
    fresh character's whole bar. America farmers keep their route potion.
    Every carried tier stays usable, so a new tier never sells or strands
    potions, and an unreadable shop keeps the current tier.
    """
    from conquest import potion_tiers

    current = loop.route.supplies.healing_type
    if not potion_tiers.adaptive():
        return current
    try:
        products = (loop.town("shop", vendor_type=3) or {}).get("products") or []
        life = loop.living()["embedded_controls"]["life"]
        silver = loop.town("supplies")["silver"]
        kind = potion_tiers.choose(
            life["max_hp"],
            silver,
            loop.route.supplies.healing_restock_to,
            offered={
                p["type_id"]: p.get("price")
                for p in products
                if p.get("type_id") in potion_tiers.HEALING_POTIONS
            },
        )
    except (ValueError, KeyError, TypeError) as error:
        loop.record(
            "healing_tier_unchanged",
            healing_type=current,
            detail=str(error),
            activity=f"Keeping {potion_tiers.name(current)}: potion prices unavailable",
        )
        return current
    if kind != loop.route.supplies.healing_type:
        loop.route = loop.route.model_copy(
            update={
                "supplies": loop.route.supplies.model_copy(
                    update={"healing_type": kind}
                )
            }
        )
    potion_tiers.set_active(kind)
    loop.record(
        "healing_tier_selected",
        healing_type=kind,
        max_hp=life["max_hp"],
        silver=silver,
        activity=f"Buying {potion_tiers.name(kind)} for {life['max_hp']} max HP",
    )
    return kind


def finish_in_town(loop):
    """Park in the restock town at the target level; the route then ends."""
    data = goal()
    if not data:
        return False
    from conquest.city_travel import city_for
    from conquest.return_scroll import return_to_town
    from conquest.world_travel import travel_to_map

    loop.phase = "restocking"
    loop.record(
        "level_goal_return",
        level=loop.last_level,
        activity=f"Level {loop.last_level} reached; returning to town to park",
    )
    return_to_town(loop)
    travel_to_map(loop, loop.route.restock_map_id)
    loop.travel(loop.route.restock_anchor)
    life = loop.living()["embedded_controls"]["life"]
    left, top, right, bottom = city_for(loop.route.restock_map_id)["town_boundary"]
    x, y = life["position"]
    if (
        life["map_id"] != loop.route.restock_map_id
        or life["dead_candidate"]
        or not (left <= x <= right and top <= y <= bottom)
    ):
        raise ValueError("Level goal completion requires a living character in town")
    data.update(active=False, reached_level=loop.last_level, reached_at=time.time())
    write_json(GOAL, data)
    loop.phase = "completed"
    loop.record(
        "level_goal_reached",
        level=loop.last_level,
        target_level=data["target_level"],
        activity=(
            f"Level goal complete: level {loop.last_level}; parked in town. "
            "Learn Scatter at the Archer trainer."
        ),
    )
    return True
