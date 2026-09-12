import copy
import time
from types import SimpleNamespace as NS
import pytest
from conquest.discord_notify import read_json,write_json
from conquest.merchants import delivery_journey as journey,delivery_route
from conquest import banking,meteor_banking


def item(uid,kind=130009):
    return dict(uid=uid,type_id=kind,amount=1,limit=1,plus=0,slot=0)


@pytest.fixture
def trip(monkeypatch):
    world={'map':1011,'silver':50,'stored_silver':1000}
    bag=[item(1),item(2,2000031),item(3,1050002)]
    bank=[];merchant=[];events=[]
    route={'outbound':{'verified':True,'source_map':1011,'destination_map':1036,'fare':100},
           'return':{'verified':True,'source_map':1036,'destination_map':1011,'fare':30}}
    write_json(delivery_route.POLICY,{'enabled':True,'parity_verified':True})
    write_json(meteor_banking.POLICY,{'origins':{'1011':route}})
    def town(action,**fields):
        if action=='supplies':return {'silver':world['silver'],'items':copy.deepcopy(bag),'capacity':40}
        if action=='warehouse-money':return {'silver':world['silver'],'stored_silver':world['stored_silver']}
        if action=='warehouse-items':return {'items':copy.deepcopy(bank),'capacity':40}
        if action=='warehouse-deposit':
            assert read_json(journey.JOURNAL)['deposit_pending']['uid']==fields['uid']
            row=next(i for i in bag if i['uid']==fields['uid'])
            bag.remove(row);bank.append(row);events.append(('deposit',row['uid']))
            return {'uid':row['uid'],'type_id':row['type_id'],'verified_in_warehouse':True}
        pytest.fail(action)
    loop=NS(town=town,living=lambda:{'embedded_controls':{'life':{'map_id':world['map']}}},
            record=lambda event,**fields:events.append((event,fields)))
    def travel(loop,plan,*,before_submit=None):
        assert world['map']==plan['source_map']
        before_submit()
        events.append(('trip',plan['destination_map']))
        world.update(map=plan['destination_map'],silver=world['silver']-plan['fare'])
    def transfer(loop,direction,amount):
        assert direction=='withdraw';events.append(('withdraw',amount))
        world['silver']+=amount;world['stored_silver']-=amount
    monkeypatch.setattr(meteor_banking,'trip',travel)
    monkeypatch.setattr(meteor_banking,'approach_market_warehouse',lambda *a:events.append(('bank_approach',world['map'])))
    monkeypatch.setattr(banking,'open_warehouse',lambda loop:events.append(('bank_open',world['map'])))
    monkeypatch.setattr(banking,'close_warehouse',lambda loop:events.append(('bank_close',world['map'])))
    monkeypatch.setattr(banking,'transport_reserve',lambda:200)
    monkeypatch.setattr(banking,'transfer',transfer)
    monkeypatch.setattr('conquest.navigation.read_terrain',lambda root,map_id:NS(map_id=map_id))
    def deliver(loop,*,send):
        if not read_json(delivery_route.POLICY).get('enabled'):return
        for row in list(bag):
            if row['type_id']==130009:bag.remove(row);merchant.append(row)
    monkeypatch.setattr(delivery_route,'market_storage',deliver)
    def detailed(rows):return [{**i,'quantity':i['amount'],'gem1':0,'gem2':0,'bound':False} for i in rows]
    def snap(name,uid,rows,map_id):
        return dict(character=name,character_uid=uid,identity={'pid':uid},server='America',timestamp=time.time(),
                    hp=100,map_id=map_id,silver=100,capacity=40,inventory=detailed(rows),booth=[],booth_open=True)
    def send(body):
        if body['action']=='delivery-readiness':return {'qualified':True}
        if body['action']=='status':return {'characters':{'Dutch':{'ready':True,'snapshot':snap('Dutch',2,merchant,1036)}}}
        if body['action']=='delivery-source':return {'farmer':snap('Parasite',1,bag,world['map'])}
        pytest.fail(body)
    return NS(loop=loop,world=world,bag=bag,bank=bank,merchant=merchant,events=events,send=send,travel=travel)


def test_required_town_delivery_round_trip_keeps_supplies_and_fares(trip):
    assert journey.start(trip.loop,send=trip.send)
    assert trip.world['map']==1011 and trip.world['silver']==200
    assert [i['uid'] for i in trip.merchant]==[1]
    assert [i['uid'] for i in trip.bank]==[2]
    assert [i['uid'] for i in trip.bag]==[3]
    assert ('withdraw',280) in trip.events
    assert read_json(journey.JOURNAL)['phase']=='completed'
    assert trip.loop.overflow_bank_changed
    before=list(trip.events)
    assert not journey.start(trip.loop,send=trip.send)  # No second trip for ammunition or empty loot.
    assert trip.events==before


def test_last_fallback_slot_can_fill_when_merchant_still_has_capacity(trip,monkeypatch):
    from test_delivery_route import snapshot
    original=trip.loop.town
    def town(action,**fields):
        result=original(action,**fields)
        if action=='warehouse-items':result['capacity']=1
        return result
    trip.loop.town=town
    def send(body):
        if body['action']=='delivery-pair':
            return {'farmer':snapshot('Parasite',1,[],(10,10)),
                    'merchant':snapshot(body['character'],2,[],(20,10))}
        return trip.send(body)
    # Use the real capacity classifier and the same checked travel contract.
    trip.loop.terrain=NS(travel_path=lambda a,b:[a,b])
    # resume replaces terrain on arrival; furnish its fixture with path support.
    monkeypatch.setattr('conquest.navigation.read_terrain',lambda *a:trip.loop.terrain)
    assert journey.start(trip.loop,send=send)
    assert trip.world['map']==1011 and len(trip.bank)==1
    assert [i['uid'] for i in trip.bag]==[3]


@pytest.mark.parametrize('reason',['disabled','unknown_binding','unavailable','unqualified_route','no_funds'])
def test_failed_preflight_leaves_origin_warehouse_fallback_untouched(trip,reason):
    send=trip.send
    if reason=='disabled':write_json(delivery_route.POLICY,{'enabled':False})
    if reason=='unqualified_route':write_json(meteor_banking.POLICY,{})
    if reason=='no_funds':trip.world['stored_silver']=0
    def blocked(body):
        value=send(body)
        if reason=='unknown_binding' and body['action']=='delivery-source':
            for row in value['farmer']['inventory']:row.pop('bound')
        if reason=='unavailable' and body['action']=='status':value={'characters':{}}
        return value
    assert not journey.start(trip.loop,send=blocked)
    assert trip.events==[] and trip.world['map']==1011
    assert not journey.pending()


def test_disconnect_after_fare_arrival_reconciles_without_repaying(trip,monkeypatch):
    def lost(loop,plan,**kw):
        trip.travel(loop,plan,**kw)
        if plan['destination_map']==1036:raise OSError('response lost after arrival')
    monkeypatch.setattr(meteor_banking,'trip',lost)
    with pytest.raises(OSError):journey.start(trip.loop,send=trip.send)
    assert journey.pending() and trip.world['map']==1036
    write_json(delivery_route.POLICY,{'enabled':False})
    assert journey.resume(trip.loop,send=trip.send)
    assert trip.events.count(('trip',1036))==1 and trip.events.count(('trip',1011))==1


def test_uncertain_fare_while_still_in_origin_cannot_repeat(trip,monkeypatch):
    def uncertain(loop,plan,*,before_submit):before_submit();raise OSError('uncertain payment')
    monkeypatch.setattr(meteor_banking,'trip',uncertain)
    with pytest.raises(OSError):journey.start(trip.loop,send=trip.send)
    with pytest.raises(ValueError,match='no repeat fare'):journey.resume(trip.loop,send=trip.send)
    assert not any(e=='trip' for e,_ in trip.events)


def test_lost_deposit_receipt_reconciles_exact_bank_uid_before_return(trip,monkeypatch):
    original=trip.loop.town
    def lost(action,**fields):
        value=original(action,**fields)
        if action=='warehouse-deposit':raise OSError('lost deposit response')
        return value
    trip.loop.town=lost
    with pytest.raises(OSError):journey.start(trip.loop,send=trip.send)
    assert read_json(journey.JOURNAL)['deposit_pending']['uid']==2
    trip.loop.town=original
    assert journey.resume(trip.loop,send=trip.send)
    assert trip.events.count(('deposit',2))==1 and trip.world['map']==1011


def test_uncertain_trade_never_starts_warehouse_input(trip,monkeypatch):
    def uncertain(*a,**kw):raise ValueError('trade uncertain')
    monkeypatch.setattr(delivery_route,'market_storage',uncertain)
    with pytest.raises(ValueError,match='trade uncertain'):journey.start(trip.loop,send=trip.send)
    assert not any(e in ('bank_approach','deposit') for e,_ in trip.events)
    assert trip.world['map']==1036 and journey.pending()
