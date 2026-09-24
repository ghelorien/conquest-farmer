"""Synthetic regression only; no game process, input or live journals."""
from contextlib import contextmanager, nullcontext
from copy import deepcopy
import threading
from types import SimpleNamespace as NS

import pytest

from conquest.merchants import handoff, listing_handoff_1078 as scopes
from conquest.merchants import booth_listing_once_1078 as listing, refill_1078
from conquest.merchants.journal import Journal
from conquest.merchants.refill import RefillSchedule
from conquest.merchants.runtime import MerchantRuntime
from test_merchant_listing_capability_1078 import receipt


def progress_status():
    identity={'pid':7,'creation_time_100ns':8,'path':'client.exe'}
    return {'handoff_requested':'merchant-refill:Dutch:123', 'characters':{'Dutch':{
        'profile_id':'dutch-profile','connected':True,'pending':[], 'needs_attention':None,
        'qualification':{'foreground_open_booth_listing_1078':True},
        'refill':{'enabled':True,'pending':True,'cursor':[2,3],'deferred':1,
            'listing1078_engine':1,'attempt_started_at':900,
            'last_verified_listing':{'request_id':'booth-list1078-refill-proof',
                'verified_at':940,'character':'Dutch','profile_id':'dutch-profile',
                'character_uid':9,'identity':identity}},
        'snapshot':{'identity':identity,'character_uid':9,'timestamp':1000,
            'inventory':[{'uid':2},{'uid':3}],'booth':[],'booth_open':True,
            'map_id':1036,'trade':None,'request':None},
        'foreground_refill_1078':{'blocker':'waiting_farmer_handoff'}}}}


def test_verified_progress_is_consumed_before_failed_parking_and_survives_restart(tmp_path,monkeypatch):
    monkeypatch.setattr(handoff.time,'time',lambda:1000)
    proof=handoff.pending_listing_progress(progress_status())
    now=[900]
    windows=handoff.WorkWindows(tmp_path/'window.json',clock=lambda:now[0])
    assert windows.reserve('first')
    original=windows.state()
    now[0]=999
    assert not windows.reserve('too-early',listing_progress=proof,continuation=True)
    now[0]=1000
    assert windows.reserve('next',listing_progress=proof,continuation=True)
    assert windows.started(listing=True)==1045
    windows.finish('unsafe_deferred')
    windows=handoff.WorkWindows(windows.path,clock=lambda:now[0])
    assert windows.state()['next_check']==original['next_check']==1800
    assert windows.state()['last_check']==original['last_check']==900
    assert not windows.reserve('repeat-failure',listing_progress=proof,continuation=True)
    next_proof={**proof,'request_id':'booth-list1078-refill-next','verified_at':1001}
    now[0]=1061
    assert windows.reserve('new-progress',listing_progress=next_proof,continuation=True)
    assert windows.state()['next_check']==1800


@pytest.mark.parametrize('scope', ['town','urgent','visit'])
def test_backlog_cannot_extend_other_scope(tmp_path,scope):
    windows=handoff.WorkWindows(tmp_path/'window.json',clock=lambda:1000)
    assert windows.reserve('first')
    before=windows.state()
    kwargs={scope:True} if scope!='visit' else {'visit':{'deadline':1060}}
    assert not windows.reserve('wrong',continuation=True,
        listing_progress={'character':'Dutch','request_id':'proof','verified_at':900},**kwargs)
    assert windows.state()==before


@pytest.mark.parametrize('change', ['stale','future_observation','future_receipt','nan_receipt',
    'old_check','unknown_queue','unknown_status','manual','transaction','attention','paused',
    'process','profile','character','uid','active_listing'])
def test_continuation_requires_current_priced_same_actor_receipt(monkeypatch,change):
    monkeypatch.setattr(handoff.time,'time',lambda:1000)
    status=progress_status();c=status['characters']['Dutch'];r=c['refill'];p=r['last_verified_listing']
    assert handoff.pending_listing_progress(status)
    if change=='stale':c['snapshot']['timestamp']=997.9
    elif change=='future_observation':c['snapshot']['timestamp']=1001
    elif change=='future_receipt':p['verified_at']=1001
    elif change=='nan_receipt':p['verified_at']=float('nan')
    elif change=='old_check':r['attempt_started_at']=950
    elif change=='unknown_queue':r['deferred']=2
    elif change=='unknown_status':c['foreground_refill_1078']={'state':'unknown_prices_deferred'}
    elif change=='manual':c['manual_input_fence']=True
    elif change=='transaction':c['pending']=[{'phase':'uncertain'}]
    elif change=='attention':c['needs_attention']={'reason':'uncertain'}
    elif change=='paused':r['enabled']=False
    elif change=='process':p['identity']={**p['identity'],'creation_time_100ns':99}
    elif change=='profile':p['profile_id']='another-profile'
    elif change=='character':p['character']='Spiritual'
    elif change=='uid':p['character_uid']=10
    elif change=='active_listing':r['listing1078_request']={'request_id':'new'}
    before=deepcopy(status)
    assert handoff.pending_listing_progress(status) is None
    assert status==before


@pytest.fixture
def completed_ui(tmp_path,monkeypatch):
    from conquest.merchants.ui import UnifiedUI
    from conquest.merchants import journal as journal_module
    monkeypatch.setattr(journal_module,'character_name',lambda name:name)
    journal=Journal(tmp_path/'journal.sqlite3')
    schedule=RefillSchedule('Dutch',journal)
    schedule.complete('booth_full',listed=2)
    coordinator=NS(lock=threading.RLock(),owner=None,stopped=False,
        manual_active=lambda:False,manual_sessions={})
    runtime=NS(lock=threading.RLock(),handoff='merchant-refill:Dutch:123',
        delivery_window=None,refill_window=None,journal=journal,refills={'Dutch':schedule},
        manual_handoff_status=lambda:None,stop_event=threading.Event(),farmer_bot_owned=lambda:False,
        work_deadline=None)
    finished=[]
    runtime.finish_handoff=lambda:finished.append(True)
    ui=NS(runtime=runtime,coordinator=coordinator,grant=None,
        grant_fence=NS(active=None,requests={}),calibrating=set(),connect_threads={})
    calls=[]
    def dispatch(body):
        calls.append(body)
        return UnifiedUI.dispatch(ui,body)
    ui.dispatch=dispatch
    monkeypatch.setattr(listing,'WORKERS',{})
    return ui,schedule,calls,finished


def test_completed_refill_uses_existing_exact_release_without_changing_controls(completed_ui):
    ui,schedule,calls,finished=completed_ui;before=schedule.state()
    assert scopes.release_completed_refill(ui,'Dutch')
    assert calls==[{'action':'handoff-release','request_id':'merchant-refill:Dutch:123'}]
    assert ui.runtime.handoff is None and finished==[True]
    assert schedule.state()==before
    assert not scopes.release_completed_refill(ui,'Dutch')
    assert len(calls)==1


@pytest.mark.parametrize('change',['other_request','grant','active_fence','known_fence','owner',
    'manual','manual_global','stop','pending_refill','listing_worker','probe_worker','transaction',
    'farmer_delivery','calibrating'])
def test_completed_refill_never_releases_live_or_uncertain_work(completed_ui,monkeypatch,change):
    ui,schedule,calls,finished=completed_ui
    if change=='other_request':ui.runtime.handoff='merchant-refill:Spiritual:123'
    elif change=='grant':ui.grant={'request_id':ui.runtime.handoff}
    elif change=='active_fence':ui.grant_fence.active=object()
    elif change=='known_fence':ui.grant_fence.requests[ui.runtime.handoff]=object()
    elif change=='owner':ui.coordinator.owner='Dutch'
    elif change=='manual':ui.coordinator.manual_sessions={'Dutch':{'holds_automation':True}}
    elif change=='manual_global':ui.runtime.manual_handoff_status=lambda:{'active':True}
    elif change=='stop':ui.runtime.stop_event.set()
    elif change=='pending_refill':schedule.start()
    elif change=='listing_worker':monkeypatch.setitem(listing.WORKERS,'running',NS(is_alive=lambda:True))
    elif change=='probe_worker':ui.delivery_probe_thread=NS(is_alive=lambda:True)
    elif change=='transaction':ui.runtime.journal.begin('pending','Dutch','delivery',{})
    elif change=='farmer_delivery':ui.runtime.farmer_bot_owned=lambda:True
    elif change=='calibrating':ui.calibrating.add('Dutch')
    before=ui.runtime.handoff
    assert not scopes.release_completed_refill(ui,'Dutch')
    assert ui.runtime.handoff==before and not calls and not finished


@pytest.mark.parametrize('scope,scheduled,remaining,expected',[
    ('market_visit',True,60,35),('market_visit',True,25,22),
    ('market_visit',False,60,20),('listing_1078',True,45,35),
    (None,True,0,35),(None,False,0,20)])
def test_worker_budget_obeys_grant_and_keeps_unrelated_operations_at_twenty(
        monkeypatch,scope,scheduled,remaining,expected):
    from conquest import desktop_runtime,input_probe
    identity={'pid':7};captured=[];outcomes=[]
    request={'request_id':'booth-list1078-budget','expected_identity':identity}
    before={'request':request,'hwnd':8,'control':{},'farmer_target':{},'merchant_intent':{},
        'scheduled_foreground_refill':scheduled,
        'farmer_grant':{'scope':scope,'expires_at':1000+remaining} if scope else None}
    monkeypatch.setattr(listing.time,'time',lambda:1000)
    monkeypatch.setattr(listing.time,'monotonic',lambda:50)
    monkeypatch.setattr(input_probe,'MessageTarget',lambda pid,hwnd:NS(pid=pid,hwnd=hwnd))
    monkeypatch.setattr(desktop_runtime,'physical_coordinates',nullcontext)
    session=NS(identity=identity)
    monkeypatch.setattr(listing,'MemorySession',lambda *args:nullcontext(session))
    monkeypatch.setattr(listing.GuiReader,'for_session',lambda session:NS(model=lambda *args:1))
    monkeypatch.setattr(listing,'open_read_only_1078',lambda *args:NS())
    monkeypatch.setattr(listing,'read_build_layout',lambda session:NS(merchant_booth_vtable_rva=2))
    def policy(*args,**kwargs):
        captured.append(kwargs['deadline'])
        raise ValueError('Stop at first policy, before native input')
    monkeypatch.setattr(listing,'_policy',policy)
    monkeypatch.setattr(listing,'_unchanged_before_input',lambda *args:True)
    @contextmanager
    def scope_guard(character,callback):
        callback();yield
    coordinator=NS(fence=None,booth_listing_once_scope=scope_guard)
    journal=NS(transition=lambda *args:outcomes.append(args),set=lambda *args:None)
    listing._run(NS(runtime=NS(journal=journal),coordinator=coordinator),'Dutch',NS(name='Dutch'),before,None)
    assert captured==[50+expected]
    assert outcomes[0][1]=='aborted' and outcomes[0][2]['input_attempted'] is False


def test_38_second_admission_preserves_safe_off_without_deadline():
    runtime=NS(work_deadline=None)
    assert MerchantRuntime.can_start_work(runtime,38)


def test_progress_proof_requires_verified_receipt_and_is_counted_once_after_restart(receipt):
    from conquest.merchants import listing_capability_1078 as capability
    x=receipt
    x.j.set('Dutch','refill',{'pending':True,'listed':0,'cursor':[91,92],
                             'listing1078_request':x.request})
    with pytest.raises(ValueError,match='exact verified listing receipt'):
        refill_1078._settle_cursor(x.j,'Dutch',x.key)
    assert x.j.get('Dutch','refill').get('last_verified_listing') is None
    capability.settle(x.j,x.key,x.first,x.second)
    refill_1078._settle_cursor(x.j,'Dutch',x.key)
    reopened=Journal(x.j.path);state=reopened.get('Dutch','refill')
    proof=state['last_verified_listing']
    assert state['listed']==1 and state['cursor']==[92]
    assert proof['request_id']==x.key and proof['profile_id']==x.baseline['profile_id']
    assert proof['identity']==x.request['expected_identity']
    assert proof['character_uid']==x.request['expected_character_uid']
    refill_1078._settle_cursor(reopened,'Dutch',x.key)
    assert reopened.get('Dutch','refill')==state


@pytest.mark.parametrize('remaining,scan_cost,expected_reads',[(37.9,0,0),(38,0,0),(45,8,1)])
def test_scheduled_admission_rechecks_full_budget_after_slow_preflight(
        receipt,monkeypatch,remaining,scan_cost,expected_reads):
    from conquest.capture import CaptureUnavailable
    from conquest.merchants import observe_1078,listing_capability_1078
    from conquest import input_probe
    x=receipt;now=[1000.];reads=[]
    body={**x.request,'request_id':'booth-list1078-refill-budgetcase'}
    runtime=NS(journal=x.j,work_deadline=1000+remaining,
        merchant_windows=lambda:[NS(identity=x.before['identity'],hwnd=11)])
    runtime.can_start_work=lambda seconds:MerchantRuntime.can_start_work(runtime,seconds)
    ui=NS(runtime=runtime,app=NS(control=NS(snapshot=lambda:{})))
    monkeypatch.setattr(listing.time,'time',lambda:now[0])
    monkeypatch.setattr(scopes,'scope_allows',lambda *args,**kwargs:True)
    monkeypatch.setattr(listing,'_farmer_safe_market',lambda *args:{})
    monkeypatch.setattr(listing,'_merchant_intent',lambda *args:{})
    monkeypatch.setattr(listing,'_policy',lambda *args,**kwargs:None)
    observed={**deepcopy(x.before),'profile_uid_verified':True,'closed_modal':True,
        'listing_preflight':{'layout_observed':True,'price_modal':{'observed':False}}}
    def observe(*args,**kwargs):
        reads.append(True);now[0]+=scan_cost;return observed
    monkeypatch.setattr(observe_1078,'observe',observe)
    monkeypatch.setattr(listing_capability_1078,'bind_current',lambda *args:{})
    monkeypatch.setattr(listing,'_price_plan',lambda *args:{'price':body['price']})
    monkeypatch.setattr(input_probe,'MessageTarget',lambda *args:NS(hwnd=11,
        snapshot=lambda:{'root_hwnd':11}))
    with pytest.raises(CaptureUnavailable,match='seconds remaining|preflight consumed'):
        listing.dispatch(ui,body,scheduled_refill=True)
    assert len(reads)==expected_reads
    assert listing._row(x.j,body['request_id']) is None
