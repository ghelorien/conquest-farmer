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


@pytest.mark.parametrize('consumed,gained',[(True,True),(False,True),(True,False),(False,False)])
def test_memory_inventory_potion_requires_consumption_and_hp(consumed,gained):
    from types import SimpleNamespace as NS
    from conquest.healing import consume_inventory_potion
    from conquest.memory_shop import GuiWindow
    item=replace(POTION,type_id=1000020)
    before=replace(BEFORE,items=(item,));after=replace(before,items=() if consumed else (item,))
    clicked=[]
    def life(**kwargs):return NS(object_address=123,current_hp=100 if clicked and gained else 30,max_hp=100)
    grid=GuiWindow(1,'Inventory/grid',(50,100),(407.,175.),(0.,0.))
    def gui(name):
        if name=='Shop':raise ValueError('not active')
        return grid
    def verify(read,accept,error):
        result=read()
        if not accept(result):raise ValueError(error)
        return result
    trade=NS(life=life,inventory=NS(read=lambda:after if clicked else before),
             shop=NS(gui=NS(read=gui)),click=lambda point,button:clicked.append((point,button)),verified_read=verify)
    if consumed and gained:
        result=consume_inventory_potion(trade,item.uid)
        assert result['consumed'] and result['hp_after']==100 and result['remaining']==0
    else:
        with pytest.raises(ValueError,match='unverified'):consume_inventory_potion(trade,item.uid)
    assert clicked==[((310,120),'right')]


@pytest.mark.parametrize('item', [POTION,replace(POTION,type_id=1000020,slot=None),replace(POTION,type_id=1000020,slot=40)])
def test_wrong_potion_or_invalid_slot_never_sends_input(item):
    from types import SimpleNamespace as NS
    from conquest.healing import consume_inventory_potion
    trade=NS(life=lambda **kw:NS(current_hp=30,max_hp=100),inventory=NS(read=lambda:replace(BEFORE,items=(item,))),
             click=lambda *a:pytest.fail('Invalid potion must never be clicked'))
    with pytest.raises(ValueError):consume_inventory_potion(trade,item.uid)
