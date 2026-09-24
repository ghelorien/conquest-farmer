import json
import threading
import time
from types import SimpleNamespace
import urllib.error
import urllib.request
import pytest

from conquest.embedded_bridge import EmbeddedBridge
from conquest.worker import request


@pytest.fixture
def bridge(tmp_path):
    calls = []
    control = {'enabled':False}
    def dispatch(operation,body):
        calls.append((operation,body))
        return {'qualified':False}
    operations = SimpleNamespace(session=SimpleNamespace(pid=1),dispatch=dispatch)
    info = tmp_path/'connection.json'
    service = EmbeddedBridge(operations,threading.RLock(),info,lambda:{'control':control})
    yield service,info,calls,control
    service.close()
    assert not service.thread.is_alive() and not info.exists()


def test_bridge_reuses_worker_protocol_and_reports_current_controls(bridge):
    service,info,calls,control = bridge
    result = request(info,'health')
    assert result['embedded_controls']['control']==control
    assert calls==[('health',{})]


def test_health_fences_cursor_observation_gap_without_changing_farming_intent(bridge):
    service, info, _calls, control = bridge
    control['enabled'] = True
    service.operations.dispatch = lambda _operation, _body: {
        'window': {'cursor': None, 'cursor_available': False}}
    result = request(info, 'health')
    assert result['embedded_controls']['manual_mouse'] is True
    assert result['embedded_controls']['control']['enabled'] is True


def test_health_cursor_api_gap_latches_physical_mouse_idle_fence(bridge,monkeypatch):
    from conquest import mouse_priority
    service, info, _calls, control = bridge
    control['enabled'] = True
    now=[10.]
    guard=mouse_priority.MousePriority(lambda:((100,100),0),clock=lambda:now[0])
    monkeypatch.setattr(mouse_priority,'_guard',guard)
    cursor_available=[False]
    service.operations.dispatch = lambda _operation, _body: {
        'window': {'cursor_available':cursor_available[0]}}
    assert request(info,'health')['embedded_controls']['manual_mouse'] is True
    assert guard.observation_gap
    cursor_available[0]=True
    now[0]+=30
    assert request(info,'health')['embedded_controls']['manual_mouse'] is True
    now[0]+=1.99
    assert request(info,'health')['embedded_controls']['manual_mouse'] is True
    now[0]+=.02
    assert request(info,'health')['embedded_controls']['manual_mouse'] is False
    assert control['enabled'] is True


def test_health_exposes_the_current_manual_session_fence_without_changing_intent(bridge,monkeypatch):
    from conquest.merchants import coordination
    service,info,calls,control=bridge
    blocked=[False]
    monkeypatch.setattr(coordination,'manual_session_blocked',lambda character:blocked[0])
    control['enabled']=True
    assert request(info,'health')['embedded_controls']['manual_input_fence'] is False
    blocked[0]=True
    assert request(info,'health')['embedded_controls']['manual_input_fence'] is True
    assert control['enabled'] is True and calls==[('health',{}),('health',{})]


def test_vendor_reader_is_read_only_bound_callback(bridge):
    service, info, calls, control = bridge
    with pytest.raises(ValueError, match='unavailable'):
        request(info, 'sample-npcs')
    service.on_sample_npcs = lambda:{'snapshot':{'npcs':[]}, 'shop_items_qualified':False}
    control['enabled'] = True
    assert request(info, 'sample-npcs')['shop_items_qualified'] is False
    with pytest.raises(ValueError, match='unsupported arguments'):
        request(info, 'sample-npcs', {'click':True})
    assert not calls


def test_town_input_requires_off_and_fresh_expiry_but_supply_reads_do_not(bridge):
    service, info, calls, control = bridge
    town_calls=[]
    service.on_town=lambda body:town_calls.append(body) or {'ok':True}
    control['enabled']=True
    assert request(info,'town',{'action':'supplies'})=={'ok':True}
    with pytest.raises(ValueError,match='Stop farming'):
        request(info,'town',{'action':'buy','expires_at':time.time()+3})
    control['enabled']=False
    with pytest.raises(ValueError,match='expire'):
        request(info,'town',{'action':'buy','expires_at':time.time()-1})
    request(info,'town',{'action':'buy','vendor_type':3,'type_id':1000020,'expires_at':time.time()+3})
    assert town_calls[-1]=={'action':'buy','vendor_type':3,'type_id':1000020}
    assert not calls


def test_town_final_press_rechecks_manual_control_revision(bridge):
    from conquest.capture import CaptureUnavailable
    service,info,calls,control=bridge
    control['revision']=2
    class Town:
        check_input=None
        def __call__(self,body):
            self.check_input()
            control['revision']+=1
            self.check_input()
            pytest.fail('Stale input was allowed')
    town=Town();service.on_town=town
    with pytest.raises(ValueError,match='permission changed'):
        request(info,'town',{'action':'service-close-panel','window':'Inventory','expires_at':time.time()+3})
    assert town.check_input is None


@pytest.mark.parametrize('operation',['foreground-click','foreground-key','shutdown','scan'])
def test_bridge_does_not_expose_unneeded_worker_operations(bridge,operation):
    service,info,calls,control = bridge
    with pytest.raises(ValueError,match='not available'):
        request(info,operation)
    assert not calls


@pytest.mark.parametrize('operation',['background-click','revive-click'])
def test_bridge_requires_fresh_stopped_diagnostic(bridge,operation):
    service,info,calls,control = bridge
    with pytest.raises(ValueError,match='expire'):
        request(info,operation,{'expires_at':time.time()-1})
    control['enabled'] = True
    with pytest.raises(ValueError,match='Stop farming'):
        request(info,operation,{'expires_at':time.time()+4})
    assert not calls
    control['enabled'] = False
    assert request(info,operation,{'expires_at':time.time()+4})['qualified'] is False
    assert len(calls)==1


def test_bridge_rejects_wrong_token_before_dispatch(bridge):
    service,info,calls,control = bridge
    connection = json.loads(info.read_text())
    call = urllib.request.Request(f"http://127.0.0.1:{connection['port']}/health",
        data=b'{}',headers={'X-Conquest-Token':'wrong'})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with pytest.raises(urllib.error.HTTPError) as error:
        opener.open(call,timeout=2)
    assert error.value.code==403 and not calls


def test_bridge_control_updates_and_reload_use_only_bound_callbacks(bridge):
    from conquest.control import FarmingControl
    service,info,calls,_ = bridge
    control = FarmingControl()
    reloads = []
    service.snapshot = lambda:{'control':control.snapshot()}
    service.control_update = control.update
    service.on_reload = lambda:reloads.append('reload')
    result = request(info,'controls',{'target_type_ids':[1],'enabled':True})
    assert result['target_type_ids']==[1] and result['enabled']
    assert request(info,'reload-app')=={'reload_queued':True}
    assert control.snapshot()['enabled']  # The safe handoff callback owns the stop.
    with pytest.raises(ValueError,match='Unsupported'):
        request(info,'controls',{'script':'anything'})
    request(info,'controls',{'enabled':False})
    with pytest.raises(ValueError,match='unsupported arguments'):
        request(info,'reload-app',{'script':'anything'})
    assert request(info,'reload-app')=={'reload_queued':True}
    assert reloads==['reload','reload'] and not calls


def test_town_preinput_retry_classification_survives_bridge(bridge):
    from conquest.town_trade import TownObservationUnavailable
    service,info,calls,control=bridge
    def changed(body):
        raise TownObservationUnavailable('NPC scene changed during observation')
    service.on_town=changed
    with pytest.raises(TownObservationUnavailable):
        request(info,'town',{'action':'open','vendor_type':5,'expires_at':time.time()+3})
    def uncertain(body):
        raise ValueError('Purchase was not verified')
    service.on_town=uncertain
    with pytest.raises(ValueError) as error:
        request(info,'town',{'action':'buy','expires_at':time.time()+3})
    assert not isinstance(error.value,TownObservationUnavailable)


def test_app_lifetime_bridge_has_no_timed_expiry_and_closes_normally(tmp_path):
    operations=SimpleNamespace(session=SimpleNamespace(pid=1),dispatch=lambda *args:{})
    info=tmp_path/'continuous.json'
    service=EmbeddedBridge(operations,threading.RLock(),info,lambda:{'control':{'enabled':False}},lifetime=None)
    try:
        assert service.deadline==float('inf')
        assert json.loads(info.read_text())['expires_at'] is None
        assert request(info,'health')['embedded_controls']['control']['enabled'] is False
    finally:service.close()
    assert not service.thread.is_alive() and not info.exists()

@pytest.mark.parametrize('saved,mode,expected',[(None,'child',True),(object(),'owned',True),(object(),'child',False)])
def test_window_input_mode_tracks_applied_host_state(bridge,saved,mode,expected):
    service,info,calls,control=bridge
    queued=[];service.on_native_window=lambda detached:queued.append(detached)
    service.sync_window_mode(SimpleNamespace(saved=saved,mode=mode))
    assert service.native_probe_mode is expected
    request(info,'native-window-mode',{'detached':False})
    assert queued==[False]
    assert service.native_probe_mode is expected  # Queueing is not application.
    service.sync_window_mode(SimpleNamespace(saved=object(),mode='owned'))
    assert service.native_probe_mode is True  # Owned reattachment keeps travel input.


def test_reattach_accepts_on_intent_and_reports_applied_state(bridge):
    service,info,calls,control=bridge
    queued=[];service.on_native_window=queued.append
    service.sync_window_mode(SimpleNamespace(saved=None,mode='owned'))
    control['enabled']=True
    assert request(info,'native-window-mode',{'detached':False})=={'window_change_queued':True}
    assert queued==[False]
    assert request(info,'health')['window_mode']=='detached'
    service.sync_window_mode(SimpleNamespace(saved=object(),mode='owned'))
    assert request(info,'health')['window_mode']=='owned'
    with pytest.raises(ValueError,match='Stop farming'):
        request(info,'native-window-mode',{'detached':True})
    assert queued==[False] and control['enabled']


def test_ground_diagnostic_is_read_only_while_farming(bridge):
    service,info,calls,control=bridge
    service.on_town=lambda body:{'drops':[],'source':'read_only_memory'}
    control['enabled']=True
    assert request(info,'town',{'action':'ground-items'})['source']=='read_only_memory'
    assert calls==[]


def test_reconnect_retry_queues_only_bound_action_without_changing_farm_intent(bridge):
    service,info,calls,control=bridge
    retries=[];service.on_reconnect=lambda:retries.append(1)
    control['enabled']=True
    assert request(info,'reconnect-retry')=={'reconnect_queued':True}
    assert retries==[1] and control['enabled'] and not calls
    with pytest.raises(ValueError,match='unsupported'):
        request(info,'reconnect-retry',{'username':'unused'})
    assert retries==[1]


def test_route_input_interruption_retains_type_for_fresh_movement_retry(bridge):
 from conquest.capture import CaptureUnavailable
 service,info,calls,control=bridge
 def blocked(body):raise CaptureUnavailable('Automation stopped or manual input active')
 service.on_route_jump=blocked
 with pytest.raises(CaptureUnavailable,match='manual input'):
  request(info,'route-jump',{'expires_at':time.time()+3})
 assert not calls
