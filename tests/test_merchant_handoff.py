from conquest.merchants.handoff import WorkWindows, resumable
import copy
import ctypes
from types import SimpleNamespace
import pytest


def test_window_persists_attempts_and_never_catches_up(tmp_path):
    now=[1000]
    w=WorkWindows(tmp_path/'state.json',clock=lambda:now[0])
    assert w.reserve('first')
    assert not w.reserve('duplicate')
    w.finish('unsafe_deferred')
    w=WorkWindows(w.path,clock=lambda:now[0])
    assert not w.due()
    now[0]=10000
    assert w.reserve('after_restart')
    assert w.started()==10015
    assert w.state()['next_check']==10900
    assert not w.reserve('catch_up')


def test_required_town_visit_advances_interval(tmp_path):
    now=[1000]
    w=WorkWindows(tmp_path/'state.json',clock=lambda:now[0])
    w.reserve('hunting');w.started();w.finish('complete')
    now[0]=1100
    assert w.reserve('restock',town=True)
    assert w.started()==1115
    assert w.state()['next_check']==2000


def test_manual_stop_pause_and_identity_changes_prevent_resume():
    proof={'target':{'pid':1,'creation':2}}
    h={'target':proof['target'],'embedded_controls':{'control':{'revision':7,'enabled':False}}}
    assert resumable(h,proof,7)
    assert not resumable(h,proof,6)
    h['embedded_controls']['control']['paused']=True
    assert not resumable(h,proof,7)
    h['embedded_controls']['control']['paused']=False
    h['target']={'pid':1,'creation':3}
    assert not resumable(h,proof,7)


@pytest.mark.parametrize('pause', [False,True])
@pytest.mark.parametrize('connected', [False,True])
def test_native_window_releases_before_resume_and_honors_f11(tmp_path,monkeypatch,pause,connected):
    from conquest.merchants import handoff,bridge
    from conquest import worker,safe_reload
    from conquest.overnight import OvernightStopped
    now=[1000.0];calls=[]
    health={'target':{'pid':7},'embedded_controls':{'control':{'enabled':True,'revision':1}}}
    monkeypatch.setattr(handoff.time,'time',lambda:now[0])
    monkeypatch.setattr(handoff.time,'sleep',lambda seconds:now.__setitem__(0,now[0]+seconds))
    original_read=handoff.read_json
    monkeypatch.setattr(handoff,'read_json',lambda path:
        {'parity_verified':True,'hunting_handoffs_enabled':True} if str(path).endswith('merchant-deliveries.json') else original_read(path))
    windows=WorkWindows(tmp_path/'window.json',clock=lambda:now[0])
    monkeypatch.setattr(handoff,'WorkWindows',lambda:windows)
    monkeypatch.setattr(ctypes.windll.user32,'GetAsyncKeyState',lambda key:0x8000 if pause else 0)
    def farm(info,operation,body=None):
        if operation=='controls':
            calls.append('resume');health['embedded_controls']['control'].update(body)
        return copy.deepcopy(health)
    monkeypatch.setattr(worker,'request',farm)
    def merchant(body):
        action=body['action'];calls.append(action)
        if action=='status':return {'handoff_requested':'window:1','characters':{'Dutch':{'connected':connected,'enabled':True,'credentials_saved':True,'qualification':{'login':True}}}}
        if action=='handoff-grant':
            assert body['expires_at']-now[0]==15
            return {'granted':True}
        if action=='handoff-release':return {'released':True}
        raise AssertionError(action)
    monkeypatch.setattr(bridge,'request',merchant)
    def park(loop,cancelled,notify,**kwargs):
        assert kwargs=={'seconds':12,'allow_town_retreat':False}
        cancelled.is_set()
        return {'target':health['target']}
    monkeypatch.setattr(safe_reload,'park',park)
    monkeypatch.setattr(safe_reload,'clear_observation',lambda h:True)
    def stop():health['embedded_controls']['control'].update(enabled=False,revision=2)
    loop=SimpleNamespace(info='test',phase='hunting',health=lambda:copy.deepcopy(health),stop_farm=stop,
        check_stop=lambda:None,record=lambda *args,**kwargs:None,focus=lambda h:calls.append('focus'))
    if pause:
        with pytest.raises(OvernightStopped,match='F11'):handoff.service_window(loop)
        assert 'resume' not in calls and 'handoff-grant' not in calls
    else:
        assert handoff.service_window(loop)
        assert calls.index('handoff-release')<calls.index('resume')
        assert windows.state()['next_check']==1900
        assert 1015<=now[0]<1016


def test_recovery_handoff_requires_operations_credentials_and_qualified_login():
    from conquest.merchants.handoff import service_candidate
    disconnected={'connected':False,'enabled':True,'credentials_saved':True,'qualification':{'login':True}}
    assert service_candidate(disconnected)
    for field in ('enabled','credentials_saved'):
        assert not service_candidate({**disconnected,field:False})
    assert not service_candidate({**disconnected,'qualification':{'login':False}})
    assert not service_candidate({})
    # A connected merchant may refill with trading paused; login cannot.
    assert service_candidate({'connected':True,'enabled':False})


def test_recovery_bypasses_refill_interval_without_catch_up(tmp_path):
    w=WorkWindows(tmp_path/'state.json',clock=lambda:1000)
    assert w.reserve('routine')
    assert not w.reserve('routine-again')
    assert w.reserve('merchant-recovery:Dutch:1',urgent=True)
    assert not w.reserve('routine-after-recovery')


def test_old_market_incident_cannot_bypass_refill_interval():
    from conquest.merchants.handoff import urgent_recovery
    s={'handoff_requested':'merchant-return:Spiritual:123','characters':{'Spiritual':{
        'snapshot':{'map_id':1036},'shop_return':{'phase':'needs_attention'}}}}
    assert not urgent_recovery(s)
    s['characters']['Spiritual']['recovery_safety']={'active':True}
    assert urgent_recovery(s)


@pytest.fixture
def town_service(tmp_path,monkeypatch):
    from conquest.merchants import handoff,bridge,service_visit
    from conquest import worker,safe_reload
    now=[1000.0];calls=[];requested=[None];park_cost=[0.0];grant_error=[False]
    health={'target':{'pid':7},'embedded_controls':{
        'control':{'enabled':False,'revision':1},'life':{'map_id':1036}}}
    monkeypatch.setattr(handoff.time,'time',lambda:now[0])
    monkeypatch.setattr(handoff.time,'sleep',lambda seconds:now.__setitem__(0,now[0]+seconds))
    original_read=handoff.read_json
    monkeypatch.setattr(handoff,'read_json',lambda path:
        {'parity_verified':True,'hunting_handoffs_enabled':False}
        if str(path).endswith('merchant-deliveries.json') else original_read(path))
    windows=WorkWindows(tmp_path/'window.json',clock=lambda:now[0])
    visit=service_visit.MarketVisit(tmp_path/'visit.json',clock=lambda:now[0])
    monkeypatch.setattr(handoff,'WorkWindows',lambda:windows)
    monkeypatch.setattr(service_visit,'MarketVisit',lambda:visit)
    monkeypatch.setattr(service_visit,'parent_visit',lambda:'required-town:1')
    monkeypatch.setattr(ctypes.windll.user32,'GetAsyncKeyState',lambda key:0)
    monkeypatch.setattr(worker,'request',lambda *args,**kwargs:copy.deepcopy(health))
    def merchant(body):
        calls.append(copy.deepcopy(body));action=body['action']
        if action=='status':
            # Trading may be paused; independent refill remains serviceable.
            return {'handoff_requested':requested[0],'characters':{'Dutch':{'connected':True,'enabled':False}}}
        if action=='refill-check':requested[0]=body['request_id'];return {'requested':requested[0]}
        if action=='handoff-grant':
            if grant_error[0]:raise OSError('Lost grant acknowledgement')
            return {'granted':True}
        if action=='handoff-release':requested[0]=None;return {'released':True}
        raise AssertionError(action)
    monkeypatch.setattr(bridge,'request',merchant)
    def park(loop,cancelled,notify,**kwargs):
        cancelled.is_set();now[0]+=min(park_cost[0],kwargs['seconds'])
        return {'target':health['target']}
    monkeypatch.setattr(safe_reload,'park',park)
    monkeypatch.setattr(safe_reload,'clear_observation',lambda h:True)
    loop=SimpleNamespace(info='test',phase='restocking',health=lambda:copy.deepcopy(health),
        stop_farm=lambda:None,check_stop=lambda:None,record=lambda *args,**kwargs:None,
        focus=lambda h:None,market_service_deadline=123)
    return SimpleNamespace(now=now,calls=calls,health=health,windows=windows,visit=visit,
        loop=loop,park_cost=park_cost,grant_error=grant_error,run=lambda:handoff.service_window(loop,town=True))


def test_refill_only_market_visit_uses_one_sixty_second_budget(town_service):
    r=town_service
    assert r.run()
    grant=next(c for c in r.calls if c['action']=='handoff-grant')
    assert grant['scope']=='market_visit' and grant['expires_at']==1060
    assert grant['visit_id']==r.visit.begin(parent='required-town:1')['visit_id']
    assert 1060<=r.now[0]<1060.3 and r.windows.state()['next_check']==1900
    assert r.windows.state()['phase']=='paused_budget'
    assert r.loop.market_service_deadline==123
    prior=len([c for c in r.calls if c['action']=='handoff-grant'])
    assert not r.run()
    assert len([c for c in r.calls if c['action']=='handoff-grant'])==prior


def test_refill_after_delivery_consumes_only_same_visit_remainder(town_service):
    r=town_service;visit=r.visit.begin(parent='required-town:1')
    r.windows.reserve('delivery-first',town=True,visit=visit);r.windows.started()
    r.windows.finish('released');r.now[0]=1050
    assert r.run()
    grant=next(c for c in r.calls if c['action']=='handoff-grant')
    assert grant['expires_at']==1060 and grant['visit_id']==visit['visit_id']
    assert r.windows.state()['next_check']==1900
    assert r.windows.state()['last_attempt_at']==1000


def test_exhausted_delivery_visit_does_not_queue_or_grant_extra_refill(town_service):
    r=town_service;visit=r.visit.begin(parent='required-town:1')
    r.windows.reserve('delivery-first',town=True,visit=visit);r.windows.started()
    r.now[0]=1060
    assert not r.run()
    assert [c['action'] for c in r.calls]==['status']
    assert r.windows.state()['request_id']=='delivery-first'


def test_non_market_town_retains_fifteen_seconds_without_market_visit(town_service):
    r=town_service;r.health['embedded_controls']['life']['map_id']=1002
    assert r.run()
    grant=next(c for c in r.calls if c['action']=='handoff-grant')
    assert grant['expires_at']==1015 and 'scope' not in grant
    assert not r.visit.path.exists()


def test_market_parking_cannot_extend_original_visit_deadline(town_service):
    r=town_service;r.visit.begin(parent='required-town:1');r.now[0]=1058;r.park_cost[0]=10
    assert not r.run()
    assert not any(c['action']=='handoff-grant' for c in r.calls)
    assert r.now[0]==1060 and r.loop.market_service_deadline==123


def test_lost_grant_acknowledgement_still_revokes_possible_input(town_service):
    r=town_service;r.grant_error[0]=True
    with pytest.raises(OSError,match='Lost grant acknowledgement'):r.run()
    actions=[c['action'] for c in r.calls]
    assert actions.index('handoff-grant')<actions.index('handoff-release')
    assert r.loop.market_service_deadline==123
