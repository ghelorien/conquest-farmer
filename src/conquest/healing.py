"""Evidence-based potion selection and verification, without input or OCR."""

from dataclasses import dataclass


@dataclass(frozen=True)
class HealingAttempt:
    uid: int
    amount_before: int
    health_before: float
    issued_at: float
    potion_type: int | None = None
    total_before: int | None = None

    def outcome(self, inventory, health, now, timeout=2):
        item = next((item for item in inventory.items if item.uid == self.uid), None)
        consumed = item is None or item.amount < self.amount_before
        if self.potion_type is not None and self.total_before is not None:
            consumed = inventory.count(self.potion_type) < self.total_before
        if consumed and health > self.health_before:
            return "verified"
        return "waiting" if 0 <= now - self.issued_at < timeout else "unverified"


def potion_point(item, visible_points):
    """Memory supplies the current slot; the calibrated panel must agree."""
    if item.slot is None or not 0 <= item.slot < 40 or item.amount <= 0:
        raise ValueError("Potion slot is invalid")
    point = (1176 + (item.slot % 10) * 40, 345 + (item.slot // 10) * 40)
    if point not in visible_points:
        raise ValueError("Potion's memory slot is not visibly accessible")
    return point


def consume_inventory_potion(trade, uid):
    """Use one memory-identified Painkiller without assuming an F1 binding."""
    from conquest.discard_loot import inventory_button

    life = trade.life(any_map=True)
    before = trade.inventory.read()
    matches = [
        i
        for i in before.items
        if i.uid == uid and i.type_id == 1000020 and i.amount > 0
    ]
    if len(matches) != 1:
        raise ValueError("Selected healing item is not a carried Painkiller")
    item = matches[0]
    if item.slot is None or not 0 <= item.slot < 40:
        raise ValueError("Healing item slot is invalid")
    if life.current_hp >= life.max_hp:
        return {"consumed": False, "reason": "already_full_health"}
    try:
        trade.shop.gui.read("Shop")
    except ValueError as error:
        if "not active" not in str(error) and "absent" not in str(error):
            raise
    else:
        raise ValueError("Close shop before using a healing item")
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
        raise ValueError("Healing inventory grid differs")
    from conquest.town_trade import TownObservationUnavailable, transient_observation

    try:
        fresh = trade.life(any_map=True)
        if (
            fresh.object_address != life.object_address
            or trade.inventory.read().items != before.items
            or trade.shop.gui.read("Inventory/##ItemGrid_") != grid
        ):
            raise TownObservationUnavailable(
                "Healing item or character changed before input"
            )
    except ValueError as error:
        if transient_observation(error):
            raise TownObservationUnavailable(str(error)) from error
        raise
    if fresh.current_hp >= fresh.max_hp:
        return {"consumed": False, "reason": "already_full_health"}
    point = (
        round(grid.position[0] + 20 + 40 * (item.slot % 10)),
        round(grid.position[1] + 20 + 40 * (item.slot // 10)),
    )
    trade.input_attempted = True
    trade.click(point, "right")
    after, healed = trade.verified_read(
        lambda: (trade.inventory.read(), trade.life(any_map=True)),
        lambda pair: (
            pair[0].count(1000020) == before.count(1000020) - 1
            and pair[1].object_address == life.object_address
            and pair[1].current_hp > fresh.current_hp
        ),
        "Healing consumption unverified; no repeat input issued",
    )
    return {
        "consumed": True,
        "uid": uid,
        "type_id": 1000020,
        "hp_before": fresh.current_hp,
        "hp_after": healed.current_hp,
        "remaining": after.count(1000020),
    }
