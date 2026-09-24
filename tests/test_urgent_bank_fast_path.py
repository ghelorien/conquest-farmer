from copy import deepcopy
from types import SimpleNamespace as NS

import pytest

from conquest import banking as b
from conquest.town_trade import sale_candidate,stash_candidate


def item(uid,kind=500003,plus=0):
    return {'uid':uid,'type_id':kind,'plus':plus,'slot':uid-1,'amount':1,'limit':1}


@pytest.mark.parametrize('family',[120,121,150,152,160,500])
@pytest.mark.parametrize('plus',[None,0,1,2])
def test_every_carried_urgent_family_can_be_stored_and_cannot_be_sold(family,plus):
    value=item(1,family*1000+3,plus)
    assert b.urgent_valuables([value])==[value]
    assert stash_candidate(value)
    assert not sale_candidate(value)
    assert not b.urgent_valuables([{**value,'slot':None}])
    assert not stash_candidate({**value,'slot':None})


@pytest.fixture
def bank(monkeypatch):
    carried=[item(1,530003,2),item(2,1088001),item(3),item(4,1088000)]
    stored=[];calls=[]
    state={'capacity':4,'uncertain':False}
    def town(action,**fields):
        if action=='supplies':return {'items':deepcopy(carried)}
        if action=='warehouse-items':return {'items':deepcopy(stored),'capacity':state['capacity']}
        assert action=='warehouse-deposit'
        uid=fields['uid'];calls.append(uid)
        value=next(value for value in carried if value['uid']==uid)
        if state['uncertain']:return {'stored':uid,'type_id':value['type_id']}
        carried.remove(value);stored.append(value)
        return {'stored':uid,'type_id':value['type_id'],'verified_in_warehouse':True}
    return NS(town=town,record=lambda *args,**kw:None),carried,stored,calls,state


def test_urgent_items_precede_meteors_and_other_loot_remains_queued(bank):
    loop,carried,stored,calls,state=bank
    assert b.stash_urgent_valuables(loop)
    assert calls==[3,4,2]
    assert [value['uid'] for value in carried]==[1]


def test_partial_capacity_preserves_verified_urgent_deposits_before_fallback(bank):
    loop,carried,stored,calls,state=bank
    state['capacity']=1
    assert b.stash_urgent_valuables(loop) is False
    assert calls==[3]
    assert [value['uid'] for value in stored]==[3]
    assert {value['uid'] for value in carried}=={1,2,4}


def test_full_bank_performs_no_speculative_deposit(bank):
    loop,carried,stored,calls,state=bank
    state['capacity']=0
    assert b.stash_urgent_valuables(loop) is False
    assert not calls


def test_unverified_deposit_stops_before_second_item_or_fallback(bank):
    loop,carried,stored,calls,state=bank
    state['uncertain']=True
    with pytest.raises(ValueError,match='unverified'):
        b.stash_urgent_valuables(loop)
    assert calls==[3] and not stored


@pytest.mark.parametrize('capacity',[None,'40',True,-1])
def test_unknown_capacity_sends_no_deposit(bank,capacity):
    loop,carried,stored,calls,state=bank
    state['capacity']=capacity
    with pytest.raises(ValueError,match='capacity is unqualified'):
        b.stash_urgent_valuables(loop)
    assert not calls


@pytest.mark.parametrize('fits',[True,False])
def test_urgent_after_shopping_skips_optional_travel_only_when_storage_succeeds(monkeypatch,fits):
    from conquest.merchants import bank_stock_alerts
    calls=[]
    monkeypatch.setattr(b,'policy',lambda:{'enabled':True})
    monkeypatch.setattr(b,'open_warehouse',lambda loop:{'silver':200,'stored_silver':0})
    monkeypatch.setattr(b,'close_warehouse',lambda loop:calls.append('close'))
    monkeypatch.setattr(b,'transport_reserve',lambda:200)
    monkeypatch.setattr(b,'stash_urgent_valuables',lambda loop:calls.append('urgent') or fits)
    monkeypatch.setattr(b,'stash_valuables',lambda loop,**kw:calls.append(('ordinary',kw)))
    monkeypatch.setattr(bank_stock_alerts,'record',lambda loop:None)
    assert b.after_shopping(NS(),urgent=True)
    assert calls==(['urgent']+([] if fits else [('ordinary',{'deliver':True})])+['close'])


def test_failed_priority_deposit_never_enters_consolidation_or_delivery(monkeypatch):
    monkeypatch.setattr(b,'policy',lambda:{'enabled':True})
    monkeypatch.setattr(b,'open_warehouse',lambda loop:{})
    monkeypatch.setattr(b,'close_warehouse',lambda loop:None)
    def uncertain(loop):raise ValueError('deposit uncertain')
    monkeypatch.setattr(b,'stash_urgent_valuables',uncertain)
    monkeypatch.setattr(b,'stash_valuables',lambda *a,**kw:pytest.fail('No fallback after uncertain deposit'))
    with pytest.raises(ValueError,match='deposit uncertain'):
        b.after_shopping(NS(),urgent=True)
