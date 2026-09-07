from dataclasses import replace
from types import SimpleNamespace

import pytest

from conquest.memory_inventory import Item, InventorySnapshot
from conquest.reloading import ReloadAttempt
from conquest.trial import supply_stop_reason


RESERVE = Item(2,1050000,200,200,0)
EMPTY = Item(1,1050000,0,200,None)
POTION = Item(3,1000000,1,1,1)
BEFORE = InventorySnapshot(10,10,(RESERVE,POTION),EMPTY,0,40)
CONFIG = SimpleNamespace(ammo_type=1050000,potion_type=1000000,ammo_key=113)


def test_empty_ammo_can_reload_only_with_calibrated_binding_and_reserve():
    assert supply_stop_reason(BEFORE,CONFIG,10) is None
    assert supply_stop_reason(replace(BEFORE,equipped_ammo=None),CONFIG,10) is None
    assert supply_stop_reason(replace(BEFORE,items=(POTION,)),CONFIG,10) == "ammo_unavailable"
    assert supply_stop_reason(BEFORE,SimpleNamespace(ammo_type=1050000,potion_type=1000000),10) == "ammo_unavailable"


def test_reload_requires_new_equipped_identity_from_observed_reserves():
    attempt = ReloadAttempt(1,((2,200),),10)
    assert attempt.outcome(BEFORE,1050000,10.5) == "waiting"
    assert attempt.outcome(BEFORE,1050000,12) == "unverified"
    after = replace(BEFORE,items=(POTION,),equipped_ammo=replace(RESERVE,slot=None,amount=198))
    assert attempt.outcome(after,1050000,10.5) == "verified"
    for uid, amount, type_id in [(1,200,1050000),(9,200,1050000),(2,0,1050000),(2,200,1000000)]:
        wrong = replace(after,equipped_ammo=Item(uid,type_id,amount,200,None))
        assert attempt.outcome(wrong,1050000,12) == "unverified"
    assert attempt.outcome(replace(after,items=(RESERVE,POTION)),1050000,12) == "unverified"
