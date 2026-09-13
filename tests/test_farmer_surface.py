import time
from types import SimpleNamespace as N
import pytest
from conquest.merchants.farmer_surface import focus_idle_farmer


def ui(enabled=False, safe=True):
    calls=[]
    state={'enabled':enabled,'revision':4}
    app=N(closing=False,control=N(snapshot=lambda:dict(state)),show_game=lambda:calls.append('focus') or True)
    return N(closed=False,app=app,coordinator=N(check=lambda:None,owner=None),safe_to_yield=lambda:safe),calls,state


def test_idle_focus_preserves_off():
    u,c,s=ui()
    assert focus_idle_farmer(u,queued_at=time.monotonic())=={'focused':True,'farming_enabled':False}
    assert c==['focus'] and s=={'enabled':False,'revision':4}


@pytest.mark.parametrize('enabled,safe',[(True,True),(False,False)])
def test_no_focus_during_active_work(enabled,safe):
    u,c,s=ui(enabled,safe)
    with pytest.raises(ValueError):focus_idle_farmer(u,queued_at=time.monotonic())
    assert not c


def test_expired_request_cannot_focus_later():
    u,c,s=ui()
    with pytest.raises(ValueError):focus_idle_farmer(u,queued_at=time.monotonic()-4)
    assert not c


def test_observation_and_execution_telemetry_do_not_change_idle_intent():
    u,c,s=ui()
    s.update(target_ids=[1],target_type_ids=[2],input_mode='background',
             resolved_target_ids=[],execution_state='off',note='Farming is off')
    def focus():
        c.append('focus')
        s.update(resolved_target_ids=[44],execution_state='waiting_for_targets',
                 note='Nearby target observation changed')
        return True
    u.app.show_game=focus
    assert focus_idle_farmer(u,queued_at=time.monotonic())=={
        'focused':True,'farming_enabled':False}
    assert s['revision']==4 and s['resolved_target_ids']==[44]


@pytest.mark.parametrize('change',[
    {'enabled':True},{'revision':5},{'target_ids':[99]},
    {'target_type_ids':[88]},{'input_mode':'foreground'},{'paused':True}])
def test_actual_intent_change_during_focus_is_rejected(change):
    u,c,s=ui()
    s.update(target_ids=[1],target_type_ids=[2],input_mode='background')
    def focus():s.update(change);return True
    u.app.show_game=focus
    with pytest.raises(ValueError,match='intent changed'):
        focus_idle_farmer(u,queued_at=time.monotonic())


def test_manual_stop_after_native_focus_is_rechecked():
    checks=[];u,c,s=ui()
    def check():
        checks.append(True)
        if len(checks)==2:raise ValueError('Manual Stop')
    u.coordinator.check=check
    with pytest.raises(ValueError,match='Manual Stop'):
        focus_idle_farmer(u,queued_at=time.monotonic())
    assert c==['focus']
