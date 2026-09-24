"""Verify arrow equipment changes using observed item identities."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ReloadAttempt:
    previous_uid: int | None
    reserve: tuple[tuple[int, int], ...]
    issued_at: float

    def outcome(self, inventory, ammo_type, now, timeout=2):
        ammo = inventory.equipped_ammo
        reserves = dict(self.reserve)
        if (
            ammo is not None
            and ammo.type_id == ammo_type
            and ammo.uid != self.previous_uid
            and ammo.uid in reserves
            and 0 < ammo.amount <= reserves[ammo.uid]
            and all(item.uid != ammo.uid for item in inventory.items)
        ):
            return "verified"
        return "waiting" if 0 <= now - self.issued_at < timeout else "unverified"
