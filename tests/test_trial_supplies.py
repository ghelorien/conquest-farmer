from dataclasses import replace
from types import SimpleNamespace
import pytest
from conquest.memory_inventory import InventorySnapshot, Item
from conquest.trial import supply_stop_reason


CONFIG = SimpleNamespace(ammo_type=1050000, potion_type=1000000)
POTION = Item(1, 1000000, 1, 1, 0)
AMMO = Item(2, 1050000, 200, 200, None)
SNAPSHOT = InventorySnapshot(10, 10.3, (POTION,), AMMO, 4420, 40)


def test_supplied_character_can_continue():
    assert supply_stop_reason(SNAPSHOT, CONFIG, 10.5) is None


@pytest.mark.parametrize("change,now,reason", [
    ({}, 11.01, "stale_inventory"), ({}, 9.9, "stale_inventory"),
    ({"equipped_ammo": None}, 10.5, "ammo_unavailable"),
    ({"equipped_ammo": replace(AMMO, amount=0)}, 10.5, "ammo_unavailable"),
    ({"equipped_ammo": replace(AMMO, type_id=10)}, 10.5, "ammo_unavailable"),
    ({"items": ()}, 10.5, "potions_exhausted"),
    ({"capacity": 1}, 10.5, "inventory_full"),
])
def test_supply_failures_stop(change, now, reason):
    assert supply_stop_reason(replace(SNAPSHOT, **change), CONFIG, now) == reason
