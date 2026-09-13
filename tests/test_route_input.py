import pytest
from conquest import route_input


@pytest.mark.parametrize('mode',['blocked','partial','moving','arrived','attacked'])
def test_route_progress_watchdog_keeps_care_active_and_does_not_interrupt_movement(monkeypatch,mode):
    now=[10.];care=[];inputs=[]
    monkeypatch.setattr(route_input.time,'monotonic',lambda:now[0])
    monkeypatch.setattr(route_input.time,'sleep',lambda delay:now.__setitem__(0,now[0]+delay))
    def request(info,operation,body=None):
        if operation=='route-jump':inputs.append(body);return {'issued':True,'movement':'run'}
        elapsed=now[0]-10
        x=10
        if inputs:
            if mode=='partial' and elapsed>=.3:x=11
            if mode=='moving':x=min(14,10+int(elapsed/.7))
            if mode=='arrived':x=14
        return {'window':{'foreground':1,'root_hwnd':1,'minimized':False},
                'embedded_controls':{'life':{'position':[x,10],'map_id':1011,
                    'current_hp':100-int(elapsed*10) if mode=='attacked' else 100,
                    'dead_candidate':False,'timestamp':now[0]}}}
    monkeypatch.setattr('conquest.worker.request',request)
    stepper=route_input.BridgeJumpStepper('unused',on_life=lambda h:care.append(now[0]))
    result=stepper.step_to((14,10),expected_position=(10,10))
    assert len(inputs)==1
    assert max(b-a for a,b in zip(care,care[1:]))<.06
    if mode=='attacked':
        assert not result['reached'] and .5<=now[0]-10<.7
    elif mode in ('blocked','partial'):
        assert not result['reached'] and result['error']=='Route movement stopped progressing'
        assert 1<=now[0]-10<1.5
        assert result['stalled_at']==([11,10] if mode=='partial' else [10,10])
    else:
        assert result['reached']
        assert now[0]-10>= (2.8 if mode=='moving' else .1)


@pytest.mark.parametrize('note',['Closed a shop panel; rechecking the route','Mouse control is yours; waiting for idle'])
def test_panel_after_movement_triggers_replanning_but_manual_pause_still_propagates(monkeypatch,note):
    from conquest.travel_care import TravelStateChanged
    calls=[]
    life={'position':[10,10],'map_id':1036,'current_hp':100}
    def request(info,operation,body=None):
        if operation=='route-jump':calls.append(operation);return {'movement':'run'}
        return {'embedded_controls':{'life':life}}
    def care(health):
        if calls:raise TravelStateChanged(note)
    monkeypatch.setattr('conquest.worker.request',request)
    stepper=route_input.BridgeJumpStepper('unused',on_life=care)
    if note.startswith('Closed'):
        result=stepper.step_to((20,10))
        assert not result['reached'] and 'intercepted' in result['error']
    else:
        with pytest.raises(TravelStateChanged):stepper.step_to((20,10))
    assert calls==['route-jump']
