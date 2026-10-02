"""Shared valuable policy, mapped to the pinned client definition table.

See profiles/valuable-items.json for read-only live qualification provenance.
Names only fail closed for storage protection; pickup always uses exact type IDs.
"""

import re

DRAGONBALL_NAMES = {
    1088000: "DragonBall",
    720028: "DBScroll",
    2000031: "1-StarDragonBall",
    2000032: "2-StarDragonBall",
    2000033: "3-StarDragonBall",
    2000034: "4-StarDragonBall",
    2000035: "5-StarDragonBall",
    2000036: "6-StarDragonBall",
    2000037: "7-StarDragonBall",
    2000038: "EpicDragonBall",
}
DRAGONBALL_TYPES = frozenset(DRAGONBALL_NAMES)
STORAGE_ONLY_TYPES = frozenset(
    (2000031, 2000032, 2000033, 2000034, 2000035, 2000036, 2000037, 2000038)
)
SPECIAL_LOOT_TYPES = DRAGONBALL_TYPES | {1088001, 720027}
# Gear quality is the type ID's last digit (6 Refined, 7 Unique, 8 Elite,
# 9 Super). Alex 2026-09-28: "You can pickup and bank any item unique and
# higher now."
UNIQUE_AND_HIGHER = frozenset((7, 8, 9))
# Alex 2026-09-29: "from now on only elite or higher items unless they are
# rings, boots, bags, bracelets, necklace", then "I still want all +1's and
# +2's regardless of (unique, elite etc)". On the ground: any +1 to +12 gear;
# unenhanced necklaces (120), bags (121), rings (150), heavy rings (151),
# bracelets (152) and boots (160) from Unique up; everything else from Elite.
ELITE_AND_HIGHER = frozenset((8, 9))
ACCESSORY_FAMILIES = frozenset((120, 121, 150, 151, 152, 160))
URGENT_EQUIPMENT_FAMILIES = frozenset((120, 121, 150, 151, 152, 160, 500))


def loot_gear(kind):
    """Gear type IDs for loot policy: 100000-599999 and the shields (900xxx).

    The client table's 511 shields (SoftShield 900300 up) carry the same
    quality digit (900307 Unique, 900308 Elite, 900309 Super). Merchant and
    warehouse equipment checks keep their own qualified 100000-599999 range.
    """
    return type(kind) is int and (100000 <= kind < 600000 or 900000 <= kind < 901000)


def urgent_storage(item):
    """Carried designated gear and Dragonballs go directly to storage."""
    get = (
        item.get
        if isinstance(item, dict)
        else lambda key, default=None: getattr(item, key, default)
    )
    kind = get("type_id")
    if not (
        get("slot") is not None
        and type(kind) is int
        and (kind in DRAGONBALL_TYPES or kind // 1000 in URGENT_EQUIPMENT_FAMILIES)
    ):
        return False
    if kind in DRAGONBALL_TYPES:
        return True
    # Gear bought to wear stays carried for its equip (equipment.py).
    from conquest.equipment import reserved_gear_uids

    return get("uid") not in reserved_gear_uids()


def storage_only(item):
    get = item.get if isinstance(item, dict) else lambda k, d=None: getattr(item, k, d)
    if get("type_id") in STORAGE_ONLY_TYPES:
        return True
    name = get("name", "")
    return isinstance(name, str) and bool(
        re.fullmatch(r"(?:[1-9][0-9]*[- ]?Star|Epic)DragonBall", name, re.I)
    )


def exact_dragonball(item):
    """An exact memory-qualified Dragonball type ID (never a name-only match)."""
    get = item.get if isinstance(item, dict) else lambda k, d=None: getattr(item, k, d)
    kind = get("type_id")
    return type(kind) is int and kind in DRAGONBALL_TYPES


def require_marketable(item, *, merchant_dragonball=False):
    """Refuse storage-only stock for automatic sale.

    ``storage_only`` stays the farmer/warehouse banking policy. Only the
    guarded 1078 merchant refill passes ``merchant_dragonball=True``: it may
    then sell a Dragonball already held by the merchant, and must itself
    enforce the not-bound, last-refresh and lowest-comparable guards.
    """
    if storage_only(item) and not (merchant_dragonball and exact_dragonball(item)):
        raise ValueError("Rare Dragonball is storage-only; automatic sale is forbidden")
