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
