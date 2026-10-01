"""Normal archer ammunition selection from live equipment and shop records."""

from pathlib import Path

from conquest.character_context import state_path

NORMAL_ARROWS = {1050000: "LuckyArrow", 1050001: "IronArrow", 1050002: "SpeedArrow"}
# Per character {"tier_cap": 1050000, "reason": "..."}: the best normal tier
# a leveling archer buys, whatever its wallet. 2026-10-01 12:3x: Suicide (62)
# one-shots Apparitions (303 HP) far past a LuckyArrow's 40 attack less, yet
# IronArrows (4.8 silver an arrow against 1) burned ~17k an hour from a 26k
# bank with no income there; the next restock would have left ~2k and turned
# on every silver pile, which cut kills to ~55/min on Bandits.
POLICY = Path(state_path(".runtime/arrow-policy.json"))
ARROW_LEVELS = {1050000: 1, 1050001: 32, 1050002: 73}
# Latest preference: one equipped pack and one spare, across all normal tiers.
MAX_ARROW_PACKS = 2
ARROW_REFILL_AMOUNTS = {1050000: 400, 1050001: 2000, 1050002: 10000}
# A stack this small (four Scatter casts at most) is a remnant, not a pack.
# An equipped remnant never counts against the pack limit. A route hunts
# until ammo_unavailable, so it walks home on one: Suicide's 2-arrow quiver
# (and Toxic's 1-arrow one) filled the "equipped" slot of the two, and both
# left town with one pack instead of the two planned (2026-09-28 10:09 and
# 10:24). A bag remnant is sold instead (overnight.blocking_remnant).
REMNANT_ARROWS = 9
# A leveling archer on 200-arrow LuckyArrow packs emptied two packs in about
# 18 minutes (live 2026-09-27), so every town trip was for arrows. The
# two-pack preference was set for 5,000-arrow SpeedArrow packs. At 15
# kills/min (level 20, 50 arrows/min) five packs lasted ~20 minutes while
# the potions lasted ~37: eight packs (~32 minutes) balance the two. At 100+
# kills/min (Suicide, 62, ~65 arrows/min) eight lasted ~25 minutes against a
# ~4.5-minute Phoenix round trip from Twin City's fields; sixteen last ~50
# (supply_plan.plan still fits them to the bag, the potions and the wallet).
LEVELING_LUCKY_PACKS = 16
# Leveling archers on 1,000-arrow IronArrow packs shoot ~62-64 arrows a
# minute on FireSpirits (Toxic and Suicide, level 41, 2026-09-28). Two packs
# lasted 17-31 minutes against a ~5-minute town trip (~1,050 tiles each way
# from Phoenix). Five last ~75 minutes, and supply_plan.plan still fits them
# to the bag and the wallet.
LEVELING_IRON_PACKS = 5


def max_arrow_packs(kind=None):
    """Packs of this normal tier the farmer may carry, the equipped one included."""
    if kind in (1050000, 1050001):
        from conquest.equipment import leveling_archer

        if leveling_archer():
            return LEVELING_LUCKY_PACKS if kind == 1050000 else LEVELING_IRON_PACKS
    return MAX_ARROW_PACKS


def equipped_remnant(kind):
    """Arrows at or under which an equipped quiver is a remnant, not a pack.

    It takes no bag slot, so for a leveling archer's LuckyArrows and
    IronArrows only a real partial pack (over a quarter of the tier's pack)
    counts against the pack limit. Live 2026-09-28: a 120-arrow IronArrow
    quiver (Suicide 17:55) and a 58-arrow one (Toxic 17:51) each filled one of
    two 1,000-arrow pack slots, so each left town with one new pack instead of
    two. A partial 5,000-arrow SpeedArrow pack, or an America farmer's quiver,
    keeps REMNANT_ARROWS.
    """
    if kind in (1050000, 1050001):
        from conquest.equipment import leveling_archer

        if leveling_archer():
            return max(REMNANT_ARROWS, ARROW_REFILL_AMOUNTS[kind] // MAX_ARROW_PACKS // 4)
    return REMNANT_ARROWS


def tier_cap():
    """The capped normal tier from POLICY, or None."""
    from conquest.discord_notify import read_json

    cap = read_json(POLICY).get("tier_cap")
    return cap if cap in ARROW_LEVELS else None


def within_cap(kind):
    cap = tier_cap()
    return cap is None or ARROW_LEVELS.get(kind, 0) <= ARROW_LEVELS[cap]


def preferred_arrow(level):
    return max(
        (
            kind
            for kind, required in ARROW_LEVELS.items()
            if required <= level and within_cap(kind)
        ),
        key=ARROW_LEVELS.get,
        default=1050000,
    )


# A leveling archer moves to a dearer tier while its wallet holds this many
# packs of it. At level 32 IronArrows (4,800 per 1,000) nearly doubled
# Toxic's damage per Scatter on Bandits but cost ~260 silver a minute against
# ~135 picked up (2026-09-27 21:34-21:45); five packs were required for a
# while, then Alex (22:36): "Use iron arrows from now on." One affordable
# pack is enough; a wallet that cannot pay for one refills the next lower tier.
LEVELING_TIER_PACKS = 1


def arrow_pack_price(kind):
    """A pack's price from the recorded Blacksmith catalog, or None."""
    from conquest.archer_shop_catalog import catalog

    prices = [
        p["price"]
        for city in (catalog().get("cities") or {}).values()
        if isinstance(city, dict)
        for p in (city.get("5") or {}).get("products") or []
        if p.get("type_id") == kind and type(p.get("price")) is int and p["price"] > 0
    ]
    return min(prices) if prices else None


def leveling_tier(level, wallet):
    """The best level-eligible normal tier the wallet sustains
    (LEVELING_TIER_PACKS of its packs); LuckyArrow always."""
    best = 1050000
    for kind, required in ARROW_LEVELS.items():
        price = arrow_pack_price(kind)
        if (
            required <= level
            and within_cap(kind)
            and price
            and price * LEVELING_TIER_PACKS <= wallet
            and required > ARROW_LEVELS[best]
        ):
            best = kind
    return best


def counted_tiers(kind=None):
    """The normal tiers whose packs count against buying ``kind``.

    A leveling archer's lower-tier stacks are its fallback once the better
    tier runs out, not a reason to keep buying the lower one. Live 2026-09-27
    22:54 (Suicide, level 32): it came in for potions with 415 LuckyArrows,
    the two-pack cap turned its IronArrow upgrade away and the refill topped
    the LuckyArrows back up to eight packs, so IronArrows never came.
    """
    if kind in ARROW_LEVELS:
        from conquest.equipment import leveling_archer

        if leveling_archer():
            return {k for k in NORMAL_ARROWS if ARROW_LEVELS[k] >= ARROW_LEVELS[kind]}
    return set(NORMAL_ARROWS)


def arrow_pack_count(snapshot, kind=None):
    """Count physical arrow packs, including partial packs and equipped ammo
    above equipped_remnant (with ``kind``, only the tiers that count against
    buying it)."""
    if not isinstance(snapshot, dict):
        from dataclasses import asdict

        snapshot = asdict(snapshot)
    tiers = counted_tiers(kind)
    items = [
        i
        for i in snapshot["items"]
        if i["type_id"] in tiers and i["amount"] > 0
    ]
    ammo = snapshot.get("equipped_ammo")
    equipped = bool(
        ammo
        and ammo["type_id"] in tiers
        and ammo["amount"] > equipped_remnant(ammo["type_id"])
        and (
            ammo.get("uid") is None
            or not any(i.get("uid") == ammo["uid"] for i in items)
        )
    )
    return len(items) + int(equipped)


def refill_target(kind):
    """Arrows a restock refills this tier to: its pack size times its pack limit."""
    return ARROW_REFILL_AMOUNTS[kind] // MAX_ARROW_PACKS * max_arrow_packs(kind)


def require_arrow_purchase_room(snapshot, kind=None):
    if arrow_pack_count(snapshot, kind) >= max_arrow_packs(kind):
        raise ValueError("Arrow purchase blocked: already carrying the maximum packs")


def eligible_arrow(product, state):
    get = (
        product.get
        if isinstance(product, dict)
        else lambda key, default=None: getattr(product, key, default)
    )
    return (
        get("type_id") in NORMAL_ARROWS
        and 1 <= get("level", 0) <= state["level"]
        and get("profession", 0) in (0, 40, 41)
    )


def current_arrow(state, default=None, reserves=(), *, equipped_ammo=None):
    """Choose a Scatter-usable tier from freshly observed carried ammunition.

    Equipment metadata identifies the arrow tier but does not contain its live
    remaining count. Only a matching inventory observation can qualify the
    equipped stack; an empty SpeedArrow must not hide a usable IronArrow pack.

    With nothing usable, the refill buys the best tier eligible for the level,
    not a route's saved tier (live 2026-09-25: a level-95 SpeedArrow remnant
    fell back to the saved IronArrow). An explicit default is only for savings
    mode, whose configured tier is the one it is allowed to buy.
    """
    item = state["equipment"].get("arrows")
    usable = [
        kind
        for kind in reserves
        if kind in ARROW_LEVELS and ARROW_LEVELS[kind] <= state["level"]
    ]
    if equipped_ammo is not None:
        observed = (
            equipped_ammo
            if isinstance(equipped_ammo, dict)
            else {
                key: getattr(equipped_ammo, key, None)
                for key in ("uid", "type_id", "amount")
            }
        )
        if (
            item
            and eligible_arrow(item, state)
            and observed.get("uid") == item.get("uid")
            and observed.get("type_id") == item["type_id"]
            and (observed.get("amount") or 0) >= 3
        ):
            usable.append(item["type_id"])
    if usable:
        return max(usable, key=ARROW_LEVELS.get)
    return preferred_arrow(state["level"]) if default is None else default


def fallback_arrow(products, level, kind, silver):
    """Next lower level-eligible normal tier the wallet can pay for, or None.

    Prices come only from the live shop reader. A required refill that cannot
    pay for the selected tier buys the best affordable lower tier instead
    (SpeedArrow -> IronArrow -> LuckyArrow) rather than stranding in town.
    """
    lower = sorted(
        (
            p
            for p in products
            if p["type_id"] in NORMAL_ARROWS
            and ARROW_LEVELS[p["type_id"]] < ARROW_LEVELS.get(kind, 0)
            and ARROW_LEVELS[p["type_id"]] <= level
            and 1 <= p.get("level", 0) <= level
            and 0 < p.get("price", 0) <= silver
        ),
        key=lambda p: ARROW_LEVELS[p["type_id"]],
        reverse=True,
    )
    return lower[0] if lower else None


def choose_arrow_upgrade(products, state, silver, reserve=3000, *, carried=()):
    old = state["equipment"].get("arrows", {})
    candidates = [
        p
        for p in products
        if eligible_arrow(p, state)
        and (p["type_id"] in carried or 0 < p["price"] <= silver - reserve)
        and p["attack_min"] >= old.get("attack_min", 0)
        and p["attack_max"] >= old.get("attack_max", 0)
        and p["attack_min"] + p["attack_max"]
        > old.get("attack_min", 0) + old.get("attack_max", 0)
    ]
    return max(
        candidates,
        key=lambda p: (p["attack_min"] + p["attack_max"], -p["price"]),
        default=None,
    )


def review_arrows(loop, products, state, silver):
    bag = loop.town("supplies")
    owned = {i["type_id"] for i in bag["items"] if i["amount"] >= 3}
    from conquest.equipment import leveling_archer

    if leveling_archer():
        from conquest.banking import STATUS
        from conquest.discord_notify import read_json

        cap = leveling_tier(
            state["level"], silver + read_json(STATUS).get("stored_silver", 0)
        )
        products = [
            p
            for p in products
            if p.get("type_id") not in ARROW_LEVELS
            or ARROW_LEVELS[p["type_id"]] <= ARROW_LEVELS[cap]
        ]
    product = choose_arrow_upgrade(products, state, silver, carried=owned)
    if product:
        carried = [
            i
            for i in bag["items"]
            if i["type_id"] == product["type_id"] and i["amount"] >= 3
        ]
        if carried:
            uid = max(carried, key=lambda i: i["amount"])["uid"]
        else:
            if arrow_pack_count(bag, product["type_id"]) >= MAX_ARROW_PACKS:
                loop.record(
                    "arrow_upgrade_deferred",
                    activity="Using existing ammunition; two-pack purchase cap reached",
                )
                if hasattr(loop, "adopt_ammunition"):
                    loop.adopt_ammunition(state)
                return state
            loop.record(
                "arrow_upgrade_buying", activity=f"Buying {product['name']} ammunition"
            )
            before = {i["uid"] for i in bag["items"]}
            loop.town("buy", vendor_type=5, type_id=product["type_id"])
            fresh = loop.town("supplies")
            matches = [
                i
                for i in fresh["items"]
                if i["type_id"] == product["type_id"] and i["uid"] not in before
            ]
            if len(matches) != 1:
                raise ValueError("Purchased arrow stack identity is uncertain")
            uid = matches[0]["uid"]
        loop.town("close", window="Shop")
        try:
            receipt = loop.town("equip-arrows", uid=uid)
        finally:
            loop.town("close", window="Inventory")
        loop.record(
            "arrows_upgraded",
            receipt=receipt,
            activity=f"Using {product['name']} ammunition",
        )
        loop.town("open", vendor_type=5)
        state = loop.town("gear")
    if hasattr(loop, "adopt_ammunition"):
        loop.adopt_ammunition(state)
    return state
