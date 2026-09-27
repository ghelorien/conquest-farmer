"""HP potion tiers: pick the Pharmacist potion that fits the character's health.

Values come from the installed client's ini/itemtype.json (checked 2026-09-26).
None has a level, class or stat requirement, so the choice depends only on
maximum HP and silver. Every tier heals, so every carried tier counts and is
drunk; a potion is never junk, whichever tier is bought today.
"""

from pathlib import Path

from conquest.character_context import state_path
from conquest.discord_notify import read_json, write_json

# type_id: (name, life restored, client catalog price)
HEALING_POTIONS = {
    1000000: ("Stancher", 70, 5),
    1000010: ("Resolutive", 100, 18),
    1000020: ("Painkiller", 250, 60),
    1000030: ("Amrita", 500, 120),
    1002000: ("Panacea", 800, 240),
    1002010: ("Ginseng", 1200, 360),
    1002020: ("Vanilla", 2000, 600),
}
# The long-standing route potion; used when no tier has been chosen.
DEFAULT_TYPE = 1000020
# One potion should restore at least this share of maximum HP: the gap left
# when healing at 60%, so a single potion refills the bar.
MINIMUM_SHARE = 0.4
TIER = Path(state_path(".runtime/healing-tier.json"))


def life(type_id):
    return HEALING_POTIONS[type_id][1]


def name(type_id):
    return HEALING_POTIONS.get(type_id, (f"potion {type_id}",))[0]


def adaptive():
    """Whether this farmer picks its tier by maximum HP.

    Back2Classic characters level from 1 and outgrow each potion; the level
    goal implies the same. America farmers keep their route potion exactly
    as before.
    """
    from conquest import level_goal

    if level_goal.goal():
        return True
    from conquest.character_context import current
    from conquest.client_attachment import FARMING_ONLY_SERVERS

    context = current()
    return bool(context and context.profile.server in FARMING_ONLY_SERVERS)


def choose(max_hp, silver, count, offered=None, reserve=0):
    """Smallest potion restoring 40% of max HP that `count` of can be bought.

    `offered` maps each tier the Pharmacist sells to its live price (a set
    or None uses the client catalog prices). Falls back to the strongest tier
    the silver covers for `count`, then to the cheapest offered tier, so a
    poor character still leaves with potions.
    """
    if type(max_hp) is not int or max_hp <= 0:
        raise ValueError("Maximum HP must be read from memory before choosing potions")
    if type(count) is not int or count <= 0:
        raise ValueError("Potion count must be positive")
    prices = {
        t: offered[t] if isinstance(offered, dict) else HEALING_POTIONS[t][2]
        for t in HEALING_POTIONS
        if offered is None or t in offered
    }
    tiers = sorted(
        (t for t, price in prices.items() if type(price) is int and price > 0),
        key=life,
    )
    if not tiers:
        raise ValueError("The Pharmacist offers no known HP potion")
    budget = silver - reserve
    affordable = [t for t in tiers if prices[t] * count <= budget]
    fitting = [t for t in affordable if life(t) >= max_hp * MINIMUM_SHARE]
    if fitting:
        return fitting[0]
    if affordable:
        return affordable[-1]
    return min(tiers, key=lambda t: prices[t])


def active_type():
    kind = read_json(TIER).get("type_id")
    return kind if kind in HEALING_POTIONS else DEFAULT_TYPE


def set_active(type_id):
    if type_id not in HEALING_POTIONS:
        raise ValueError("Unknown HP potion")
    if read_json(TIER).get("type_id") != type_id:
        write_json(TIER, {"type_id": type_id})


def usable(type_id, include=None):
    """Any HP potion heals; `include` also admits a configured other item."""
    return type_id in HEALING_POTIONS or (include is not None and type_id == include)


def count(inventory, include=None):
    """Carried HP potions of every tier, from an Inventory result or snapshot.

    A configured healing item that is not an HP potion counts exactly.
    """
    exact = include is not None and include not in HEALING_POTIONS
    if isinstance(inventory, dict):
        items, get = inventory["items"], (lambda i, k: i[k])
    elif hasattr(inventory, "items"):
        items, get = inventory.items, getattr
    else:
        # A count-only reader: the configured (or active) tier alone.
        return inventory.count(include or active_type())
    return sum(
        get(i, "amount")
        for i in items
        if (get(i, "type_id") == include if exact else usable(get(i, "type_id")))
    )


def pick(inventory, missing_hp, include=None):
    """The carried potion that best covers the missing HP.

    Prefer the smallest potion that restores everything missing (so small
    potions are used up first); otherwise the strongest one carried. Returns
    None when no HP potion is carried.
    """
    carried = [
        i
        for i in inventory.items
        if usable(i.type_id) and getattr(i, "amount", 0) > 0
    ]
    if not carried:
        return None
    covering = [i for i in carried if life(i.type_id) >= missing_hp]
    if covering:
        return min(covering, key=lambda i: (life(i.type_id), i.amount))
    return max(carried, key=lambda i: (life(i.type_id), -i.amount))
