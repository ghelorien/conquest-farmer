"""Unpack one carried MeteorScroll into its ten Meteors (worker side).

MillionaireLee, who packs them (profiles/meteor-banking.json): "Just right
click on it, it will return into 10 meteors or 10 DragonBalls again." The
Magic Artisan takes loose Meteors from the bag (market_artisan).
"""

SCROLL = 720027
METEOR = 1088001
UNPACKED = 10


def receipt(before, item, after):
    """The scroll left the bag, ten single Meteors arrived, nothing else moved."""
    identity = lambda i: (i.type_id, i.amount, i.limit, i.plus)
    old = {i.uid: identity(i) for i in before.items if i.uid != item.uid}
    new = {i.uid: identity(i) for i in after.items}
    added = [uid for uid in new if uid not in old]
    return (
        item.uid not in new
        and all(new.get(uid) == value for uid, value in old.items())
        and len(added) == UNPACKED
        and all(new[uid] == (METEOR, 1, 1, 0) for uid in added)
        and after.silver == before.silver
        and after.equipped_ammo == before.equipped_ammo
    )


def unpack(trade, uid):
    from conquest.discard_loot import inventory_button

    before = trade.inventory.read()
    matches = [
        i for i in before.items if i.uid == uid and i.type_id == SCROLL and i.amount == 1
    ]
    if len(matches) != 1:
        raise ValueError("One carried MeteorScroll is required")
    item = matches[0]
    if item.slot is None or not 0 <= item.slot < 40:
        raise ValueError("MeteorScroll slot is invalid")
    if before.capacity - len(before.items) < UNPACKED - 1:
        raise ValueError("Unpacking a MeteorScroll needs nine free bag slots")
    for name in ("Shop", "Warehouse"):
        try:
            trade.shop.gui.read(name)
        except ValueError as error:
            if "not active" not in str(error) and "absent" not in str(error):
                raise
        else:
            raise ValueError("Close town panels before unpacking a MeteorScroll")
    try:
        trade.shop.gui.read("Inventory")
    except ValueError as error:
        if "not active" not in str(error) and "absent" not in str(error):
            raise
        trade.input_attempted = True
        trade.click(inventory_button(trade.shop.gui))
        trade.verified_read(
            lambda: trade.shop.gui.read("Inventory"),
            bool,
            "Inventory opening unverified",
        )
    grid = trade.shop.gui.read("Inventory/##ItemGrid_")
    if grid.size != (407.0, 175.0) or grid.scroll != (0.0, 0.0):
        raise ValueError("MeteorScroll inventory grid differs")
    fresh = trade.inventory.read()
    if (
        fresh.items != before.items
        or fresh.silver != before.silver
        or fresh.equipped_ammo != before.equipped_ammo
        or trade.shop.gui.read("Inventory/##ItemGrid_") != grid
    ):
        raise ValueError("Inventory changed before unpacking")
    point = (
        round(grid.position[0] + 20 + 40 * (item.slot % 10)),
        round(grid.position[1] + 20 + 40 * (item.slot // 10)),
    )
    trade.input_attempted = True
    trade.click(point, "right")
    after = trade.verified_read(
        trade.inventory.read,
        lambda after: receipt(before, item, after),
        "MeteorScroll unpacking unverified; no repeat input issued",
        timeout=5,
    )
    return {
        "unpacked": item.uid,
        "meteors": after.count(METEOR),
        "added": sorted(
            i.uid for i in after.items if i.uid not in {j.uid for j in before.items}
        ),
    }
