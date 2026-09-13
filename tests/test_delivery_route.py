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
    monkeypatch.setattr(safe_reload,'clear_observation',lambda h:True)
    monkeypatch.setattr(route,'WorkWindows',lambda:handoff.WorkWindows(tmp_path/'windows.json'))
    write_json(route.POLICY,{'enabled':True,'parity_verified':True})
    f=snapshot('Parasite',1,[item(100),item(101),item(102,2000031),item(103,1050002)])
    d=snapshot('Dutch',2,[item(i) for i in range(200,239)],(20,10))
    s=snapshot('Spiritual',3,[item(i) for i in range(300,339)],(30,10))
    events=[];receipts={};health={'target':{'pid':1},'embedded_controls':{
        'life':{'map_id':1036},'control':{'enabled':False,'revision':1}}}
    loop=NS(health=lambda:copy.deepcopy(health),living=lambda:copy.deepcopy(health),
        terrain=NS(travel_path=lambda a,b:[a,b]),check_stop=lambda:None,
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
                receipts[key]={'request_id':key,'phase':'verified','character':body['character'],'uids':body['uids']}
            return {'running':False,'receipt':receipts[key]}
        if action=='delivery-status':return {'running':False,'receipt':receipts.get(body['request_id'])}
        if action=='handoff-release':return {'released':True}
        return {}
    return NS(loop=loop,send=send,f=f,d=d,s=s,events=events,receipts=receipts,health=health)


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


def test_lost_start_response_preserves_journal_and_restart_reconciles(rig):
    def lost(body):
        result=rig.send(body)
        if body['action']=='delivery-start':raise OSError('response lost')
        return result
    with pytest.raises(OSError):route.market_storage(rig.loop,send=lost)
    key=read_json(route.STATE)['active']['request_id']
    assert key in rig.receipts
    write_json(route.POLICY,{'enabled':False})
    route.market_storage(rig.loop,send=rig.send)
    assert not read_json(route.STATE)['active']
    assert len(rig.d['inventory'])==40
    assert route.receipt_for(100,130009)


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
    assert calls==['Dutch']


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


def test_failed_delivery_timer_write_leaves_exact_window_releasable():
    import threading
    from conquest.merchants.ui import UnifiedUI
    def fail():raise OSError('Journal write failed')
    finished=[]
    runtime=NS(refill_enabled=lambda name:True,
               refills={name:NS(start=fail) for name in ('Dutch','Spiritual')},handoff=None,
               finish_handoff=lambda:finished.append(True))
    ui=NS(runtime=runtime,grant=None,coordinator=NS(lock=threading.RLock(),owner=None,stopped=False))
    with pytest.raises(OSError,match='Journal'):
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
        'request_id':'batch','phase':'verified' if verified else 'offer_ready'}))
    ui=NS(runtime=runtime,grant={'request_id':'batch','expires_at':1234},safe_to_yield=lambda:True,
          coordinator=InputCoordinator(lambda:True,path=tmp_path/'input.lock'))
    if verified:
        assert UnifiedUI.dispatch(ui,{'action':'delivery-refill','request_id':'batch'})=={'refill':True,'expires_at':1234}
        assert runtime.refill_window=='batch' and runtime.delivery_window is None
    else:
        with pytest.raises(ValueError,match='stock hold'):
            UnifiedUI.dispatch(ui,{'action':'delivery-refill','request_id':'batch'})
        assert runtime.delivery_window=='batch'
