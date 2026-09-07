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
