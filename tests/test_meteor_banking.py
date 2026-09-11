from types import SimpleNamespace as NS
import pytest
from conquest import meteor_banking as m,banking,storage_halt
from conquest.discord_notify import read_json,write_json


def item(uid,kind=m.METEOR):
    return dict(uid=uid,type_id=kind,amount=1,limit=1,slot=0,plus=0)


@pytest.fixture
def route(monkeypatch):
    state={'map':1011,'silver':1000,'capacity':60}
    bag=[];local=[item(i) for i in range(10)];market=[];events=[]
    outbound={'verified':True,'destination_map':1036,'fare':100}
    inbound={'verified':True,'destination_map':1011,'fare':0}
    exchange={'transaction_verified':True,'fee':0,'identity':{'name':'MillionaireLee'},
              'approach':[230,240],'dialogs':[{'option':'intro'},{'option':'pack'}]}
    write_json(m.POLICY,{'enabled':True,'qualified':True,'exchange':exchange,
        'origins':{'1011':{'outbound':outbound,'return':inbound}}})
    def town(action,**fields):
        if action=='vendor-status':return {'reachable':False}
        bank=market if state['map']==1036 else local
        if action=='supplies':return {'items':list(bag),'silver':state['silver'],'capacity':40}
        if action=='warehouse-items':return {'items':list(bank),'capacity':state['capacity']}
        if action=='warehouse-money':return {'silver':state['silver'],'stored_silver':5000}
        if action=='service-locate':return {'identity':exchange['identity']}
        if action in ('service-open','service-close-panel'):return {}
        if action in ('warehouse-withdraw-meteor','warehouse-deposit'):
            source,target=(bank,bag) if action=='warehouse-withdraw-meteor' else (bag,bank)
            value=next(i for i in source if i['uid']==fields['uid']);source.remove(value);target.append(value)
            events.append((action,fields['uid']))
            return {'verified_in_inventory':True,'verified_in_warehouse':True}
        pytest.fail(action)
    def trip(loop,leg):
        if state['map']==1036:assert not m.carried(loop)
        state['map']=leg['destination_map'];state['silver']-=leg['fare'];events.append(('trip',state['map']))
    def select(loop,name,step):
        events.append(('choice',step['option']))
        if step['option']=='pack':
            assert len([i for i in bag if i['type_id']==m.METEOR])==10
            bag[:]=[i for i in bag if i['type_id']!=m.METEOR]+[item(99,m.SCROLL)]
    monkeypatch.setattr(m,'trip',trip);monkeypatch.setattr(m,'select_saved_dialog',select)
    monkeypatch.setattr('conquest.navigation.read_terrain',lambda *a:NS(map_id=state['map']))
    monkeypatch.setattr(banking,'open_warehouse',lambda loop:events.append(('open',state['map'])))
    monkeypatch.setattr(banking,'close_warehouse',lambda loop:events.append(('close',state['map'])))
    loop=NS(town=town,living=lambda:{'embedded_controls':{'life':{'map_id':state['map'],'position':[190,189]}}},
            record=lambda *a,**kw:None,travel=lambda *a,**kw:events.append(('travel',a[0])))
    return loop,state,bag,local,market,events


def test_full_round_trip_packs_banks_returns_and_does_not_repeat(route):
    loop,state,bag,local,market,events=route
    assert m.consolidate(loop,{'items':list(local)})
    assert not bag and not local and market==[item(99,m.SCROLL)]
    assert state['map']==1011 and state['silver']==900
    assert read_json(m.JOURNAL)['phase']=='completed'
    assert events.count(('choice','pack'))==1
    assert not m.consolidate(loop,{'items':list(local)})


def test_banked_scroll_resumes_return_without_reexchange(route):
    loop,state,bag,local,market,events=route;state['map']=1036
    market.append(item(99,m.SCROLL))
    write_json(m.JOURNAL,{'phase':'stored_in_market','origin':1011,'scroll_uid':99})
    assert m.resume(loop) and state['map']==1011
    assert not any(e[0] in ('choice','warehouse-withdraw-meteor','warehouse-deposit') for e in events)


def test_missing_banked_scroll_blocks_departure(route):
    loop,state,bag,local,market,events=route;state['map']=1036
    write_json(m.JOURNAL,{'phase':'stored_in_market','origin':1011,'scroll_uid':99})
    with pytest.raises(ValueError,match='not in Market storage'):m.resume(loop)
    assert not any(e[0]=='trip' for e in events)


def test_partial_withdrawal_reconciles_exact_ids(route):
    loop,state,bag,local,market,events=route;bag.append(local.pop(0))
    write_json(m.JOURNAL,{'phase':'withdrawing','origin':1011,'meteor_uids':list(range(10))})
    assert m.resume(loop)
    assert ('warehouse-withdraw-meteor',0) not in events
    assert sum(e[0]=='warehouse-withdraw-meteor' for e in events)==9


def test_user_consumed_scroll_still_banks_remaining_valuables_before_return(route):
    loop,state,bag,local,market,events=route;state['map']=1036
    bag.append(item(101))
    write_json(m.JOURNAL,{'phase':'storing_scroll','origin':1011,'scroll_uid':99,
        'user_confirmed_scroll_consumption':{'uid':99,'confirmed':True,
                                            'source':'explicit user confirmation'}})
    assert m.resume(loop) and state['map']==1011
    assert not bag and item(101) in market
    assert events.index(('warehouse-deposit',101))<events.index(('trip',1011))
    assert read_json(m.JOURNAL)['scroll_uid']==99
    assert not any(e[0]=='choice' for e in events)


@pytest.mark.parametrize('uid,confirmed,source',[(98,True,'explicit user confirmation'),
    (99,False,'explicit user confirmation'),(99,True,'inferred from missing item')])
def test_scroll_consumption_requires_explicit_confirmation_for_exact_uid(route,uid,confirmed,source):
    loop,state,bag,local,market,events=route;state['map']=1036
    write_json(m.JOURNAL,{'phase':'storing_scroll','origin':1011,'scroll_uid':99,
        'user_confirmed_scroll_consumption':{'uid':uid,'confirmed':confirmed,'source':source}})
    with pytest.raises(ValueError,match='not in Market storage'):m.resume(loop)
    assert not any(e[0]=='trip' for e in events)


def test_uncertain_departure_never_pays_again(route):
    loop,state,bag,local,market,events=route
    write_json(m.JOURNAL,{'phase':'travelling','origin':1011,'departure_attempted':True})
    with pytest.raises(ValueError,match='no repeat fare'):m.resume(loop)
    assert not any(e[0]=='trip' for e in events)


def test_exchange_receipt_resumes_without_repeating_transaction(route):
    loop,state,bag,local,market,events=route;state['map']=1036
    bag.append(item(99,m.SCROLL))
    write_json(m.JOURNAL,{'phase':'exchange_pending','origin':1011,'meteor_uids':list(range(10)),
                         'before':{'items':list(local),'silver':1000}})
    assert m.resume(loop)
    assert not any(e[0]=='choice' for e in events)


def test_full_market_stops_and_never_returns(route,monkeypatch):
    loop,state,bag,local,market,events=route;state.update(map=1036,capacity=1)
    bag.append(item(99,m.SCROLL))
    write_json(m.JOURNAL,{'phase':'storing_scroll','origin':1011,'scroll_uid':99})
    def stop(*args):raise RuntimeError('storage halt')
    monkeypatch.setattr(storage_halt,'request_stop',stop)
    with pytest.raises(RuntimeError,match='storage halt'):m.resume(loop)
    assert read_json(m.JOURNAL)['phase']=='market_full'
    assert state['map']==1036 and not any(e[0]=='trip' for e in events)


def test_duplicate_meteor_ids_cannot_form_batch():
    with pytest.raises(ValueError,match='duplicate'):m.batch([item(1)]*10)


def test_market_approach_avoids_blocked_frontage_and_early_npc_stop():
    calls=[]
    loop=NS(living=lambda:{'embedded_controls':{'life':{'map_id':1036,'position':[190,189]}}},
        town=lambda *a,**kw:None,travel=lambda *a,**kw:calls.append((a,kw)))
    m.approach_market_warehouse(loop,'Bank valuables')
    assert [a[0] for a,kw in calls]==[(186,184),(182,184)]
    assert all('service_name' not in kw for a,kw in calls)
    assert all(kw['vendor_type']==0 and kw['arrival_radius']==2 for a,kw in calls)


def test_market_approach_uses_reachable_warehouse_without_waypoint_movement():
    loop=NS(living=lambda:{'embedded_controls':{'life':{'map_id':1036,'position':[182,193]}}},
        town=lambda action,**kw:{'reachable':True} if action=='vendor-status' else None,
        travel=lambda *a,**kw:pytest.fail('Warehouse already reachable'))
    m.approach_market_warehouse(loop,'Bank valuables')


def test_market_approach_accepts_adjacent_tile_instead_of_crowded_exact_target():
    loop=NS(living=lambda:{'embedded_controls':{'life':{'map_id':1036,'position':[183,185]}}},
        town=lambda *a,**kw:None,travel=lambda *a,**kw:pytest.fail('Already beside approach'))
    m.approach_market_warehouse(loop,'Bank valuables')


def test_market_approach_stays_at_verified_bank_tile():
    loop=NS(living=lambda:{'embedded_controls':{'life':{'map_id':1036,'position':[182,184]}}},
        town=lambda *a,**kw:None,travel=lambda *a,**kw:pytest.fail('Already at bank'))
    m.approach_market_warehouse(loop,'Bank valuables')


def test_market_approach_recovers_obstructed_southern_crossing_once():
    calls=[]
    def travel(point,**kw):
        calls.append(point)
        if len(calls)==1:raise ValueError('Town route remains obstructed')
    loop=NS(living=lambda:{'embedded_controls':{'life':{'map_id':1036,'position':[201,215]}}},
        town=lambda *a,**kw:None,travel=travel)
    m.approach_market_warehouse(loop,'Bank valuables')
    assert calls==[(186,184),(189,215),(189,203),(186,184),(182,184)]


def test_market_frontage_reposition_stops_as_soon_as_warehouse_reachable():
    calls=[];panels=[]
    def travel(point,**kw):
        calls.append(point)
        assert kw['vendor_type']==0
        if len(calls)==1:raise ValueError('Town route remains obstructed')
    def town(action,**kw):
        if action=='vendor-status':return {'reachable':len(calls)>=2}
        panels.append(kw['window'])
    loop=NS(living=lambda:{'embedded_controls':{'life':{'map_id':1036,'position':[183,190]}}},
        town=town,travel=travel)
    m.approach_market_warehouse(loop,'Bank valuables')
    assert calls==[(186,184),(186,199)]
    assert panels==['Dialog','Inventory']


def test_market_frontage_repeated_failure_is_bounded():
    calls=[]
    def travel(point,**kw):
        calls.append(point)
        raise ValueError('Town route remains obstructed')
    loop=NS(living=lambda:{'embedded_controls':{'life':{'map_id':1036,'position':[183,190]}}},
        town=lambda *a,**kw:None,travel=travel)
    with pytest.raises(ValueError,match='remains obstructed'):
        m.approach_market_warehouse(loop,'Bank valuables')
    assert calls==[(186,184),(186,199)]


def test_market_approach_does_not_retry_unrelated_or_repeated_failure():
    calls=[]
    def travel(point,**kw):
        calls.append(point)
        raise ValueError('Town route remains obstructed')
    loop=NS(living=lambda:{'embedded_controls':{'life':{'map_id':1036,'position':[201,215]}}},
        town=lambda *a,**kw:None,travel=travel)
    with pytest.raises(ValueError,match='remains obstructed'):
        m.approach_market_warehouse(loop,'Bank valuables')
    assert calls==[(186,184),(189,215)]
    calls.clear()
    loop.living=lambda:{'embedded_controls':{'life':{'map_id':1036,'position':[230,240]}}}
    with pytest.raises(ValueError,match='remains obstructed'):
        m.approach_market_warehouse(loop,'Bank valuables')
    assert calls==[(186,184)]


def test_crossing_recovery_does_not_disable_later_frontage_recovery():
    calls=[];position=[203,215]
    def travel(point,**kw):
        calls.append(point)
        if calls.count((186,184))==1 and len(calls)==1:
            raise ValueError('Town route remains obstructed')
        if len(calls)==4:
            position[:]=[188,191]
            raise ValueError('Town route remains obstructed')
        position[:]=point
    loop=NS(living=lambda:{'embedded_controls':{'life':{'map_id':1036,'position':position.copy()}}},
        town=lambda action,**kw:{'reachable':position==[176,183]} if action=='vendor-status' else None,
        travel=travel)
    m.approach_market_warehouse(loop,'Bank valuables')
    assert calls==[(186,184),(189,215),(189,203),(186,184),(186,199),(176,199),(176,183)]


def test_service_open_retries_only_open_after_range_walk(monkeypatch):
    clock=[0.];opens=[];records=[{'kind':1,'text':'Yeah. Thanks.'}]
    monkeypatch.setattr(m.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(m.time,'sleep',lambda seconds:clock.__setitem__(0,clock[0]+seconds))
    def read(*args):
        if len(opens)==1:raise ValueError('Requested GUI window is not active')
        return {'records':records}
    monkeypatch.setattr('conquest.worker.request',read)
    loop=NS(info='test',living=lambda:None,record=lambda *a,**kw:None,
            town=lambda action,**kw:opens.append(action))
    m.open_saved_service(loop,'Mark.Controller',{'records':records})
    assert opens==['service-open','service-open']


def test_service_open_never_selects_or_retries_changed_dialog(monkeypatch):
    opens=[]
    monkeypatch.setattr('conquest.worker.request',lambda *a:{'records':[{'text':'Unexpected service'}]})
    loop=NS(info='test',living=lambda:None,record=lambda *a,**kw:None,
            town=lambda action,**kw:opens.append(action))
    with pytest.raises(ValueError,match='dialog changed'):
        m.open_saved_service(loop,'Mark.Controller',{'records':[]})
    assert opens==['service-open']


def test_manual_storage_clear_reconciles_meteor_journal():
    write_json(m.JOURNAL,{'phase':'market_full','origin':1011,'scroll_uid':99})
    write_json(storage_halt.HALT,{'active':True})
    storage_halt.clear_by_user()
    assert not storage_halt.active() and m.pending()
    assert read_json(m.JOURNAL)['scroll_uid']==99


def test_market_start_resumes_before_city_and_hunt(monkeypatch):
    from conquest import overnight,city_travel,storage_overflow
    events=[]
    monkeypatch.setattr(m,'pending',lambda:True)
    monkeypatch.setattr(m,'resume',lambda loop:events.append('return Phoenix'))
    monkeypatch.setattr(storage_overflow,'pending',lambda:False)
    monkeypatch.setattr(banking,'close_warehouse',lambda loop:events.append('close bank'))
    monkeypatch.setattr(city_travel,'ensure_city_visit',lambda loop:events.append('town check'))
    monkeypatch.setattr(overnight,'request',lambda *a:None)
    def hunt():
        events.append('hunt');raise overnight.OvernightStopped('test finished')
    loop=NS(check_stop=lambda:None,refresh=lambda:None,record=lambda *a,**kw:None,
        stop_farm=lambda:events.append('pause'),prepare_supplies=lambda:events.append('supplies'),
        select_level_route=lambda:None,hunt=hunt,info=None)
    loop._run_route=lambda:overnight.OvernightLoop._run_route(loop)
    overnight.OvernightLoop.run(loop)
    assert events==['pause','return Phoenix','close bank','town check','supplies','hunt']


def test_consolidation_runs_even_with_space_and_reloads_inventory(route):
    loop,state,bag,local,market,events=route
    bag.append(item(100,150009))
    banking.stash_valuables(loop)
    assert state['map']==1011 and not bag and loop.overflow_bank_changed
    assert {i['uid'] for i in market}=={99,100}
    assert events.count(('warehouse-deposit',100))==1

def test_verified_return_does_not_walk_back_to_market_bank(route):
    loop,state,bag,local,market,events=route;state['map']=1036
    market.append(item(99,m.SCROLL))
    write_json(m.JOURNAL,{'phase':'returning','origin':1011,'scroll_uid':99,'market_verified_at':123})
    assert m.resume(loop)
    assert ('open',1036) not in events and not any(e[0]=='travel' for e in events)
    assert state['map']==1011


def test_verified_return_rebanks_new_carried_valuable(route):
    loop,state,bag,local,market,events=route;state['map']=1036
    market.append(item(99,m.SCROLL));bag.append(item(100))
    write_json(m.JOURNAL,{'phase':'returning','origin':1011,'scroll_uid':99,'market_verified_at':123})
    assert m.resume(loop)
    assert events.index(('warehouse-deposit',100))<events.index(('trip',1011))
