import copy
import time
from types import SimpleNamespace as NS
import pytest
from conquest.discord_notify import write_json,read_json
from conquest.merchants import delivery_route as route


def item(uid,kind=130009):
    return dict(uid=uid,type_id=kind,plus=0,gem1=0,gem2=0,quantity=1,bound=False,slot=0)


def snapshot(name,uid,items=(),position=(10,10)):
    return dict(character=name,character_uid=uid,identity={'pid':uid,'creation':1},server='America',
        timestamp=time.time(),map_id=1036,hp=100,silver=100,capacity=40,inventory=list(items),booth=[],
        position=list(position),booth_open=True,trade=None,request=None)


@pytest.fixture
def rig(monkeypatch,tmp_path):
    from conquest import safe_reload
    from conquest.merchants import handoff
    from conquest.merchants import service_visit
    original_visit=service_visit.MarketVisit
    monkeypatch.setattr(service_visit,'MarketVisit',lambda:original_visit(tmp_path/'visit.json'))
    monkeypatch.setattr(safe_reload,'clear_observation',lambda h:True)
    monkeypatch.setattr(route,'WorkWindows',lambda:handoff.WorkWindows(tmp_path/'windows.json'))
    write_json(route.POLICY,{'enabled':True,'parity_verified':True})
    f=snapshot('Parasite',1,[item(100),item(101),item(102,2000031),item(103,1050002)])
    d=snapshot('Dutch',2,[item(i) for i in range(200,239)],(20,10))
    s=snapshot('Spiritual',3,[item(i) for i in range(300,339)],(30,10))
    events=[];receipts={};health={'target':{'pid':1},'embedded_controls':{
        'life':{'map_id':1036},'control':{'enabled':False,'revision':1}}}
    loop=NS(health=lambda:copy.deepcopy(health),living=lambda:copy.deepcopy(health),
        terrain=NS(travel_path=lambda a,b:[a,b],walkable=lambda p:p[0]>=0 and p[1]>=0),check_stop=lambda:None,
        town=lambda action,**fields:events.append((action,fields)),
        focus=lambda h:events.append(('focus',None)),
        record=lambda event,**fields:events.append((event,fields)))
    def travel(p,**fields):
        # Match the real OvernightLoop contract rather than accepting any radius.
        assert type(fields.get('arrival_radius')) is int
        assert 0 <= fields['arrival_radius'] <= 2
        f['position']=list(p);events.append(('travel',list(p)))
    loop.travel=travel
    def send(body):
        events.append((body['action'],copy.deepcopy(body)))
        action=body['action']
        for snap in (f,d,s):snap['timestamp']=time.time()
        if action=='delivery-readiness':return {'qualified':True}
        if action=='delivery-target':
            from conquest.viewport import clear_scene
            m=d if body['character']=='Dutch' else s
            dx,dy=m['position'][0]-f['position'][0],m['position'][1]-f['position'][1]
            point=[700+(dx-dy)*32,350+(dx+dy)*16]
            viewport=[1416,850]
            return {'ready':max(abs(dx),abs(dy))<=12 and clear_scene(point,viewport),
                    'farmer_position':f['position'],'merchant_position':m['position'],
                    'point':point,'viewport':viewport,'reason':'outside_client','occupied_tiles':[]}
        if action=='status':return {'characters':{m['character']:{'ready':True} for m in (d,s)}}
        if action=='delivery-pair':return {'farmer':copy.deepcopy(f),'merchant':copy.deepcopy(d if body['character']=='Dutch' else s)}
        if action=='delivery-start':
            key=body['request_id']
            if key not in receipts:
                # Assert route intent is durable before the source command.
                assert read_json(route.STATE)['active']['request_id']==key
                target=d if body['character']=='Dutch' else s
                moved=[i for i in f['inventory'] if i['uid'] in body['uids']]
                target['inventory']+=moved
                f['inventory']=[i for i in f['inventory'] if i not in moved]
                receipts[key]={'request_id':key,'phase':'verified','character':body['character'],'uids':body['uids'],
                               'items':moved,'delivered':moved,'remaining':[],
                               'outcome':'transferred','next_action':'release_route','proof_digest':'test-proof'}
                saved=read_json(route.STATE)['active']
                receipts[key].update({k:saved.get(k) for k in ('visit_id','town_visit_id','farmer_profile_id')})
            return {'running':False,'receipt':receipts[key]}
        if action=='delivery-status':return {'running':False,'receipt':receipts.get(body['request_id'])}
        if action=='handoff-release':return {'released':True}
        return {}
    return NS(loop=loop,send=send,f=f,d=d,s=s,events=events,receipts=receipts,health=health,
              visit_path=tmp_path/'visit.json')


def test_disabled_storage_does_not_inspect_or_move(rig):
    write_json(route.POLICY,{'enabled':False})
    assert route.market_storage(rig.loop,send=rig.send)==[]
    assert rig.events==[]


def test_split_delivery_rechecks_capacity_preserves_star_and_ammo(rig):
    result=route.market_storage(rig.loop,send=rig.send)
    assert [r['merchant'] for r in result]==['Dutch','Spiritual']
    assert [i['uid'] for i in rig.f['inventory']]==[102,103]
    assert len(rig.d['inventory'])==len(rig.s['inventory'])==40
    assert route.receipt_for(100,130009) and not route.receipt_for(100,2000031)
    assert not read_json(route.STATE)['active']
    assert len([e for e,_ in rig.events if e=='handoff-release'])==2


def test_full_merchants_do_not_cause_travel(rig):
    rig.d['inventory'].append(item(900));rig.s['inventory'].append(item(901))
    assert route.market_storage(rig.loop,send=rig.send)==[]
    assert not any(e=='travel' for e,_ in rig.events)


def test_unreachable_first_merchant_defers_to_second_without_repeated_approach(rig,monkeypatch):
    visits=[]
    original=route.approach_merchant
    def approach(loop,plan,send,**kw):
        visits.append(plan['merchant'])
        return False if plan['merchant']=='Dutch' else original(loop,plan,send,**kw)
    monkeypatch.setattr(route,'approach_merchant',approach)
    result=route.market_storage(rig.loop,send=rig.send)
    assert [r['merchant'] for r in result]==['Spiritual']
    assert visits==['Dutch','Spiritual']
    assert [i['uid'] for i in rig.f['inventory']]==[101,102,103]


def test_full_warehouse_can_continue_with_no_loot_and_ready_merchant_capacity(rig):
    rig.f['inventory']=[]
    assert not route.warehouse_exhausted(rig.loop,{'items':[{}],'capacity':1},[],send=rig.send)
    assert not any(e in ('travel','delivery-start','handoff-grant') for e,_ in rig.events)


@pytest.mark.parametrize('kind',[2000031,2000032,2000033,2000034,2000035,2000036,2000037,2000038,1088000,130009])
def test_remaining_valuable_cannot_be_excused_by_other_merchant_capacity(rig,kind):
    assert route.warehouse_exhausted(rig.loop,{'items':[{}],'capacity':1},[item(900,kind)],send=rig.send)
    assert not rig.events


@pytest.mark.parametrize('reason',['disabled','unqualified','full','disconnected','stale','blocked_path','active_trade','wrong_character'])
def test_full_bank_continuation_requires_current_qualified_reachable_capacity(rig,reason):
    rig.f['inventory']=[]
    if reason=='disabled':write_json(route.POLICY,{'enabled':False})
    if reason=='full':
        rig.d['inventory'].append(item(900));rig.s['inventory'].append(item(901))
    if reason=='blocked_path':rig.loop.terrain.travel_path=lambda *a:[]
    def send(body):
        result=rig.send(body)
        if reason=='unqualified' and body['action']=='delivery-readiness':result['qualified']=False
        if reason=='disconnected' and body['action']=='status':result['characters']={}
        if body['action']=='delivery-pair':
            if reason=='stale':result['merchant']['timestamp']-=6
            if reason=='active_trade':result['farmer']['trade']={'participant':'Dutch'}
            if reason=='wrong_character':result['merchant']['character']='Stranger'
        return result
    assert route.warehouse_exhausted(rig.loop,{'items':[{}],'capacity':1},[],send=send)
    assert not any(e in ('travel','delivery-start','handoff-grant') for e,_ in rig.events)


def test_nonfull_warehouse_does_not_trigger_additional_merchant_inspection(rig):
    assert not route.warehouse_exhausted(rig.loop,{'items':[],'capacity':1},[item(900)],send=rig.send)
    assert not rig.events


def test_capacity_changes_during_travel_are_replanned(rig):
    rig.d["position"]=[40,10]
    original=rig.loop.travel
    def travel(p,**kw):
        original(p,**kw)
        if len(rig.d['inventory'])<40:rig.d['inventory'].append(item(900))
    rig.loop.travel=travel
    result=route.market_storage(rig.loop,send=rig.send)
    assert [r['merchant'] for r in result]==['Spiritual']
    assert len(rig.f['inventory'])==3


def test_missing_readiness_uses_warehouse_without_travel(rig):
    def send(body):
        if body['action']=='delivery-readiness':return {'qualified':False}
        pytest.fail('Unqualified delivery inspected capacity or submitted input')
    assert route.market_storage(rig.loop,send=send)==[]


def test_lost_start_response_reconciles_natively_without_repeating_start(rig):
    def lost(body):
        result=rig.send(body)
        if body['action']=='delivery-start':raise OSError('response lost')
        return result
    assert len(route.market_storage(rig.loop,send=lost))==2
    assert not read_json(route.STATE)['active']
    assert len(rig.d['inventory'])==40
    assert route.receipt_for(100,130009)
    starts=[b['request_id'] for e,b in rig.events if e=='delivery-start']
    assert len(starts)==len(set(starts))==2


def test_unknown_submission_never_falls_through_to_warehouse(rig):
    write_json(route.STATE,{'active':{'request_id':'lost','merchant':'Dutch','items':[item(100)]}})
    write_json(route.POLICY,{'enabled':False})
    with pytest.raises(ValueError,match='no receipt'):route.market_storage(rig.loop,send=rig.send)
    assert not any(e in ('delivery-start','travel') for e,_ in rig.events)


def test_wrong_or_uncertain_receipt_prevents_fallback(rig):
    def send(body):
        result=rig.send(body)
        if body['action']=='delivery-status' and result['receipt']:
            result['receipt']={**result['receipt'],'uids':[999]}
        return result
    with pytest.raises(ValueError,match='reconciliation'):route.market_storage(rig.loop,send=send)
    assert read_json(route.STATE)['active']
    assert not any(e=='focus' for e,_ in rig.events)


def test_manual_revision_change_never_refocuses_or_continues(rig):
    from conquest.overnight import OvernightStopped
    def send(body):
        result=rig.send(body)
        if body['action']=='handoff-release':rig.health['embedded_controls']['control']['revision']+=1
        return result
    with pytest.raises(OvernightStopped):route.market_storage(rig.loop,send=send)
    assert not any(e=='focus' for e,_ in rig.events)


def test_delivery_window_respects_global_stop_and_independent_refill_permission():
    import threading
    from conquest.merchants.ui import UnifiedUI
    calls=[]
    runtime=NS(refill_enabled=lambda name:name=='Dutch',
               refills={'Dutch':NS(start=lambda:calls.append('Dutch'))},handoff=None)
    ui=NS(runtime=runtime,coordinator=NS(lock=threading.RLock(),owner=None,stopped=True))
    body={'action':'delivery-window','request_id':'test-window'}
    with pytest.raises(ValueError,match='unavailable'):UnifiedUI.dispatch(ui,body)
    assert not calls and runtime.handoff is None
    ui.coordinator.stopped=False
    assert UnifiedUI.dispatch(ui,body)=={'requested':'test-window'}
    assert runtime.handoff==runtime.delivery_window=='test-window'
    assert calls==[]  # Delivery has priority; refill begins only after reconciliation.


def test_delivery_window_retry_does_not_reset_refill_or_replace_another_window():
    import threading
    from conquest.merchants.ui import UnifiedUI
    calls=[]
    runtime=NS(refill_enabled=lambda name:True,
               refills={name:NS(start=lambda:calls.append('start')) for name in ('Dutch','Spiritual')},
               handoff='existing',delivery_window=None,refill_window='existing')
    ui=NS(runtime=runtime,grant=None,coordinator=NS(lock=threading.RLock(),owner=None,stopped=False))
    assert UnifiedUI.dispatch(ui,{'action':'delivery-window','request_id':'existing'})=={'requested':'existing'}
    assert runtime.refill_window=='existing' and runtime.delivery_window is None and not calls
    with pytest.raises(ValueError,match='Another'):
        UnifiedUI.dispatch(ui,{'action':'delivery-window','request_id':'other'})
    assert runtime.handoff=='existing' and not calls


def test_delivery_window_has_no_refill_write_and_remains_releasable():
    import threading
    from conquest.merchants.ui import UnifiedUI
    def fail():raise OSError('Journal write failed')
    finished=[]
    runtime=NS(refill_enabled=lambda name:True,
               refills={name:NS(start=fail) for name in ('Dutch','Spiritual')},handoff=None,
               finish_handoff=lambda:finished.append(True))
    ui=NS(runtime=runtime,grant=None,coordinator=NS(lock=threading.RLock(),owner=None,stopped=False))
    UnifiedUI.dispatch(ui,{'action':'delivery-window','request_id':'failed-window'})
    assert ui.grant is None and runtime.handoff==runtime.delivery_window=='failed-window'
    assert UnifiedUI.dispatch(ui,{'action':'handoff-release','request_id':'failed-window'})=={'released':True}
    assert runtime.handoff is runtime.delivery_window is runtime.refill_window is None
    assert finished==[True]


def test_remainder_refill_uses_original_deadline_only(rig,monkeypatch):
    now=[1000.0];calls=[]
    monkeypatch.setattr(route.time,'time',lambda:now[0])
    monkeypatch.setattr(route.time,'sleep',lambda seconds:now.__setitem__(0,now[0]+seconds))
    def send(body):
        calls.append(body['action'])
        if body['action']=='delivery-refill':return {'refill':True,'expires_at':1003}
        return {'characters':{'Dutch':{'refill':{'enabled':True,'pending':True}}}}
    proof={'target':rig.health['target']}
    assert route.refill_remainder(rig.loop,send,'batch',1003,proof,1)
    assert now[0]==1003 and calls.count('delivery-refill')==1
    before=list(calls)
    assert not route.refill_remainder(rig.loop,send,'batch',1003,proof,1)
    assert calls==before


def test_f11_prevents_refill_and_does_not_create_new_window(rig,monkeypatch):
    from conquest.overnight import OvernightStopped
    monkeypatch.setattr(route.ctypes.windll.user32,'GetAsyncKeyState',lambda key:0x8000)
    with pytest.raises(OvernightStopped,match='F11'):
        route.refill_remainder(rig.loop,lambda b:pytest.fail('No command after F11'),
                              'batch',time.time()+10,{'target':rig.health['target']},1)


@pytest.mark.parametrize('verified',[False,True])
def test_refill_phase_requires_both_delivery_receipts(tmp_path,monkeypatch,verified):
    from conquest.merchants.ui import UnifiedUI
    from conquest.merchants.coordination import InputCoordinator
    from conquest.merchants import delivery_operation as op
    monkeypatch.setattr(op,'Journal',lambda path:None)
    monkeypatch.setattr(op,'status',lambda journal,key:{'phase':'verified','character':'Dutch'})
    runtime=NS(delivery_window='batch',handoff='batch',journal=NS(get=lambda *a:{
        'request_id':'batch','phase':'verified' if verified else 'offer_ready'}),
        refill_enabled=lambda c:False)
    ui=NS(runtime=runtime,grant={'request_id':'batch','expires_at':1234},safe_to_yield=lambda:True,
          coordinator=InputCoordinator(lambda:True,path=tmp_path/'input.lock'))
    if verified:
        assert UnifiedUI.dispatch(ui,{'action':'delivery-refill','request_id':'batch'})=={'refill':True,'expires_at':1234}
        assert runtime.refill_window=='batch' and runtime.delivery_window is None
    else:
        with pytest.raises(ValueError,match='stock hold'):
            UnifiedUI.dispatch(ui,{'action':'delivery-refill','request_id':'batch'})
        assert runtime.delivery_window=='batch'


def test_merchant_already_in_trade_range_does_not_move(rig):
    rig.f['position']=[229,189];rig.d['position']=[232,193]
    route.approach_merchant(rig.loop,{'merchant':'Dutch','position':[232,193]},rig.send)
    assert not any(e=='travel' for e,_ in rig.events)


def test_actionable_target_at_thirteen_tiles_continues_to_checked_range(rig):
    rig.f['position']=[226,209];rig.d['position']=[239,213]
    def send(body):
        result=rig.send(body)
        if body['action']=='delivery-target':result['ready']=True
        return result
    assert route.approach_merchant(
        rig.loop,{'merchant':'Dutch','position':[239,213]},send)
    assert max(abs(a-b) for a,b in zip(rig.f['position'],rig.d['position']))<=12
    assert any(event=='travel' for event,_ in rig.events)


def test_actionable_out_of_range_target_fails_when_no_checked_tile_exists(rig):
    rig.f['position']=[226,209];rig.d['position']=[239,213]
    rig.loop.terrain.walkable=lambda point:False
    def send(body):
        result=rig.send(body)
        if body['action']=='delivery-target':result['ready']=True
        return result
    assert not route.approach_merchant(
        rig.loop,{'merchant':'Dutch','position':[239,213]},send)
    assert not any(event=='travel' for event,_ in rig.events)


def test_actionable_target_at_exactly_twelve_tiles_does_not_move(rig):
    rig.f['position']=[227,209];rig.d['position']=[239,213]
    def send(body):
        result=rig.send(body)
        if body['action']=='delivery-target':result['ready']=True
        return result
    assert route.approach_merchant(
        rig.loop,{'merchant':'Dutch','position':[239,213]},send)
    assert not any(event=='travel' for event,_ in rig.events)


def test_merchant_approach_requires_visible_reachable_tile(rig):
    rig.f['position']=[10,10];rig.d['position']=[40,10]
    assert route.approach_merchant(rig.loop,{'merchant':'Dutch','position':[40,10]},rig.send)
    assert rig.send({'action':'delivery-target','character':'Dutch'})['ready']
    assert rig.f['position']!=rig.d['position']


def test_absent_remote_recipient_uses_only_checked_ingress_before_fresh_target_probe(rig):
    from conquest.navigation import line_tiles
    rig.f['position']=[10,10];rig.d['position']=[50,10]
    rig.loop.terrain.travel_path=lambda a,b:line_tiles(a,b)
    def send(body):
        if body['action']=='delivery-target':
            distance=max(abs(a-b) for a,b in zip(rig.f['position'],rig.d['position']))
            if distance>12:
                rig.events.append(('delivery-target',copy.deepcopy(body)))
                return {'ready':False,'reason':'recipient_absent','point':None,
                        'farmer_position':list(rig.f['position']),
                        'merchant_position':list(rig.d['position']),
                        'viewport':[1416,850],'occupied_tiles':[list(rig.f['position'])]}
        return rig.send(body)
    assert route.approach_merchant(rig.loop,{'merchant':'Dutch','position':[50,10]},send)
    assert rig.f['position']==[46,10]
    assert len([1 for event,_ in rig.events if event=='travel'])==3


@pytest.mark.parametrize('stalled',[False,True])
def test_approach_applies_short_deadline_inside_travel_and_restores_outer_visit(rig,monkeypatch,stalled):
    from conquest.travel_progress import TravelStalled
    now=[1000.0];rig.loop.market_service_deadline=1060.0;rig.d['position']=[40,10]
    monkeypatch.setattr(route.time,'time',lambda:now[0])
    original=rig.loop.travel
    def travel(point,**fields):
        assert rig.loop.market_service_deadline==1015.0
        if stalled:raise TravelStalled('test stall')
        original(point,**fields)
    rig.loop.travel=travel
    result=route.approach_merchant(rig.loop,
        {'merchant':'Dutch','position':rig.d['position']},rig.send,deadline=1060.0)
    assert result is (not stalled)
    assert rig.loop.market_service_deadline==1060.0


def test_ambiguous_remote_recipient_is_hard_failure_without_movement(rig):
    def send(body):
        if body['action']=='delivery-target':raise ValueError('Receiver UID is ambiguous in the farmer scene')
        return rig.send(body)
    with pytest.raises(ValueError,match='ambiguous'):
        route.approach_merchant(rig.loop,{'merchant':'Dutch','position':rig.d['position']},send)
    assert not any(event=='travel' for event,_ in rig.events)


def test_slow_target_observation_cannot_authorize_after_approach_deadline(rig,monkeypatch):
    now=[1000.0];monkeypatch.setattr(route.time,'time',lambda:now[0])
    def send(body):
        result=rig.send(body)
        if body['action']=='delivery-target':now[0]=1016.0
        return result
    assert not route.approach_merchant(rig.loop,
        {'merchant':'Dutch','position':rig.d['position']},send,deadline=1060.0)
    assert not any(event=='travel' for event,_ in rig.events)


def test_service_window_covers_remote_ingress_before_recipient_is_actionable(rig):
    from conquest.navigation import line_tiles
    rig.f['position']=[10,10];rig.d['position']=[50,10]
    rig.loop.terrain.travel_path=lambda a,b:line_tiles(a,b)
    original_travel=rig.loop.travel
    def travel(point,**fields):
        visit=read_json(rig.visit_path)
        assert (visit['phase']=='active'
                and time.time()<rig.loop.market_service_deadline<=visit['deadline'])
        original_travel(point,**fields)
    rig.loop.travel=travel
    def send(body):
        if body['action']=='delivery-target':
            distance=max(abs(a-b) for a,b in zip(rig.f['position'],rig.d['position']))
            if distance>12:
                rig.events.append(('delivery-target',copy.deepcopy(body)))
                return {'ready':False,'reason':'recipient_absent','point':None,
                        'farmer_position':list(rig.f['position']),
                        'merchant_position':list(rig.d['position']),
                        'viewport':[1416,850],'occupied_tiles':[list(rig.f['position'])]}
        return rig.send(body)
    result=route.market_storage(rig.loop,send=send)
    visit=read_json(rig.visit_path)
    assert result and visit['phase']=='active' and rig.loop.market_service_deadline is None


def test_current_dutch_diagonal_in_range_is_not_clickable(rig):
    rig.f['position']=[252,221];rig.d['position']=[264,210]
    assert not rig.send({'action':'delivery-target','character':'Dutch'})['ready']
    assert route.approach_merchant(rig.loop,{'merchant':'Dutch','position':[264,210]},rig.send)
    assert rig.send({'action':'delivery-target','character':'Dutch'})['ready']
    assert any(e=='travel' for e,_ in rig.events)


def test_booth_stock_counts_against_delivery_capacity(rig):
    rig.d['inventory']=[item(i) for i in range(200,208)]
    rig.d['booth']=[item(i) for i in range(400,432)]
    rig.s['inventory']=[item(i) for i in range(300,307)]
    rig.s['booth']=[item(i) for i in range(500,532)]
    plans=route.candidates(rig.loop,rig.send)
    assert len(plans)==1 and plans[0]['merchant']=='Spiritual'
    assert len(plans[0]['items'])==1


def test_full_spiritual_uses_last_dutch_slot_for_dragonball_then_warehouse(rig):
    from conquest.merchants.delivery import plan_deliveries
    blade=item(100,410065);blade['plus']=1
    db=item(101,1088000)
    star=item(102,2000031)
    rig.f['inventory']=[blade,db,star]
    rig.s['inventory']=[item(i) for i in range(300,308)]
    rig.s['booth']=[item(i) for i in range(400,432)]
    rig.d['inventory']=[item(i) for i in range(500,507)]
    rig.d['booth']=[item(i) for i in range(600,632)]
    def plan():
        return plan_deliveries(rig.f,[{'character':m['character'],'ready':True,
            'snapshot':m,'verified_travel_distance':1} for m in (rig.s,rig.d)])
    proposed=plan()
    assert [(p['merchant'],[i['uid'] for i in p['items']]) for p in proposed['deliveries']]==[('Dutch',[101])]
    assert {i['uid'] for i in proposed['warehouse']}=={100,102}
    result=route.market_storage(rig.loop,send=rig.send)
    assert [r['merchant'] for r in result]==['Dutch']
    assert [i['uid'] for i in result[0]['items']]==[101]
    remaining=plan()
    assert remaining['deliveries']==[]
    assert {i['uid'] for i in remaining['warehouse']}=={100,102}
    assert sum(e=='delivery-start' for e,_ in rig.events)==1
