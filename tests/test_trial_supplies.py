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


@pytest.mark.parametrize('equipped,reserve,expected,reload',[
    (0,0,'ammo_unavailable',True),(1,0,'ammo_unavailable',True),
    (2,0,'ammo_unavailable',True),(3,0,None,False),
    (1,1000,None,True),(2,3,None,True),(2,2,'ammo_unavailable',True)])
def test_scatter_needs_three_in_one_stack_or_reloads(equipped,reserve,expected,reload):
    from conquest.trial import ammunition_reload_needed
    config=SimpleNamespace(ammo_type=1050000,potion_type=1000000,ammo_key=113,attack_button='right',jump_scatter=True)
    items=(POTION,)+( (replace(AMMO,uid=3,amount=reserve,slot=1),) if reserve else () )
    bag=replace(SNAPSHOT,items=items,equipped_ammo=replace(AMMO,amount=equipped))
    assert supply_stop_reason(bag,config,10.5)==expected
    assert ammunition_reload_needed(bag,config)==reload


def test_single_attack_can_use_last_arrow():
    assert supply_stop_reason(replace(SNAPSHOT,equipped_ammo=replace(AMMO,amount=1)),CONFIG,10.5) is None


@pytest.mark.parametrize('remaining',[3,23,24,25])
def test_patrol_does_not_create_partly_used_arrow_packs(remaining):
    from conquest.trial import ammunition_reload_needed
    config=SimpleNamespace(ammo_type=1050000,attack_button='right',jump_scatter=True)
    bag=replace(SNAPSHOT,items=(replace(AMMO,uid=3,slot=0),),equipped_ammo=replace(AMMO,amount=remaining))
    assert not ammunition_reload_needed(bag,config,proactive=True)


def test_better_selected_reserve_reloads_instead_of_stopping_on_old_equipped_tier():
    from conquest.trial import ammunition_reload_needed
    config=SimpleNamespace(ammo_type=1050002,potion_type=1000000,ammo_key=113,
                           attack_button='right',jump_scatter=True)
    speed=replace(AMMO,uid=3,type_id=1050002,amount=5000,limit=5000,slot=1)
    bag=replace(SNAPSHOT,items=(POTION,speed))
    assert supply_stop_reason(bag,config,10.5) is None
    assert ammunition_reload_needed(bag,config)
    assert supply_stop_reason(replace(bag,items=(POTION,)),config,10.5)=='ammo_unavailable'
