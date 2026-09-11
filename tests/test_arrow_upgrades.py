import pytest
from types import SimpleNamespace
from conquest.arrow_upgrades import choose_arrow_upgrade,current_arrow
from conquest.overnight import OvernightLoop,supply_counts
from conquest.routes import RouteLibrary


def state(level=32,kind=1050000,attack=10):
    return {'level':level,'profession':41,'equipment':{'arrows':dict(type_id=kind,level=1 if kind==1050000 else 32,
        profession=40,attack_min=attack,attack_max=attack)}}


def product(kind=1050001,level=32,attack=50,price=4800):
    return dict(type_id=kind,name='IronArrow',level=level,profession=40,attack_min=attack,attack_max=attack,price=price)


def test_level_32_selects_iron_arrows_without_spending_supply_reserve():
    p=product()
    assert choose_arrow_upgrade([p],state(),7800)==p
    assert choose_arrow_upgrade([p],state(31),20000) is None
    assert choose_arrow_upgrade([p],state(),7799) is None
    assert choose_arrow_upgrade([p],state(kind=1050001,attack=50),20000) is None
    assert choose_arrow_upgrade([product(kind=1050020,attack=100)],state(),20000) is None


def test_equipped_or_reserved_iron_is_not_treated_as_missing_lucky_arrows():
    assert current_arrow(state(kind=1050001,attack=50))==1050001
    assert current_arrow({'level':32,'equipment':{}},reserves=[1050000,1050001])==1050001
    assert current_arrow({'level':31,'equipment':{}},reserves=[1050000,1050001])==1050000
    loop=OvernightLoop.__new__(OvernightLoop);loop.route=RouteLibrary().load('bandit');loop.record=lambda *a,**k:None
    bag={'items':[{'type_id':1050001,'amount':1000},{'type_id':1050000,'amount':200}],
         'equipped_ammo':{'type_id':1050001,'amount':836},'silver':15983,'capacity':40}
    loop.town=lambda *args:bag
    loop.adopt_ammunition(state(kind=1050001,attack=50))
    assert loop.route.supplies.arrow_type==1050001
    assert supply_counts(bag,loop.route)['arrows']==1836


def test_native_reload_uses_selected_uid_and_closes_inventory():
    from conquest.native_farm import NativeFarmSupervisor
    s=NativeFarmSupervisor.__new__(NativeFarmSupervisor);calls=[]
    s.observer=SimpleNamespace(town_trade=lambda body:calls.append(body))
    s.dispatch=lambda callback:callback()
    inventory=SimpleNamespace(items=[SimpleNamespace(uid=1,type_id=1050000,amount=200),
        SimpleNamespace(uid=2,type_id=1050001,amount=1000)])
    s.reload_arrows(inventory,1050001)
    assert calls==[{'action':'equip-arrows','uid':2},{'action':'close','window':'Inventory'}]


def test_optional_iron_topup_preserves_money_but_essential_restock_can_spend_it():
    loop=OvernightLoop.__new__(OvernightLoop)
    loop.route=RouteLibrary().load('bandit')
    loop.route=loop.route.model_copy(update={'supplies':loop.route.supplies.model_copy(update={'arrow_type':1050001})})
    loop.record=lambda *a,**k:None
    bag={'items':[],'equipped_ammo':{'type_id':1050001,'amount':1000},'silver':7000,'capacity':40}
    calls=[]
    def town(action,**body):
        calls.append(action)
        return bag if action=='supplies' else {'products':[product()]} if action=='shop' else {'bought':1050001}
    loop.town=town
    assert not loop.buy_supply(5,1050001)
    assert 'buy' not in calls
    bag['equipped_ammo']['amount']=0
    assert loop.buy_supply(5,1050001)
    assert calls[-1]=='buy'


def test_tiny_iron_reserves_can_be_recycled_without_selling_last_ammo():
    from conquest.town_trade import expendable_arrow
    from conquest.memory_inventory import Item,InventorySnapshot
    small=Item(1,1050001,23,1000,0,0)
    full=Item(2,1050001,1000,1000,None,0)
    bag=InventorySnapshot(0,0,(small,),full,9000,40)
    assert expendable_arrow(small,bag)
    assert not expendable_arrow(small,InventorySnapshot(0,0,(small,),None,9000,40))


@pytest.mark.parametrize('count',[10,21,22])
def test_purchase_cap_counts_partial_packs_and_prevents_topups(count):
    from conquest.arrow_upgrades import arrow_pack_count,require_arrow_purchase_room
    loop=OvernightLoop.__new__(OvernightLoop);loop.route=RouteLibrary().load('bandit');loop.record=lambda *a,**kw:None
    bag={'items':[{'uid':i,'type_id':1050001 if i%2 else 1050000,'amount':24} for i in range(count-1)],
         'equipped_ammo':{'uid':99,'type_id':1050001,'amount':23},'silver':100000,'capacity':40}
    loop.town=lambda action,**kw:bag if action=='supplies' else pytest.fail('No shop input at pack cap')
    assert arrow_pack_count(bag)==count
    assert not loop.buy_supply(5,1050001)
    with pytest.raises(ValueError,match='ten or more'):require_arrow_purchase_room(bag)


def test_arrow_upgrade_does_not_bypass_pack_cap():
    from conquest.arrow_upgrades import review_arrows
    bag={'items':[{'uid':i,'type_id':1050000,'amount':24} for i in range(10)],'equipped_ammo':None}
    loop=SimpleNamespace(town=lambda action,**kw:bag if action=='supplies' else pytest.fail('Must not buy upgrade'),record=lambda *a,**kw:None)
    assert review_arrows(loop,[product()],state(),20000)==state()


def test_pack_count_deduplicates_equipped_uid_and_accepts_inventory_reader_snapshot():
    from conquest.arrow_upgrades import arrow_pack_count,require_arrow_purchase_room
    from conquest.memory_inventory import InventorySnapshot,Item
    item=Item(1,1050001,24,1000,0,0)
    bag=InventorySnapshot(0,0,(item,),item,200,40)
    assert arrow_pack_count(bag)==1
    require_arrow_purchase_room(bag)


def test_spent_scatter_remnants_are_retired_before_refill_even_with_free_slots():
    from conquest.town_trade import expendable_arrow
    from conquest.memory_inventory import InventorySnapshot,Item
    spent=Item(1,1050001,2,1000,0,0)
    assert expendable_arrow(spent,InventorySnapshot(0,0,(spent,),None,200,40))
    loop=OvernightLoop.__new__(OvernightLoop);loop.route=RouteLibrary().load('bandit');loop.record=lambda *a,**kw:None
    bag={'items':[{'uid':1,'type_id':1050001,'amount':2}], 'equipped_ammo':None,'silver':200,'capacity':40}
    calls=[]
    def town(action,**kw):
        if action=='supplies':return bag
        assert action=='sell_partial_arrow';calls.append(kw['uid']);bag['items']=[];return {'sold':kw['uid']}
    loop.town=town;loop.recycle_small_arrows()
    assert calls==[1]
