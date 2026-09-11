from types import SimpleNamespace as NS
from dataclasses import replace
import pytest
from conquest.equipment import upgrade_candidate,choose_upgrades,equip_receipt
from conquest.memory_inventory import Item

def state(level=30):
    return {'level':level,'profession':41,'equipment':{'bow':{'uid':1,'type_id':500035,'level':25,
        'plus':0,'attack_min':92,'attack_max':114,'gem1':0,'gem2':0}}}

def bow(**updates):
    p={'type_id':500045,'name':'HardBow','level':30,'profession':40,'sex':0,'price':4000,
       'attack_min':117,'attack_max':143,'defense':0,'dodge':0}
    p.update(updates);return p

def test_level_30_gets_level_30_bow_but_not_35():
    s=state()
    assert choose_upgrades([bow(),bow(type_id=500055,level=35)],s,10000)==[bow()]
    assert not choose_upgrades([bow()],state(29),10000)

@pytest.mark.parametrize('change',[{'plus':1},{'plus':None},{'type_id':500037},{'gem1':1},{'gem2':1}])
def test_protected_equipped_gear_is_not_replaced_by_plain_shop_gear(change):
    s=state();s['equipment']['bow'].update(change)
    assert not upgrade_candidate(bow(),s)

def test_no_duplicate_tier_or_stat_downgrade_and_preserve_supply_money():
    assert not upgrade_candidate(bow(level=25),state())
    assert not upgrade_candidate(bow(attack_min=80),state())
    assert not choose_upgrades([bow()],state(),6999)
    assert choose_upgrades([bow()],state(),7000)==[bow()]

def test_equipping_keeps_old_item_and_all_unrelated_inventory():
    new=Item(2,500045,100,100,0,0);keep=Item(3,1088001,1,1,1,0)
    before=NS(items=(new,keep),silver=9000)
    old=Item(1,500035,100,100,0,0)
    after=NS(items=(old,keep),silver=9000)
    newstate=state();newstate['equipment']['bow']={'uid':2}
    assert equip_receipt(2,'bow',before,state(),after,newstate)
    assert not equip_receipt(2,'bow',before,state(),NS(items=(keep,),silver=9000),newstate)
    assert not equip_receipt(2,'bow',before,state(),NS(items=(old,keep),silver=8999),newstate)


def test_every_slot_reviewed_and_headgear_is_in_armorer_review():
    from conquest.equipment import SLOTS,VENDORS,review_slots
    assert set().union(*map(set,VENDORS.values()))==set(SLOTS)
    head=bow(type_id=113413,name='CatHat',level=37,attack_min=0,attack_max=0,defense=20)
    s=state(37)
    assert head in choose_upgrades([head],s,10000)
    report=review_slots([bow(),head],s,10000)
    assert set(report)==set(SLOTS)
    assert report['head']['upgrades']==['CatHat']
    assert report['armor']['reasons']==['This shop does not stock this slot']
    assert 'HardBow' in report['bow']['upgrades']
    assert 'Keeping silver for supplies' in review_slots([bow()],s,6999)['bow']['reasons']
    s['equipment']['bow']['plus']=1
    assert 'Keeping protected or unverified equipped gear' in review_slots([bow()],s,10000)['bow']['reasons']


def test_empty_arrow_stack_may_disappear_but_live_stack_must_return_to_bag():
    new=Item(2,1050001,1000,1000,0,0)
    old=Item(1,1050001,0,1000,None,0)
    before=NS(items=(new,),equipped_ammo=old,silver=9000)
    after=NS(items=(),equipped_ammo=new,silver=9000)
    gear={'equipment':{'arrows':{'uid':1,'type_id':1050001}}}
    equipped={'equipment':{'arrows':{'uid':2,'type_id':1050001}}}
    assert equip_receipt(2,'arrows',before,gear,after,equipped)
    before.equipped_ammo=replace(old,amount=836)
    assert not equip_receipt(2,'arrows',before,gear,after,equipped)
    after.items=(before.equipped_ammo,)
    assert equip_receipt(2,'arrows',before,gear,after,equipped)
