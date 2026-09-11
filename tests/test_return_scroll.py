from dataclasses import replace
from types import SimpleNamespace as NS
import pytest
from conquest import return_scroll as r
from conquest.memory_inventory import InventorySnapshot,Item


def snapshots():
    scroll=Item(1,r.TYPE,2,5,0,0);potion=Item(2,1000020,1,1,1,0)
    bag=InventorySnapshot(1,1,(scroll,potion),None,1000,40)
    after=replace(bag,items=(replace(scroll,amount=1),potion))
    source=NS(map_id=1002,position=(110,345),object_address=123,current_hp=500,dead_candidate=False)
    arrival=NS(map_id=1002,position=(430,380),object_address=123,current_hp=500,dead_candidate=False)
    return scroll,bag,after,source,arrival


def test_return_requires_consumption_same_character_and_living_town_arrival():
    item,before,after,source,arrival=snapshots()
    assert r.receipt(before,item,after,source,arrival)
    assert not r.receipt(before,item,before,source,arrival)
    assert not r.receipt(before,item,after,source,source)
    for changes in [dict(dead_candidate=True),dict(object_address=456),dict(map_id=1011)]:
        assert not r.receipt(before,item,after,source,NS(**{**vars(arrival),**changes}))
    assert not r.receipt(before,item,replace(after,silver=999),source,arrival)
    assert not r.receipt(before,item,replace(after,items=after.items[:1]),source,arrival)


def test_last_scroll_disappears_but_inventory_slot_compaction_is_allowed():
    item,before,after,source,arrival=snapshots()
    item=replace(item,amount=1);before=replace(before,items=(item,before.items[1]))
    after=replace(after,items=(replace(before.items[1],slot=0),))
    assert r.receipt(before,item,after,source,arrival)


def test_unqualified_scroll_route_never_uses_input():
    r.write_json(r.POLICY,{'enabled':True,'qualified':False})
    assert not r.return_to_town(NS(living=lambda:pytest.fail('Not qualified')))


def test_stock_only_buys_two_at_verified_price_and_preserves_inventory_space():
    r.write_json(r.POLICY,{'enabled':True,'qualified':False})
    bag={'silver':1000,'items':[],'capacity':40};buys=[]
    def town(action,**kw):
        if action=='shop':return {'products':[{'type_id':r.TYPE,'price':200}]}
        if action=='supplies':return bag
        assert action=='buy' and kw=={'vendor_type':3,'type_id':r.TYPE}
        buys.append(kw);bag['items'].append({'type_id':r.TYPE,'amount':1});bag['silver']-=200
        return {'bought':r.TYPE,'amount':1,'price':200}
    loop=NS(route=NS(restock_map_id=1002,supplies=NS(minimum_free_slots=4)),town=town,record=lambda *a,**k:None)
    r.stock(loop);r.stock(loop)
    assert len(buys)==2 and bag['silver']==600


def test_ambiguous_scroll_submission_blocks_a_second_click(monkeypatch):
    r.write_json(r.STATUS,{'state':'submitted'})
    _,_,_,source,_=snapshots()
    trade=NS(life=lambda **kw:source,click=lambda *a:pytest.fail('No retry'))
    with pytest.raises(ValueError,match='no repeat'):r.use(trade)


def test_scroll_use_verifies_receipt_before_enabling_automatic_returns(monkeypatch):
    from dataclasses import dataclass
    @dataclass
    class Life:
        map_id:int=1002
        position:tuple=(110,345)
        object_address:int=123
        current_hp:int=500
        dead_candidate:bool=False
    item,before,after,_,_=snapshots();state={'used':False};clicks=[]
    source=Life();arrival=replace(source,position=(430,380))
    grid=NS(size=(407.,175.),scroll=(0.,0.),position=(100.,100.))
    def read(name):
        if name in ('Shop','Warehouse'):raise ValueError(name+' not active')
        return grid
    def click(point,button):
        clicks.append((point,button));state['used']=True
    def verified(read,accept,message,**kw):
        result=read()
        assert accept(result),message
        return result
    trade=NS(life=lambda **kw:arrival if state['used'] else source,
        inventory=NS(read=lambda:after if state['used'] else before),
        shop=NS(gui=NS(read=read)),click=click,verified_read=verified)
    result=r.use(trade)
    assert result['state']=='verified' and len(clicks)==1
    assert clicks[0]==((120,120),'right')
    assert r.read_json(r.POLICY)['qualified'] is True
    assert r.read_json(r.STATUS)['state']=='verified'
