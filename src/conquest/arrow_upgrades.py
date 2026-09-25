"""Normal archer ammunition selection from live equipment and shop records."""

NORMAL_ARROWS = {1050000: "LuckyArrow", 1050001: "IronArrow", 1050002: "SpeedArrow"}
ARROW_LEVELS = {1050000: 1, 1050001: 32, 1050002: 73}
# Latest preference: one equipped pack and one spare, across all normal tiers.
MAX_ARROW_PACKS = 2
ARROW_REFILL_AMOUNTS = {1050000: 400, 1050001: 2000, 1050002: 10000}


def preferred_arrow(level):
    return max(
        (kind for kind, required in ARROW_LEVELS.items() if required <= level),
        key=ARROW_LEVELS.get,
        default=1050000,
    )


def _snapshot(snapshot):
    if isinstance(snapshot, dict):
        return snapshot
    from dataclasses import asdict

    return asdict(snapshot)


def usable_arrow_tiers(snapshot, level):
    """Level-eligible normal tiers carried in a Scatter-usable (>= 3) stack."""
    snapshot = _snapshot(snapshot)
    stacks = list(snapshot["items"])
    if snapshot.get("equipped_ammo"):
        stacks.append(snapshot["equipped_ammo"])
    return {
        s["type_id"]
        for s in stacks
        if s["type_id"] in NORMAL_ARROWS
        and s["amount"] >= 3
        and ARROW_LEVELS[s["type_id"]] <= level
    }


def lower_tier_in_use(snapshot, level):
    """Return the usable tier being used up before the level-best tier is bought.

    Owned usable packs are spent first. While the best usable tier is below the
    best tier for the level, no arrow is bought: neither a lower-tier spare nor
    an early upgrade. Returns None when a best-tier pack is usable or nothing
    usable is carried (a required refill then buys the level-best tier).
    """
    usable = usable_arrow_tiers(snapshot, level)
    if not usable:
        return None
    best = max(usable, key=ARROW_LEVELS.get)
    return best if ARROW_LEVELS[best] < ARROW_LEVELS[preferred_arrow(level)] else None


def arrow_pack_count(snapshot):
    """Count physical arrow packs, including partial packs and equipped ammo."""
    snapshot = _snapshot(snapshot)
    items = [
        i
        for i in snapshot["items"]
        if i["type_id"] in NORMAL_ARROWS and i["amount"] > 0
    ]
    ammo = snapshot.get("equipped_ammo")
    equipped = bool(
        ammo
        and ammo["type_id"] in NORMAL_ARROWS
        and ammo["amount"] > 0
        and (
            ammo.get("uid") is None
            or not any(i.get("uid") == ammo["uid"] for i in items)
        )
    )
    return len(items) + int(equipped)


def require_arrow_purchase_room(snapshot):
    if arrow_pack_count(snapshot) >= MAX_ARROW_PACKS:
        raise ValueError("Arrow purchase blocked: already carrying two or more packs")


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
            if arrow_pack_count(bag) >= MAX_ARROW_PACKS:
                loop.record(
                    "arrow_upgrade_deferred",
                    activity="Using existing ammunition; two-pack purchase cap reached",
                )
                if hasattr(loop, "adopt_ammunition"):
                    loop.adopt_ammunition(state)
                return state
            held = lower_tier_in_use(bag, state["level"])
            if held:
                loop.record(
                    "arrow_upgrade_deferred",
                    activity=f"Using remaining {NORMAL_ARROWS[held]} before buying {product['name']}",
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
