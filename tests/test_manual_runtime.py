from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from functools import partial
import json
import threading
import time
from types import SimpleNamespace as NS

import pytest

from conquest.capture import CaptureUnavailable
from conquest.merchants.controller import MerchantController
from conquest.merchants.coordination import InputCoordinator, install, input_scope
from conquest.merchants.journal import Journal
from conquest.merchants.manual_sessions import BindingMismatch, ManualSessionError, ManualSessionStore
from conquest.merchants.manual_recovery import consume_farmer, consume_merchant, settlement
from conquest.merchants.runtime import MerchantRuntime
from conquest.merchants.sales import observe


def item(uid=10, **changes):
    return dict(uid=uid,type_id=410009,plus=1,gem1=0,gem2=0,quantity=1,
                bound=False,name='SuperBlade',**changes)


def snapshot(at=100, **changes):
    value = dict(character='Dutch',character_uid=123,server='America',timestamp=at,
                 identity={'pid':7,'creation_time_100ns':9,'path':'C:/Game/ImConquer.exe'},
                 inventory=[item()],booth=[item(11,price=100)],silver=1000,capacity=40,
                 booth_open=True,own_booth_uid=123,hp=100,map_id=1036,position=[210,210],
                 request={'participant':'Visitor','participant_uid':44,
                          'message':'Visitor wishes to trade with you.'},trade=None)
    value.update(changes)
    return value


@pytest.fixture
def rig(tmp_path,monkeypatch):
    x=NS(now=100,state=snapshot(),calls=[])
    monkeypatch.setattr(time,'time',lambda:x.now)
    x.journal=Journal(tmp_path/'journal.sqlite3')
    x.guard=InputCoordinator(lambda:True,path=tmp_path/'input.lock')
    x.runtime=MerchantRuntime(object(),x.guard,journal=x.journal,market_path=tmp_path/'market.json')
    for refill in x.runtime.refills.values():refill.clock=lambda:x.now
    x.read=lambda:{**deepcopy(x.state),'timestamp':x.now}
    x.driver=NS(read=x.read,require_qualified=lambda name:None,memory=NS(read=lambda **kw:x.read()))
    x.controller=MerchantController('Dutch',x.journal,x.driver,x.guard)
    x.controller.accept_request=lambda *a:x.calls.append('accept_request')
    x.controller.accept_delivery=lambda *a:x.calls.append('accept_delivery')
    x.controller.apply_price=lambda *a:x.calls.append('list')
    x.runtime.controllers['Dutch']=x.controller
    x.runtime.observers['Dutch']=NS(lock=threading.RLock(),
        adapter=NS(identity=x.state['identity'],assert_identity=lambda:None))
    x.runtime.disconnected=lambda character:False
    for character in ('Dutch','Spiritual'):
        x.journal.set(character,'enabled',True)
        x.journal.set(character,'refill_enabled',False)
    x.guard.owner_allowed=lambda character:character=='Farmer' or x.runtime.input_allowed(character)
    install(x.guard)
    yield x
    install(None)


def approve(x):
    x.runtime.step('Dutch')
    row=x.runtime.manual_status('Dutch')
    x.now+=1
    return x.runtime.approve_manual(row['approval_binding'],operator='Floor')


def settle(x,**changes):
    x.state.update(request=None,trade=None,**changes)
    x.now+=1;x.runtime.step('Dutch')
    x.now+=5;x.runtime.step('Dutch')


def test_unknown_request_is_durable_target_only_pause(rig):
    x=rig;x.runtime.step('Dutch')
    row=x.runtime.manual_status('Dutch')
    assert row['phase']=='approval_pending' and row['deadline']==130
    assert row['fence_scope']=='target' and not row['ever_approved']
    assert x.journal.get('Dutch','enabled') and not x.calls
    with input_scope():pass
    with x.guard.lease('Spiritual',purpose='trade'):pass
    with pytest.raises(CaptureUnavailable,match='Manual visitor'):
        with x.guard.lease('Dutch',purpose='trade'):pytest.fail('Pending target cannot take input')
    x.now=110;x.runtime.step('Dutch')
    assert x.runtime.manual_status('Dutch')['approval_binding']==row['approval_binding']
    assert x.runtime.manual_status('Dutch')['deadline']==130
    json.dumps(x.runtime.manual_status())


@pytest.mark.parametrize('window',['request','trade','closed'])
def test_approved_session_never_calls_trade_or_listing_and_fences_farmer(rig,window):
    x=rig;row=approve(x)
    assert row['phase']=='manual_active'
    x.now+=1
    if window!='request':x.state['request']=None
    if window=='trade':x.state['trade']={'participant':'Visitor','participant_uid':44,'own_silver':99,'other_silver':12}
    x.runtime.step('Dutch')
    assert not x.calls and not x.journal.pending('Dutch')
    assert x.runtime.manual_status('Dutch')['fence_scope']=='global'
    for character in ('Dutch','Spiritual'):
        with pytest.raises(CaptureUnavailable,match='Manual visitor'):
            with x.guard.lease(character,purpose='trade'):pytest.fail('Manual input is exclusive')
    with pytest.raises(CaptureUnavailable,match='Manual visitor'):
        with input_scope():pytest.fail('Farmer cannot take manual input')


def test_activation_never_focuses_or_changes_enablement(rig):
    x=rig;x.runtime.enable('Dutch',False)
    x.guard.on_acquire=lambda character:pytest.fail('Approval is memory only')
    x.guard.stop()
    row=approve(x)
    assert row['phase']=='manual_active' and x.guard.stopped
    assert not x.journal.get('Dutch','enabled')
    settle(x)
    assert x.guard.stopped and not x.journal.get('Dutch','enabled')
    with pytest.raises(CaptureUnavailable,match='stopped'):
        with input_scope():pass


def test_later_pause_and_mouse_ownership_survive_settlement(rig):
    x=rig;approve(x);x.runtime.enable('Dutch',False)
    x.guard.manual_active=lambda:True
    settle(x)
    assert not x.runtime.manual_status('Dutch') and not x.runtime.enabled('Dutch')
    with pytest.raises(CaptureUnavailable,match='manual input'):
        with input_scope():pass


def test_exact_permission_auto_activates_but_does_not_grant_delivery_trust(rig):
    x=rig;approve(x);settle(x)
    x.now+=1;x.state['request']=snapshot()['request'];x.runtime.step('Dutch')
    assert x.runtime.manual_status('Dutch')['phase']=='manual_active' and not x.calls
    from conquest.character_context import trusted_delivery
    assert not trusted_delivery('Dutch','Visitor',44)


def test_revocation_never_releases_active_fence_or_cancels_later_request(rig,monkeypatch):
    x=rig;row=approve(x)
    x.runtime.revoke_manual(row['visitor'],operator='Floor')
    assert x.guard.manual_session_blocked()
    x.state['request']=None;x.now+=1;x.runtime.step('Dutch')
    x.state['request']=snapshot()['request'];x.now+=1;x.runtime.step('Dutch')
    pending=x.runtime.manual_status('Dutch')
    assert pending['phase']=='approval_pending' and pending['ever_approved']
    x.runtime.reject_manual(pending['approval_binding'])
    monkeypatch.setattr('conquest.merchants.unrelated_request.decline_unrelated_request',
                        lambda *a,**kw:pytest.fail('Approved interval must never send Cancel'))
    x.now+=1;x.runtime.step('Dutch');assert x.guard.manual_session_blocked()


def test_changed_request_at_decline_boundary_records_no_claim_or_input(rig,tmp_path,monkeypatch):
    from test_unrelated_request import rig as native_rig,patches
    from conquest.merchants.unrelated_request import decline_unrelated_request
    x=rig;x.runtime.step('Dutch');row=x.runtime.manual_status('Dutch')
    x.runtime.reject_manual(row['approval_binding'])
    before=x.read();controller,_=native_rig(tmp_path,before,{**before,'request':None});controller.journal=x.journal
    def changed(target,a,b,size,**kw):
        before['request']={**before['request'],'participant_uid':45}
        kw['before_press']()
        pytest.fail('Changed request must not receive input')
    patches(monkeypatch,changed)
    with pytest.raises(ValueError,match='changed'):
        decline_unrelated_request(controller,x.read(),operations_enabled=True,
                                  manual_session=(x.runtime.manual_sessions,row['id']))
    with x.journal.db() as db:assert db.execute('SELECT COUNT(*) FROM manual_decline_claims').fetchone()[0]==0


@pytest.mark.parametrize('field',['participant','participant_uid','message'])
def test_request_change_cannot_use_displayed_binding(rig,field):
    x=rig;x.runtime.step('Dutch');binding=x.runtime.manual_status('Dutch')['approval_binding']
    x.now+=1;x.state['request'][field]=(
        'Different visitor' if field=='participant' else 45 if field=='participant_uid' else 'Changed prompt')
    with pytest.raises(ManualSessionError):x.runtime.approve_manual(binding)
    assert not x.runtime.manual_sessions.permissions()
    x.runtime.step('Dutch')
    assert x.runtime.manual_status('Dutch')['phase']=='needs_attention' and not x.calls


@pytest.mark.parametrize('field,value',[('pid',8),('path','C:/Other/ImConquer.exe')])
def test_process_change_cannot_use_displayed_manual_approval(rig,field,value):
    """A displayed request is bound to the exact process, not merely the visitor."""
    x=rig;x.runtime.step('Dutch');binding=x.runtime.manual_status('Dutch')['approval_binding']
    x.now+=1;x.state['identity'][field]=value
    with pytest.raises(ManualSessionError):x.runtime.approve_manual(binding)
    assert not x.runtime.manual_sessions.permissions() and not x.calls
    x.runtime.process_manual('Dutch',x.read())
    assert x.runtime.manual_status('Dutch')['phase']=='needs_attention'


def test_unresolved_bot_transaction_blocks_manual_admission_even_if_reconcile_returns(rig):
    x=rig;x.journal.begin('unresolved','Dutch','listing',{'uid':99})
    x.controller.reconcile=lambda s:None
    x.runtime.step('Dutch')
    assert not x.runtime.manual_status('Dutch') and not x.calls


def test_reservation_takes_priority_over_allowlisted_visitor(rig,monkeypatch):
    x=rig;approve(x);settle(x);x.now+=1;x.state['request']=snapshot()['request']
    monkeypatch.setattr('conquest.merchants.delivery_reservation.active',lambda *a:{'request_id':'batch'})
    x.runtime.step('Dutch')
    assert x.calls==['accept_request'] and not x.runtime.manual_status('Dutch')


def test_existing_bot_open_trade_is_not_quarantined(rig):
    x=rig;x.state.update(request=None,trade={'participant':'Parasite','participant_uid':55})
    x.journal.set('Dutch','accepted_request',dict(identity=x.state['identity'],participant_uid=55,opened_at=x.now))
    x.runtime.step('Dutch')
    assert x.calls==['accept_delivery'] and not x.runtime.manual_status('Dutch')


def test_reservation_cannot_break_an_existing_approved_fence(rig,monkeypatch):
    x=rig;approve(x)
    monkeypatch.setattr('conquest.merchants.delivery_reservation.active',lambda *a:{'request_id':'batch'})
    x.now+=1;x.runtime.step('Dutch')
    assert not x.calls and x.runtime.manual_status('Dutch')['ever_approved']


def test_unapproved_open_trade_is_quarantined(rig):
    x=rig;x.state.update(request=None,trade={'participant':'Visitor','participant_uid':44})
    x.runtime.step('Dutch')
    assert x.runtime.manual_status('Dutch')['phase']=='needs_attention' and not x.calls


def test_incomplete_first_memory_is_durable_target_hold(rig):
    x=rig;x.state['request']['participant_uid']=None;x.runtime.step('Dutch')
    row=x.runtime.manual_status('Dutch')
    assert row['phase']=='needs_attention' and row['visitor'] is None and row['approval_binding'] is None
    restarted=MerchantRuntime(object(),InputCoordinator(),journal=x.journal)
    assert restarted.manual_status('Dutch')['id']==row['id']
    assert restarted.coordinator.manual_session_blocked('Dutch')


@pytest.mark.parametrize('fault',['same_process','rollover','missing'])
def test_restart_observes_without_input_and_evidence_failures_stay_held(rig,fault):
    x=rig;row=approve(x)
    restarted=MerchantRuntime(object(),InputCoordinator(),journal=x.journal)
    assert restarted.coordinator.manual_session_blocked()
    x.runtime=restarted
    x.now+=1
    current=x.read()
    if fault=='rollover':current['identity']['creation_time_100ns']+=1
    if fault=='missing':current.pop('silver')
    restarted.process_manual('Dutch',current)
    expected='manual_active' if fault=='same_process' else 'needs_attention'
    assert restarted.manual_status('Dutch')['phase']==expected
    if fault!='same_process':
        x.now+=1;restarted.process_manual('Dutch',x.read())
        assert restarted.manual_status('Dutch')['phase']=='needs_attention'
    assert not x.calls


def test_approval_timeout_race_is_serialized_and_never_claims_input(rig):
    x=rig;x.runtime.step('Dutch');row=x.runtime.manual_status('Dutch');x.now=130
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=[pool.submit(x.runtime.approve_manual,row['approval_binding']),
                 pool.submit(x.runtime.reject_manual,row['approval_binding'])]
        for result in results:
            try:result.result()
            except BindingMismatch:pass
    assert x.runtime.manual_status('Dutch')['request_state']=='decline_pending'
    assert not x.runtime.manual_sessions.permissions() and not x.calls
    with x.journal.db() as db:
        assert db.execute('SELECT COUNT(*) FROM manual_declines').fetchone()[0]==1
        assert db.execute('SELECT COUNT(*) FROM manual_decline_claims').fetchone()[0]==0


def test_decline_claim_only_at_native_boundary_and_never_replayed(rig,tmp_path,monkeypatch):
    from test_unrelated_request import rig as native_rig,patches
    from conquest.merchants.unrelated_request import decline_unrelated_request
    x=rig;x.runtime.step('Dutch');row=x.runtime.manual_status('Dutch');x.runtime.reject_manual(row['approval_binding'])
    before=x.read();after={**before,'request':None}
    controller,_=native_rig(tmp_path,before,after);controller.journal=x.journal
    attempts=[]
    def lost(target,a,b,size,**kw):
        with x.journal.db() as db:assert db.execute('SELECT COUNT(*) FROM manual_decline_claims').fetchone()[0]==0
        kw['before_press']();attempts.append(1);raise OSError('uncertain input')
    patches(monkeypatch,lost)
    with pytest.raises(OSError,match='uncertain input'):
        decline_unrelated_request(controller,before,operations_enabled=True,manual_session=(x.runtime.manual_sessions,row['id']))
    assert x.runtime.manual_sessions.get(row['id'])['request_state']=='decline_claimed'
    x.now+=1;x.runtime.step('Dutch')
    assert attempts==[1] and not x.calls
    settle(x)
    assert x.runtime.manual_sessions.get(row['id'])['phase']=='declined_verified'


@pytest.mark.parametrize('change',['unchanged','add','modify','remove','booth_to_bag'])
def test_atomic_settlement_excludes_false_sales_and_only_marks_real_new_stock(rig,change):
    x=rig;observe(x.journal,x.read());approve(x)
    x.state.update(request=None,booth=[],silver=1097)
    if change=='add':x.state['inventory'].append(item(12))
    if change=='modify':x.state['inventory'][0]['quantity']=2
    if change=='remove':x.state['inventory']=[]
    if change=='booth_to_bag':x.state['inventory'].append(item(11))
    x.now+=1;observe(x.journal,x.read());x.runtime.step('Dutch')
    x.now+=5;x.runtime.step('Dutch')
    assert not x.runtime.manual_status('Dutch')
    with x.journal.db() as db:
        assert db.execute('SELECT COUNT(*) FROM sales').fetchone()[0]==0
        assert db.execute('SELECT COUNT(*) FROM transactions').fetchone()[0]==0
        assert db.execute("SELECT COUNT(*) FROM events WHERE event='sales_observation_gap'").fetchone()[0]==1
        baseline=json.loads(db.execute('SELECT snapshot FROM sales_baseline').fetchone()[0])
        assert baseline['silver']==1097 and '_sales_anchor' not in baseline
        signal=db.execute('SELECT * FROM manual_replans').fetchone()
        assert signal['merchant_pending']==1 and signal['farmer_pending']==0
    assert x.journal.get('Dutch','new_stock',False)==(change in ('add','modify'))
    # Sales observing after settlement can advance again, with no manual receipt.
    x.now+=1;observe(x.journal,x.read())
    with x.journal.db() as db:
        assert json.loads(db.execute('SELECT snapshot FROM sales_baseline').fetchone()[0])['timestamp']==x.now


def test_settlement_hook_failure_rolls_back_baseline_gap_and_terminal(rig):
    x=rig;row=approve(x);x.state['request']=None;x.now+=1;x.runtime.step('Dutch')
    callback=x.runtime.manual_sessions.on_settlement
    def fail(*args):callback(*args);raise RuntimeError('injected commit failure')
    x.runtime.manual_sessions.on_settlement=fail;x.now+=5
    with pytest.raises(RuntimeError):x.runtime.step('Dutch')
    assert x.runtime.manual_sessions.get(row['id'])['phase']=='settlement_observed'
    assert x.guard.manual_session_blocked()
    with x.journal.db() as db:
        assert db.execute('SELECT COUNT(*) FROM sales_baseline').fetchone()[0]==0
        assert db.execute('SELECT COUNT(*) FROM manual_replans').fetchone()[0]==0
        assert db.execute("SELECT COUNT(*) FROM events WHERE event='sales_observation_gap'").fetchone()[0]==0
    x.runtime.manual_sessions.on_settlement=callback;x.now+=1;x.runtime.step('Dutch')
    assert x.runtime.manual_sessions.get(row['id'])['phase']=='completed'


def test_merchant_replan_uses_fresh_memory_and_preserves_refill_pause(rig):
    x=rig;approve(x);settle(x)
    assert not consume_merchant(x.journal,'Dutch',x.read())
    x.now+=1;x.runtime.enable('Dutch',False)
    assert consume_merchant(x.journal,'Dutch',x.read())
    assert x.runtime.refills['Dutch'].due()
    assert not x.runtime.refill_enabled('Dutch') and not x.runtime.enabled('Dutch')
    assert not consume_farmer(x.journal,dict(timestamp=x.now,map_id=1002,inventory=[],urgent_banking=False))
    x.runtime.step('Dutch');assert not x.calls


def test_farmer_signal_requires_fresh_current_map_inventory_and_valuables(rig):
    x=rig
    store=ManualSessionStore(x.journal.path,on_settlement=partial(settlement,target_role='Farmer'))
    row=store.begin_request('farmer-profile',snapshot(),now=100)
    store.allow_and_activate(row['approval_binding'],snapshot(101),operator='Floor',now=101)
    store.observe(row['id'],snapshot(102,request=None),now=102)
    store.observe(row['id'],snapshot(107,request=None),now=107)
    with pytest.raises(ValueError):consume_farmer(x.journal,{'timestamp':108},now=108)
    evidence=dict(timestamp=108,map_id=1002,inventory=[{'uid':10}],urgent_banking=True)
    assert consume_farmer(x.journal,evidence,now=108)==[row['id']]
    assert consume_farmer(x.journal,evidence,now=108)==[]
    with x.journal.db() as db:
        saved=db.execute('SELECT * FROM manual_replans').fetchone()
        assert saved['merchant_pending']==0 and json.loads(saved['farmer_evidence'])==evidence


def test_override_rebaseline_survives_restart_and_requires_two_fresh_equal_samples(rig):
    x=rig;observe(x.journal,x.read());row=approve(x)
    x.runtime.override_manual(row['id'],confirmation_reference='checked-1',operator='Floor',reason='Manual inspection')
    hold=x.runtime.manual_status('Dutch')
    assert hold['rebaseline'] and hold['fence_scope']=='target'
    assert not x.guard.manual_session_blocked() and x.guard.manual_session_blocked('Dutch')
    x.state.update(request=None,booth=[],silver=1097)
    x.now+=1;x.runtime.step('Dutch');observe(x.journal,x.read())
    first=x.now
    restarted=MerchantRuntime(object(),InputCoordinator(),journal=x.journal)
    assert restarted.manual_status('Dutch')['stable_since']==first
    x.now+=4;restarted.process_manual('Dutch',x.read());assert restarted.manual_status('Dutch')
    x.now+=1;restarted.process_manual('Dutch',x.read())
    assert restarted.manual_status('Dutch') is None
    # Replaying the domain disposition cannot create a second baseline job.
    restarted.override_manual(row['id'],confirmation_reference='checked-1',operator='Floor',reason='Manual inspection')
    assert restarted.manual_status('Dutch') is None
    x.now+=1;observe(x.journal,x.read())
    with x.journal.db() as db:
        assert db.execute('SELECT COUNT(*) FROM sales').fetchone()[0]==0
        assert db.execute("SELECT COUNT(*) FROM events WHERE event='sales_observation_gap'").fetchone()[0]==1
        assert db.execute('SELECT COUNT(*) FROM manual_replans').fetchone()[0]==1
        assert json.loads(db.execute('SELECT snapshot FROM sales_baseline').fetchone()[0])['timestamp']==x.now


@pytest.mark.parametrize('fault',['rollover','missing','open_window','ownership_change'])
def test_override_baseline_cannot_settle_bad_or_changing_evidence(rig,fault):
    x=rig;row=approve(x)
    x.runtime.override_manual(row['id'],confirmation_reference='checked',operator='Floor',reason='Explicit disposition')
    x.state['request']=None;x.now+=1;x.runtime.step('Dutch')
    first=x.now;x.now+=5
    if fault=='rollover':x.state['identity']['creation_time_100ns']+=1
    if fault=='missing':x.state.pop('silver')
    if fault=='open_window':x.state['request']=snapshot()['request']
    if fault=='ownership_change':x.state['silver']+=1
    x.runtime.step('Dutch')
    held=x.runtime.manual_status('Dutch')
    assert held and held['holds_automation']
    if fault in ('rollover','missing'):
        assert held['phase']=='needs_attention'
        x.state=snapshot(request=None);x.now+=10;x.runtime.step('Dutch')
        assert x.runtime.manual_status('Dutch')['phase']=='needs_attention'
    elif fault=='ownership_change':assert held['stable_since']>first
    else:assert held['stable_since'] is None
    with x.journal.db() as db:assert db.execute('SELECT COUNT(*) FROM sales_baseline').fetchone()[0]==0


def test_baseline_override_failure_and_retry_remain_target_scoped(rig):
    x=rig;x.state['request']['participant_uid']=None;x.runtime.step('Dutch')
    hold=x.runtime.manual_status('Dutch')
    x.runtime.override_manual(hold['id'],confirmation_reference='fixed-reader',operator='Floor',reason='Reader repaired')
    x.state=snapshot(request=None);x.now+=1;x.runtime.step('Dutch')
    x.state.pop('silver');x.now+=1;x.runtime.step('Dutch')
    held=x.runtime.manual_status('Dutch');assert held['phase']=='needs_attention'
    x.runtime.override_manual(held['id'],confirmation_reference='retry-reader',operator='Floor',reason='Rechecked reader')
    x.state=snapshot(request=None);x.now+=1;x.runtime.step('Dutch')
    since=x.runtime.manual_status('Dutch')['stable_since']
    x.runtime.override_manual(held['id'],confirmation_reference='retry-reader',operator='Floor',reason='Rechecked reader')
    assert x.runtime.manual_status('Dutch')['stable_since']==since
    x.now+=5;x.runtime.step('Dutch');assert not x.runtime.manual_status('Dutch')


def test_manual_market_observation_does_not_pause_or_disconnect_intent(rig):
    from conquest.merchants.market_guard import MarketGuard
    from conquest.merchants.recovery_safety import observe as recovery_observe
    x=rig;approve(x)
    guard=MarketGuard(x.runtime)
    observer=x.runtime.observers['Dutch'];observer.health_layout=object()
    guard.check('Dutch',observer,read=lambda *a:NS(map_id=1002,dead_candidate=False))
    assert x.journal.get('Dutch','enabled') and guard.observations['Dutch']['map_id']==1002
    x.journal.set('Dutch','recovery_safety',{'active':True,'last_progress':1})
    recovery_observe(x.runtime,'Dutch',x.state['identity'],close=lambda *a:pytest.fail('Manual session cannot disconnect'))
    assert x.journal.get('Dutch','enabled') and x.journal.get('Dutch','recovery_safety')['last_progress']==1


def test_manual_farmer_observer_keeps_life_reads_without_recovery_input(rig,monkeypatch):
    from test_native_farm import setup
    x=rig;approve(x)
    supervisor,control,life,_=setup(monkeypatch)
    supervisor.recovery.step=lambda *a:pytest.fail('Manual fence must precede recovery gameplay')
    before=control.snapshot()
    result=supervisor.observe()
    assert result['waiting'] and result['manual_session']
    assert supervisor.position==life.position and control.snapshot()==before
    control.update({'enabled':False})
    assert supervisor.observe()['stop']


def test_manual_alert_wait_is_quiet_and_never_confirms_old_incident(rig):
    from conquest.merchants.alerts import Alerts,condition
    x=rig;approve(x)
    state={'manual_session':x.runtime.manual_status('Dutch'),'manual_input_fence':True,'connected':True}
    alerts=Alerts();alerts.observe('Dutch',('Old failure',0),100)
    alerts.state['incidents']['Dutch']['sent']=True
    alerts.poll({'characters':{'Dutch':state}},120)
    assert alerts.state['incidents']['Dutch'].get('healthy_since') is None
    state['manual_session'].update(phase='needs_attention',reason='Memory missing')
    assert condition(state)==('Manual visitor session needs attention: Memory missing',0)
