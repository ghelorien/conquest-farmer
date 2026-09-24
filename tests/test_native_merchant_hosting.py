from contextlib import nullcontext
from types import SimpleNamespace as NS
import threading
import time
import pytest

from conquest.merchants import restore_hosts as hosts
from conquest.merchants.journal import Journal
from conquest.memory_build_layout import CLIENT_SHA256_1078


def fixture(tmp_path,monkeypatch):
    journal=Journal(tmp_path/'journal.sqlite3');calls=[]
    identity={'pid':2,'creation_time_100ns':3,'path':'game.exe'}
    pane=NS(winfo_ismapped=lambda:False,winfo_id=lambda:9,winfo_width=lambda:1,winfo_height=lambda:1)
    observer=NS(hwnd=2,merchant_observation_only=True,
        adapter=NS(expected_sha256=CLIENT_SHA256_1078,identity=identity,assert_identity=lambda:calls.append('identity')))
    runtime=NS(journal=journal,observers={'Dutch':observer,'Spiritual':observer},latest={c:{'identity':identity,'timestamp':time.time()} for c in ('Dutch','Spiritual')},
        manual_handoff_status=lambda:None,connecting={},refilling={},attachments={},delivery_window=None,refill_window=None,stop_event=threading.Event(),handoff='merchant-refill:Spiritual:123')
    coordinator=NS(owner=None,purpose=None,lock=threading.RLock(),stopped=False,
        manual_active=lambda:False,manual_session_blocked=lambda c:False,surface_blocks={})
    ui=NS(closed=False,app=NS(closing=False,control=NS(snapshot=lambda:{'enabled':False,'paused':False,'revision':1})),
        runtime=runtime,coordinator=coordinator,calibrating=set(),released_clients=set(),grant=None,
        hosts={},client_panes={'Dutch':pane,'Spiritual':pane},layout_status={},calibration_results={})
    monkeypatch.setattr('conquest.merchants.background_probe.probe_busy',lambda ui:False)
    monkeypatch.setattr('conquest.merchants.listing_handoff_1078.parked',lambda ui:calls.append('fresh_park'))
    monkeypatch.setattr('conquest.merchants.booth_probe_1078._farmer_journals_clear',lambda r:None)
    monkeypatch.setattr('conquest.merchants.coordination.input_scope',lambda **kw:nullcontext(calls.append(kw)))
    class Host:
        mode='owned';saved=None
        def __init__(self):
            self.api=NS(assert_owner=lambda *a:calls.append('owner'),gui=NS(
                IsIconic=lambda h:False,GetClientRect=lambda h:(0,0,1420,1009),
                GetAncestor=lambda h,f:h if h==2 else 10,GetWindow=lambda h,f:10,
                IsWindowVisible=lambda h:False))
        def attach(self,hwnd,identity,parent,w,h):
            calls.append(('attach',hwnd,parent,w,h));self.saved=NS(hwnd=hwnd,identity=identity)
        def resize(self,*a):calls.append(('resize',a))
    return ui,Host,calls


def test_hidden_hosts_attach_under_fresh_nonmarket_park_without_changing_controls(tmp_path,monkeypatch):
    ui,Host,calls=fixture(tmp_path,monkeypatch)
    hosts.restore(ui,host_factory=Host)
    assert len([x for x in calls if isinstance(x,tuple) and x[0]=='attach'])==2
    assert 'fresh_park' in calls and {'purpose':'merchant_host'} in calls
    assert all(s['attached'] and not s['selected'] and not s['native_visible'] for s in ui.layout_status.values())
    assert ui.coordinator.surface_blocks=={'Dutch':True,'Spiritual':True}
    assert ui.runtime.handoff=='merchant-refill:Spiritual:123'
    assert ui.runtime.journal.get('Dutch','enabled') is None
    assert ui.runtime.journal.get('Dutch','refill_enabled') is None


def test_selected_identified_client_automatically_hosts_with_untouched_pending_meteor(tmp_path,monkeypatch):
    from conquest.merchants.ui import UnifiedUI
    from conquest import meteor_banking
    from conquest.discord_notify import write_json
    ui,Host,calls=fixture(tmp_path,monkeypatch)
    write_json(meteor_banking.JOURNAL,{'phase':'storing_scroll','scroll_uid':296447900,
                                     'exchange_verified':True,'origin':1011})
    before=meteor_banking.JOURNAL.read_bytes()
    monkeypatch.setattr('conquest.merchants.booth_probe_1078._farmer_journals_clear',
                        lambda runtime:pytest.fail('Display must not interpret Farmer transaction journals'))
    monkeypatch.setattr('conquest.window_host.EmbeddedWindow',lambda **kwargs:Host())
    monkeypatch.setattr('conquest.merchants.ui.probe_busy',lambda ui:False)
    ui.notebook=NS(select=lambda:'Dutch');ui.frames={'Dutch':'Dutch','Spiritual':'Spiritual'}
    ui.auto_embedding=False;ui.auto_embed_retry={}
    ui.client_panes['Dutch']=NS(winfo_ismapped=lambda:True,winfo_id=lambda:9,
        winfo_width=lambda:1420,winfo_height=lambda:1009)
    with ui.runtime.journal.db() as db:
        saved=list(db.execute('SELECT character,name,value FROM state ORDER BY character,name'))
    UnifiedUI.auto_show_selected(ui)
    assert ui.hosts['Dutch'].saved and ui.layout_status['Dutch']['attached']
    assert ('attach',2,9,1420,1009) in calls and 'fresh_park' in calls
    assert {'purpose':'merchant_host'} in calls
    assert meteor_banking.JOURNAL.read_bytes()==before
    with ui.runtime.journal.db() as db:
        assert list(db.execute('SELECT character,name,value FROM state ORDER BY character,name'))==saved
    assert ui.runtime.handoff=='merchant-refill:Spiritual:123'
    assert ui.app.control.snapshot()=={'enabled':False,'paused':False,'revision':1}


def test_pending_merchant_transaction_still_protects_layout(tmp_path,monkeypatch):
    ui,Host,calls=fixture(tmp_path,monkeypatch)
    ui.runtime.journal.begin('unresolved-listing','Dutch','booth_listing_1078_once',{})
    assert hosts.restore_readonly(ui,'Dutch',host_factory=Host) is False
    assert not any(isinstance(c,tuple) and c[0]=='attach' for c in calls)


@pytest.mark.parametrize('hold',['stop','manual','owner','visitor','handoff','refilling'])
def test_hosting_holds_send_no_window_mutation(tmp_path,monkeypatch,hold):
    ui,Host,calls=fixture(tmp_path,monkeypatch)
    if hold=='stop':ui.coordinator.stopped=True
    elif hold=='manual':ui.coordinator.manual_active=lambda:True
    elif hold=='owner':ui.coordinator.owner='Dutch'
    elif hold=='visitor':ui.coordinator.manual_session_blocked=lambda c:True
    elif hold=='handoff':ui.runtime.manual_handoff_status=lambda:{'phase':'ready'}
    else:ui.runtime.refilling={'Dutch':1}
    hosts.restore(ui,host_factory=Host)
    assert not any(isinstance(x,tuple) and x[0]=='attach' for x in calls)


def test_host_intent_independent_of_permissions_and_manual_release_survives_restart(tmp_path,monkeypatch):
    ui,Host,calls=fixture(tmp_path,monkeypatch)
    for c in ('Dutch','Spiritual'):
        ui.runtime.journal.set(c,'enabled',False);ui.runtime.journal.set(c,'refill_enabled',False)
    row=hosts.requested(ui)
    assert set(row['identities'])=={'Dutch','Spiritual'}
    assert hosts.requested(ui)==row
    hosts.released(ui,'Dutch',True)
    ui.released_clients={c for c in ('Dutch','Spiritual') if ui.runtime.journal.get(c,'host_released',False)}
    assert set(hosts.requested(ui)['identities'])=={'Spiritual'}
    hosts.restore(ui,host_factory=Host)
    assert set(ui.hosts)=={'Spiritual'}
    assert ui.runtime.handoff=='merchant-refill:Spiritual:123'
    hosts.released(ui,'Dutch',False)
    assert ui.runtime.journal.get('Dutch','host_released') is False


def test_host_grant_cannot_yield_game_input_and_release_restores_queued_refill(tmp_path,monkeypatch):
    from conquest.merchants.ui import UnifiedUI
    from conquest.merchants.grant_fence import GrantFence
    ui,Host,calls=fixture(tmp_path,monkeypatch)
    ui.grant_fence=GrantFence();ui.runtime.finish_handoff=lambda:pytest.fail('Host release must not pause refill or advance its timer')
    request=hosts.requested(ui)
    result=UnifiedUI.dispatch(ui,{'action':'handoff-grant','request_id':request['request_id'],
        'revision':1,'expires_at':time.time()+10,'safe':True,'scope':'merchant_host'})
    assert result=={'granted':True}
    assert UnifiedUI.safe_to_yield(ui) is False
    assert ui.runtime.refill_window is None and ui.runtime.delivery_window is None
    assert UnifiedUI.dispatch(ui,{'action':'handoff-release','request_id':request['request_id']})=={'released':True}
    assert ui.runtime.handoff=='merchant-refill:Spiritual:123'
    assert ui.grant is None and ui.grant_fence.active is None


def test_host_request_admission_requires_matching_connected_process_and_separate_timer(tmp_path):
    from conquest.merchants.handoff import native_host_request,WorkWindows
    status={'host_request':{'request_id':'merchant-host:abc','identities':{'Dutch':{'pid':2}}},
        'characters':{'Dutch':{'connected':True,'enabled':False,'refill':{'enabled':False},'snapshot':{'identity':{'pid':2}}}}}
    assert native_host_request(status)=='merchant-host:abc'
    ordinary=WorkWindows(tmp_path/'refill.json',clock=lambda:1000)
    ordinary.reserve('merchant-refill:Dutch:1');before=ordinary.path.read_bytes()
    hosting=WorkWindows(tmp_path/'host.json',clock=lambda:1001)
    assert hosting.reserve('merchant-host:abc') and hosting.started()==1016
    hosting.finish('released')
    assert ordinary.path.read_bytes()==before
    status['characters']['Dutch']['snapshot']['identity']={'pid':3}
    assert native_host_request(status) is None


def test_manual_session_defers_host_intent_without_erasing_preference(tmp_path,monkeypatch):
    ui,Host,calls=fixture(tmp_path,monkeypatch)
    previous=hosts.requested(ui)
    ui.coordinator.manual_session_blocked=lambda c:True
    assert hosts.requested(ui) is None
    ui.coordinator.manual_session_blocked=lambda c:False
    assert hosts.requested(ui)==previous


def test_pending_transaction_defers_host_intent_until_exact_cleanup(tmp_path,monkeypatch):
    ui,Host,calls=fixture(tmp_path,monkeypatch)
    previous=hosts.requested(ui)
    ui.runtime.journal.begin('exact-pending','Dutch','booth_listing_1078_once',{})
    assert hosts.requested(ui) is None
    assert ui.native_host_request==previous
    ui.runtime.journal.transition('exact-pending','aborted',{'cancel_verified':True})
    assert hosts.requested(ui)==previous


def test_host_scope_denies_low_level_game_input(monkeypatch):
    from conquest.merchants import coordination
    from conquest.capture import CaptureUnavailable
    monkeypatch.setattr(coordination,'_coordinator',NS(purpose='merchant_host'))
    with pytest.raises(CaptureUnavailable,match='does not authorize'):coordination.check_input()
    @coordination.coordinated_input
    def click():pytest.fail('Host-only authority reached game input')
    with pytest.raises(CaptureUnavailable,match='does not authorize'):click()


def test_qualified_native_lease_uses_only_native_presentation_and_releases(tmp_path):
    from conquest.merchants.coordination import InputCoordinator
    guard=InputCoordinator(safe_to_yield=lambda:True,path=tmp_path/'input.lock');calls=[]
    guard.native_trade1078_policy=lambda c:c=='Dutch'
    guard.surface_blocks['Dutch']=True
    guard.on_acquire=lambda c:pytest.fail('Legacy surface preparation must remain unavailable')
    guard.on_native_acquire=lambda c:calls.append(('present',c,guard.owner,guard.purpose))
    guard.on_release=lambda c:calls.append(('release',c,guard.owner))
    with guard.lease('Dutch',purpose='trade'):calls.append('body')
    assert calls==[('present','Dutch','Dutch','trade'),'body',('release','Dutch',None)]


def test_exact_native_lease_presents_hidden_host_without_qualifying_or_attaching(tmp_path,monkeypatch):
    ui,Host,calls=fixture(tmp_path,monkeypatch)
    hosts.restore(ui,host_factory=Host);calls.clear()
    host=ui.hosts['Dutch'];observer=ui.runtime.observers['Dutch']
    host.api.gui.GetForegroundWindow=lambda:0
    ui.coordinator.owner='Dutch';ui.coordinator.purpose='booth_listing_1078_once'
    ui.safe_to_yield=lambda:True
    selected=['Farmer']
    def select(value=None):
        if value is not None:selected[0]=value
        return selected[0]
    ui.notebook=NS(select=select);ui.frames={'Dutch':'Dutch'}
    ui.detail_tabs={'Dutch':NS(select=lambda index:None)};ui.input_bookmarks={}
    ui.root=NS(update_idletasks=lambda:None)
    ui.client_panes['Dutch']=NS(winfo_ismapped=lambda:True,winfo_width=lambda:1420,winfo_height=lambda:1009)
    ui.hosts['Spiritual'].api.show_async=lambda h,flag:calls.append(('hide',h,flag))
    expected=(host,observer,2,dict(observer.adapter.identity),'booth_listing_1078_once')
    hosts.present_native(ui,'Dutch',expected)
    assert selected[0]=='Dutch' and ui.input_bookmarks['Dutch']['tab']=='Farmer'
    assert ('resize',(1420,1009)) in calls and ('hide',2,0) in calls
    assert ui.coordinator.surface_blocks['Dutch'] is True
    assert not any(isinstance(c,tuple) and c[0]=='attach' for c in calls)
    ui.coordinator.stopped=True;calls.clear()
    with pytest.raises(ValueError,match='lease changed'):hosts.present_native(ui,'Dutch',expected)
    assert calls==[]


@pytest.mark.parametrize('reason',['town','urgent','not_due'])
def test_host_intent_does_not_starve_ordinary_work(tmp_path,monkeypatch,reason):
    from conquest.merchants import handoff,bridge
    identity={'pid':2}
    status={'host_request':{'request_id':'merchant-host:x','identities':{'Dutch':identity}},
        'handoff_requested':'merchant-recovery:Dutch:1' if reason=='urgent' else 'merchant-refill:Dutch:1',
        'characters':{'Dutch':{'connected':True,'snapshot':{'identity':identity},
            'refill':{'enabled':True},'qualification':{'foreground_open_booth_listing_1078':True},
            'recovery_safety':{'active':reason=='urgent'}}}}
    monkeypatch.setattr(bridge,'request',lambda body:status)
    monkeypatch.setattr(handoff,'read_json',lambda path:{'parity_verified':True,'hunting_handoffs_enabled':True})
    ordinary=NS(due=lambda:True);paths=[]
    def windows(path=None):
        paths.append(path)
        return ordinary if path is None else NS(due=lambda:False)
    monkeypatch.setattr(handoff,'WorkWindows',windows)
    class ReachedHealth(Exception):pass
    def health():raise ReachedHealth
    with pytest.raises(ReachedHealth):handoff.service_window(NS(health=health),town=reason=='town')
    assert paths[0] is None
    assert len(paths)==(2 if reason=='not_due' else 1)


@pytest.mark.parametrize('outcome',['attached','lost_ack','manual_conflict'])
def test_host_attachment_before_grant_does_not_release_nonexistent_request(tmp_path,monkeypatch,outcome):
    import copy
    import ctypes
    from conquest.merchants import handoff,bridge
    from conquest import worker,safe_reload
    identity={'pid':2};calls=[];statuses=[0]
    status={'host_request':{'request_id':'merchant-host:x','identities':{'Dutch':identity}},
        'characters':{'Dutch':{'connected':True,'snapshot':{'identity':identity,'timestamp':time.time()}}}}
    health={'target':{'pid':1},'embedded_controls':{'control':{'enabled':True,'revision':3}}}
    original=handoff.WorkWindows
    ordinary=original(tmp_path/'ordinary.json');hosting=original(tmp_path/'hosting.json')
    monkeypatch.setattr(handoff,'WorkWindows',lambda path=None:ordinary if path is None else hosting)
    read=handoff.read_json
    monkeypatch.setattr(handoff,'read_json',lambda path:
        {'parity_verified':True,'hunting_handoffs_enabled':True} if path==handoff.POLICY else read(path))
    monkeypatch.setattr(ctypes.windll.user32,'GetAsyncKeyState',lambda k:0)
    monkeypatch.setattr(safe_reload,'park',lambda *a,**kw:{})
    monkeypatch.setattr(safe_reload,'clear_observation',lambda h:True)
    def merchant(body):
        action=body['action'];calls.append(action)
        if action=='status':
            statuses[0]+=1
            if statuses[0]==1:return copy.deepcopy(status)
            return {**status,'host_request':None,'input_owner':None,'handoff_active':False,
                'manual_sessions':[],'manual_handoff':{'phase':'ready'} if outcome=='manual_conflict' else None,
                'manual_farmer':{'input_fenced':False},'layout':{'Dutch':{'attached':True}}}
        if action=='handoff-grant':
            if outcome=='lost_ack':raise OSError('Lost acknowledgement')
            raise bridge.MerchantRejected('Handoff request changed')
        if action=='handoff-release':return {'released':True}
        pytest.fail(action)
    monkeypatch.setattr(bridge,'request',merchant)
    def farmer(info,action,body=None):
        if action=='controls':calls.append('resume');health['embedded_controls']['control'].update(body)
        return copy.deepcopy(health)
    monkeypatch.setattr(worker,'request',farmer)
    def stop():health['embedded_controls']['control']['enabled']=False
    loop=NS(info='test',phase='hunting',health=lambda:copy.deepcopy(health),stop_farm=stop,
        check_stop=lambda:None,record=lambda *a,**kw:None,focus=lambda h:calls.append('focus'))
    if outcome=='attached':
        assert handoff.service_window(loop)
        assert 'handoff-release' not in calls and 'resume' in calls
        assert hosting.state()['phase']=='host_restored_before_grant'
    elif outcome=='lost_ack':
        with pytest.raises(OSError,match='Lost acknowledgement'):handoff.service_window(loop)
        assert calls.index('handoff-release')<calls.index('resume')
    else:
        with pytest.raises(bridge.MerchantRejected):handoff.service_window(loop)
        assert 'handoff-release' not in calls and 'resume' not in calls


@pytest.mark.parametrize('changed',['owner','grant','manual','identity','stale','attachment'])
def test_completed_host_race_never_ignores_conflicting_or_stale_proof(changed):
    from conquest.merchants.handoff import host_restored_before_grant
    identities={'Dutch':{'pid':2}}
    status={'handoff_active':False,'input_owner':None,'host_request':None,'manual_handoff':None,
        'manual_sessions':[],'manual_farmer':{'input_fenced':False},'layout':{'Dutch':{'attached':True}},
        'characters':{'Dutch':{'connected':True,'snapshot':{'identity':{'pid':2},'timestamp':time.time()}}}}
    assert host_restored_before_grant(status,identities)
    if changed=='owner':status['input_owner']='Dutch'
    elif changed=='grant':status['handoff_active']=True
    elif changed=='manual':status['manual_farmer']['input_fenced']=True
    elif changed=='identity':status['characters']['Dutch']['snapshot']['identity']={'pid':3}
    elif changed=='stale':status['characters']['Dutch']['snapshot']['timestamp']-=3
    else:status['layout']['Dutch']['attached']=False
    assert not host_restored_before_grant(status,identities)
