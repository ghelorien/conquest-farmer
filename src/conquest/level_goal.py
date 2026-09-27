"""Back2Classic: level a fresh archer to a target level (Scatter), then park.

The goal rides on automatic leveling (leveling_routes picks the monster for
the level). On top of it this mode:
- sends the farmer to town every GEAR_STEP levels so the normal restock
  reviews and buys gear upgrades, even when supplies are not yet exhausted;
- picks the Pharmacist potion that fits the character's maximum HP;
- at the target level, learns Scatter in town (scatter_training) and keeps
  leveling with it instead of parking.
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
    return f"Back2Classic: leveling to {data['target_level']}, gear check when a shop tier unlocks"


# (level, reviewed level, city) -> whether a tier upgrade is due; the worn
# gear is read at most once for each.
_tier_checks = {}
GEAR_SLOTS = ("bow", "armor", "ring", "boots", "necklace", "head")


def tier_unlocked(level, reviewed, gear, city, silver):
    """A saved shop tier became usable since the last review, upgrades the worn
    item and fits the silver on hand and banked.

    `gear` returns the build-qualified gear read; it is only called when the
    city's catalog has a tier in (reviewed, level].
    """
    key = (level, reviewed, city)
    if key in _tier_checks:
        return _tier_checks[key]
    from conquest.archer_shop_catalog import catalog
    from conquest.equipment import category, upgrade_reason

    vendors = catalog().get("cities", {}).get(str(city), {})
    stock = [
        p
        for vendor in vendors.values()
        if isinstance(vendor, dict)
        for p in vendor.get("products", [])
        if category(p.get("type_id")) in GEAR_SLOTS
        and type(p.get("level")) is int
        and reviewed < p["level"] <= level
        and type(p.get("price")) is int
        and 0 < p["price"] <= silver
    ]
    result = False
    if stock:
        try:
            state = gear()
        except (ValueError, OSError):
            return False  # Unreadable now; a later hunt tick asks again.
        result = any(upgrade_reason(p, state) is None for p in stock)
    _tier_checks[key] = result
    return result


def due(level, *, gear=None, city=None, silver=None):
    """What the hunt should do at this verified level: None, "gear" or "reached".

    With `gear`, `city` and `silver`, a trip is due as soon as a tier of the
    city's saved shop catalog unlocks that upgrades worn gear (Twin City bows
    unlock at 8, 15, 20 and 25). Every GEAR_STEP levels a trip is due anyway,
    which also covers a city without a saved catalog.
    """
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
    if (
        gear is not None
        and city is not None
        and type(silver) is int
        and tier_unlocked(level, reviewed, gear, city, silver)
    ):
        return "gear"
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
    """At the target level: walk to town, learn Scatter, keep leveling.

    The goal ends in town. Scatter is learned there from ArcherGod
    (scatter_training, memory-proven); when that is not possible yet, a
    later town visit retries. Either way the route carries on with automatic
    leveling instead of parking idle, so this returns False.
    """
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
    from conquest import scatter_training

    learned = scatter_training.attempt(loop)
    data.update(
        active=False,
        reached_level=loop.last_level,
        reached_at=time.time(),
        scatter_learned=learned,
    )
    write_json(GOAL, data)
    loop.record(
        "level_goal_reached",
        level=loop.last_level,
        target_level=data["target_level"],
        scatter_learned=learned,
        activity=(
            f"Level goal complete: level {loop.last_level}; "
            + ("Scatter learned" if learned else "Scatter still to learn")
            + "; leveling on"
        ),
    )
    return False
