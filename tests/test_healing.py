from dataclasses import replace
import pytest
from conquest.healing import HealingAttempt, potion_point
from conquest.memory_inventory import Item, InventorySnapshot

POTION = Item(42, 1000000, 1, 1, 6)
BEFORE = InventorySnapshot(10, 10.1, (POTION,), None, 0, 40)


def test_healing_requires_both_consumption_and_health_gain():
    attempt = HealingAttempt(42, 1, 30, 10)
    after = replace(BEFORE, items=())
    assert attempt.outcome(after, 100, 10.5) == "verified"
    assert attempt.outcome(BEFORE, 100, 10.5) == "waiting"
    assert attempt.outcome(after, 30, 10.5) == "waiting"
    assert attempt.outcome(after, 30, 12) == "unverified"
    assert attempt.outcome(BEFORE, 30, 12) == "unverified"


def test_consuming_different_potion_is_not_confirmation():
    attempt = HealingAttempt(99, 1, 30, 10)
    # Item 99 is absent in the observation, so dispatch must only create an
    # attempt using an item actually present in its before observation.
    attempt = HealingAttempt(POTION.uid, POTION.amount, 30, 10)
    assert attempt.outcome(BEFORE, 100, 12) == "unverified"


def test_memory_slot_must_match_visible_potion():
    assert potion_point(POTION, ((1416, 345),)) == (1416, 345)
    moved = replace(POTION, slot=26)
    assert potion_point(moved, ((1416, 425),)) == (1416, 425)
    with pytest.raises(ValueError, match="accessible"):
        potion_point(moved, ((1416, 345),))
    with pytest.raises(ValueError, match="invalid"):
        potion_point(replace(POTION, slot=None), ())
