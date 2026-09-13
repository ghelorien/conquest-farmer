from types import SimpleNamespace as NS
import pytest
from conquest import banking as b


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(b,'policy',lambda:{'enabled':True,'transport_reserve':200,'withdraw_essentials':True})
    monkeypatch.setattr(b,'stash_valuables',lambda loop,**kwargs:None)


def test_pending_merchant_trade_prevents_cleanup_input_after_storage_error(enabled,monkeypatch):
    from conquest.merchants import delivery_route
    from conquest.discord_notify import write_json
    write_json(delivery_route.STATE,{'active':{'request_id':'uncertain'}})
    monkeypatch.setattr(b,'open_warehouse',lambda loop:{})
    def fail(*a,**kw):raise ValueError('uncertain trade')
    monkeypatch.setattr(b,'stash_valuables',fail)
    monkeypatch.setattr(b,'close_warehouse',lambda loop:pytest.fail('No panel input during uncertain trade'))
    with pytest.raises(ValueError,match='uncertain trade'):b.after_shopping(NS())


@pytest.mark.parametrize('wallet,stored,expected',[(14221,0,('deposit',14021)),(200,500,None),(50,1000,('withdraw',150)),(50,60,('withdraw',60))])
def test_after_shopping_banks_everything_above_fares(enabled,monkeypatch,wallet,stored,expected):
    calls=[]
    monkeypatch.setattr(b,'open_warehouse',lambda loop:{'silver':wallet,'stored_silver':stored})
    monkeypatch.setattr(b,'transfer',lambda loop,direction,amount:calls.append((direction,amount)))
    monkeypatch.setattr(b,'close_warehouse',lambda loop:calls.append('close'))
    b.after_shopping(NS())
    assert calls==([expected] if expected else [])+['close']


def test_transport_withdraws_only_shortfall_before_departure(enabled,monkeypatch):
    calls=[];loop=NS(town=lambda action:{'silver':75})
    monkeypatch.setattr(b,'open_warehouse',lambda loop:{'silver':75,'stored_silver':10000})
    monkeypatch.setattr(b,'transfer',lambda loop,direction,amount:calls.append((direction,amount)))
    monkeypatch.setattr(b,'close_warehouse',lambda loop:None)
    assert b.ensure_transport(loop)
    assert calls==[('withdraw',125)]


def test_restock_budget_does_not_fund_arrows_at_ten_partial_packs(monkeypatch):
    from conquest.routes import RouteLibrary
    route=RouteLibrary().load('bandit')
    bag={'items':[{'uid':i,'type_id':1050001,'amount':24,'limit':1000} for i in range(10)]
         +[{'uid':20,'type_id':1000020,'amount':5,'limit':1}],
         'equipped_ammo':None,'capacity':40,'silver':200}
    monkeypatch.setattr(b,'transport_reserve',lambda:200)
    assert b.shopping_budget(route,bag)==200


@pytest.mark.parametrize('remnant_location',['equipped','inventory'])
def test_speed_refill_funds_both_packs_after_spent_remnant_recycling(monkeypatch,remnant_location):
    from conquest.routes import RouteLibrary
    route=RouteLibrary().load('bandit')
    route=route.model_copy(update={'supplies':route.supplies.model_copy(update={
        'arrow_type':1050002,'arrows_restock_to':10000,'healing_restock_to':5})})
    remnant={'uid':1,'type_id':1050002,'amount':2,'limit':5000}
    bag={'items':[{'type_id':1000020,'amount':5,'limit':1}],
         'equipped_ammo':None,'capacity':40,'silver':200}
    if remnant_location=='equipped':bag['equipped_ammo']=remnant
    else:bag['items'].append(remnant)
    original=b.read_json
    catalog={'cities':{str(route.restock_map_id):{'5':{'products':[
        {'type_id':1050002,'level':73,'price':34000}]}}}}
    monkeypatch.setattr(b,'read_json',lambda path,*a,**kw:catalog if str(path).endswith('archer-shop-catalog.json') else original(path,*a,**kw))
    monkeypatch.setattr(b,'transport_reserve',lambda:200)
    assert b.shopping_budget(route,bag)==71200
    assert remnant['amount']==2  # Budgeting cannot discard actual inventory.


def test_restock_withdrawal_budget_is_essential_supplies_and_fares(enabled,monkeypatch):
    from conquest.routes import RouteLibrary
    route=RouteLibrary().load('poltergeist')
    route=route.model_copy(update={'supplies':route.supplies.model_copy(update={'arrows_restock_to':1600,'healing_restock_to':10})})
    bag={'silver':200,'items':[{'type_id':1000020,'amount':2,'limit':1}],
         'equipped_ammo':{'type_id':1050000,'amount':100,'limit':200},'capacity':40}
    assert b.shopping_budget(route,bag)==880  # One spare pack + eight potions + fares.
    calls=[];loop=NS(route=route,town=lambda action:bag)
    monkeypatch.setattr(b,'open_warehouse',lambda loop:{'silver':200,'stored_silver':10000})
    monkeypatch.setattr(b,'transfer',lambda loop,direction,amount:calls.append((direction,amount)))
    monkeypatch.setattr(b,'close_warehouse',lambda loop:None)
    assert b.fund_restock(loop)
    assert calls==[('withdraw',680)]

@pytest.mark.parametrize('reachable',[True,False])
def test_warehouse_approach_uses_live_reachability(reachable):
    calls=[]
    def town(action,**fields):
        calls.append((action,fields))
        return {'reachable':reachable} if action=='vendor-status' else {}
    loop=NS(living=lambda:{'embedded_controls':{'life':{'map_id':1002,'position':[426,337]}}},
            town=town,terrain=NS(walkable=lambda p:True),
            travel=lambda target,**fields:calls.append(('travel',fields)))
    b.open_warehouse(loop)
    travel=[fields for action,fields in calls if action=='travel']
    assert bool(travel) is not reachable
    if travel:assert travel[0]['vendor_type']==0
    assert [action for action,fields in calls][-4:]==['warehouse-locate','close','open-bank','warehouse-money']


def test_market_bank_removes_intercepting_shop_before_alternate_target(monkeypatch):
    monkeypatch.setattr(b.time,'sleep',lambda _:None)
    panels={'Shop'};calls=[];attempts=[0]
    def town(action,**fields):
        calls.append((action,fields))
        if action=='vendor-status':return {'reachable':True}
        if action=='warehouse-locate':return {'position':[182,180]}
        if action=='close':panels.discard(fields['window'])
        if action=='open-bank':
            assert 'Shop' not in panels
            attempts[0]+=1
            if attempts[0]==1:
                panels.update(('Shop','Inventory'))
                raise ValueError('Warehouse opening unverified; no repeat input issued')
        if action=='warehouse-open':
            assert not panels
        return {}
    loop=NS(living=lambda:{'embedded_controls':{'life':{'map_id':1036,'position':[195,176]}}},
            town=town,record=lambda *a,**kw:None)
    b.open_warehouse(loop)
    assert sum(action=='warehouse-open' for action,_ in calls)==1
    assert not any(action in ('buy','sell','warehouse-deposit') for action,_ in calls)

def test_warehouse_panel_open_timeout_rechecks_without_repeating_transfer(monkeypatch):
    monkeypatch.setattr(b.time,'sleep',lambda _:None)
    calls=[];attempts=[0]
    def town(action,**fields):
        calls.append(action)
        if action=='vendor-status':return {'reachable':True}
        if action=='open-bank':
            attempts[0]+=1
            if attempts[0]==1:raise ValueError('Warehouse opening unverified; no repeat input issued')
        return {}
    loop=NS(living=lambda:{'embedded_controls':{'life':{'map_id':1002,'position':[413,354]}}},
            town=town,record=lambda *a,**kw:None)
    b.open_warehouse(loop)
    assert calls.count('open-bank')==2 and calls.count('warehouse-money')==1
    assert not any(action.startswith('warehouse-money-') for action in calls)


def test_warehouse_uncertain_money_error_is_not_retried(monkeypatch):
    calls=[]
    def town(action,**fields):
        calls.append(action)
        if action=='vendor-status':return {'reachable':True}
        if action=='open-bank':raise ValueError('Unexpected bank balance')
        return {}
    loop=NS(living=lambda:{'embedded_controls':{'life':{'map_id':1002,'position':[413,354]}}},town=town)
    with pytest.raises(ValueError,match='Unexpected bank balance'):b.open_warehouse(loop)
    assert calls.count('open-bank')==1


def test_stash_plus_and_meteor_by_inventory_uid_and_require_receipt():
    calls=[]
    items=[{'uid':1,'type_id':530013,'plus':2,'slot':0},
           {'uid':2,'type_id':1088001,'plus':0,'slot':1},
           {'uid':3,'type_id':1000020,'plus':0,'slot':2},
           {'uid':4,'type_id':500089,'plus':2,'slot':None}]
    def town(action,**fields):
        if action=='supplies':return {'items':items}
        calls.append((action,fields))
        return {'stored':fields['uid'],'verified_in_warehouse':True}
    b.stash_valuables(NS(town=town,record=lambda *a,**k:None))
    assert calls==[('warehouse-deposit',{'uid':1}),('warehouse-deposit',{'uid':2})]


def test_unverified_valuable_deposit_is_not_repeated():
    calls=[]
    def town(action,**fields):
        if action=='supplies':return {'items':[{'uid':1,'type_id':1088001,'slot':0}]}
        calls.append(action);return {}
    with pytest.raises(ValueError,match='unverified'):
        b.stash_valuables(NS(town=town,record=lambda *a,**k:None))
    assert calls==['warehouse-deposit']


def test_bandit_two_pack_refill_withdraws_full_shopping_budget(enabled,monkeypatch):
    from conquest.routes import RouteLibrary
    route=RouteLibrary().load('bandit')
    bag={'silver':200,'items':[],
         'equipped_ammo':{'type_id':1050001,'amount':0,'limit':1000},'capacity':40}
    calls=[];loop=NS(route=route,town=lambda action:bag)
    monkeypatch.setattr(b,'open_warehouse',lambda loop:{'silver':200,'stored_silver':100000})
    monkeypatch.setattr(b,'transfer',lambda loop,direction,amount:calls.append((direction,amount)))
    monkeypatch.setattr(b,'close_warehouse',lambda loop:None)
    assert b.fund_restock(loop)
    # Two 4,800-silver packs, five 60-silver potions, 3,000 spending reserve.
    assert calls==[('withdraw',12900)]


def test_level_73_budget_funds_speed_upgrade_only_when_pack_room_exists(monkeypatch):
    from conquest.routes import RouteLibrary
    route=RouteLibrary().load('bandit')
    route=route.model_copy(update={'supplies':route.supplies.model_copy(update={
        'arrow_type':1050001,'arrows_restock_to':2000,'healing_restock_to':5})})
    original=b.read_json
    catalog={'cities':{str(route.restock_map_id):{'5':{'products':[
        {'type_id':1050001,'level':32,'price':4800},
        {'type_id':1050002,'level':73,'price':34000}]}}}}
    monkeypatch.setattr(b,'read_json',lambda path,*a,**kw:catalog if str(path).endswith('archer-shop-catalog.json') else original(path,*a,**kw))
    monkeypatch.setattr(b,'transport_reserve',lambda:200)
    bag={'items':[{'uid':1,'type_id':1050001,'amount':1000,'limit':1000},
                  {'uid':2,'type_id':1000020,'amount':5,'limit':1}],
         'equipped_ammo':None,'capacity':40,'silver':200}
    assert b.shopping_budget(route,bag,level=72)==8000
    assert b.shopping_budget(route,bag,level=73)==37200
    bag['items'].append({'uid':3,'type_id':1050001,'amount':1000,'limit':1000})
    assert b.shopping_budget(route,bag,level=73)==200


def test_two_speed_packs_require_no_refill_funds_when_equipped_is_partial(monkeypatch):
    from conquest.routes import RouteLibrary
    route=RouteLibrary().load('bandit')
    route=route.model_copy(update={'supplies':route.supplies.model_copy(update={
        'arrow_type':1050002,'arrows_restock_to':10000,'healing_restock_to':5})})
    monkeypatch.setattr(b,'transport_reserve',lambda:200)
    bag={'items':[{'uid':1,'type_id':1050002,'amount':5000,'limit':5000},
                  {'uid':2,'type_id':1000020,'amount':5,'limit':1}],
         'equipped_ammo':{'uid':3,'type_id':1050002,'amount':4,'limit':5000},'capacity':40,'silver':200}
    assert b.shopping_budget(route,bag,level=73)==200

def test_market_open_uses_one_lower_click_then_verifies(monkeypatch):
    monkeypatch.setattr(b.time,'sleep',lambda _:None)
    calls=[]
    def town(action,**fields):
        calls.append(action)
        if action=='warehouse-locate':return {'position':[182,180]}
        if action=='vendor-status':return {'reachable':True}
        if action=='open-bank' and calls.count(action)==1:
            raise ValueError('Warehouse opening unverified; no repeat input issued')
        if action=='service-close-panel':raise ValueError('Requested GUI window is not active')
        return {}
    loop=NS(living=lambda:{'embedded_controls':{'life':{'map_id':1036,'position':[186,184]}}},town=town,record=lambda *a,**kw:None)
    b.open_warehouse(loop)
    assert calls.count('warehouse-open')==1
    assert calls[-2:]==['open-bank','warehouse-money']
    assert not any(action.startswith('warehouse-money-') for action in calls)

@pytest.mark.parametrize('kind,plus,slot,wanted',[
    (500008,0,1,False),(500009,0,1,False),(500003,1,1,False),
    (500003,2,1,True),(500008,2,1,True),(500003,12,1,True),
    (500003,13,1,False),(500003,None,1,False),(500003,2,None,False),
    (1088000,0,1,True),(1088001,0,1,False),(720027,0,1,False),
    (1000020,2,1,False)])
def test_urgent_banking_only_carried_dragonballs_and_plus_two(kind,plus,slot,wanted):
    item={'uid':123,'type_id':kind,'plus':plus,'slot':slot}
    assert bool(b.urgent_valuables([item])) is wanted
    assert bool(b.urgent_valuables([NS(**item)])) is wanted
