from types import SimpleNamespace as NS
import numpy as np
import pytest
from conquest.navigation import TerrainMap,clear_segment
from conquest.market_navigation import recovery_landing
from conquest.overnight import OvernightLoop,OvernightStopped
from conquest.routes import RouteLibrary


def terrain():
    return TerrainMap(1036,300,300,np.zeros((300,300),dtype=bool),'',(),())


def test_alternate_is_long_visible_and_never_repeats_failed_landing():
    t=terrain();used=set();directions=set();failed={(212,200)}
    for _ in range(3):
        p=recovery_landing(t,(200,200),(230,200),(518,396),failed=failed,used=used)
        assert p not in used|failed and 8<=max(abs(a-b) for a,b in zip(p,(200,200)))<=12
        assert clear_segment(t,(200,200),p)
        length=max(abs(p[0]-200),abs(p[1]-200));direction=((p[0]-200)/length,(p[1]-200)/length)
        assert direction not in directions;directions.add(direction)
        used.add(p)


def test_recovery_preserves_collision_and_map_guards():
    t=terrain();t.blocked[:]=True;t.blocked[200,200]=False
    assert recovery_landing(t,(200,200),(230,200),(518,396)) is None


def test_failed_bridge_jump_keeps_running_until_crossing_cleared(monkeypatch):
    from conquest import scene_input,city_travel
    monkeypatch.setattr(scene_input,'memory_player_anchor',lambda *a:(518,396))
    monkeypatch.setattr(city_travel,'service_role',lambda *a:None)
    loop=OvernightLoop.__new__(OvernightLoop);loop.route=RouteLibrary().load('bandit')
    loop.terrain=terrain();loop.terrain.map_id=1011
    position=[200,200];steps=[]
    loop.living=lambda:{'embedded_controls':{'life':{'position':position.copy()}}}
    loop.care=NS(check=lambda h:None,session=None);loop.record=lambda *a,**kw:None
    def step(target,expected_position):
        distance=max(abs(a-b) for a,b in zip(position,target))
        steps.append((tuple(position),distance))
        if len(steps)==1:return {'reached':False}
        if position[0]<212:assert distance<=4,'Retried jump before crossing cleared'
        position[:]=target;return {'reached':True}
    loop.stepper=NS(step_to=step)
    loop.travel((230,200))
    assert position==[230,200]
    assert any(source[0]>=212 and distance>=8 for source,distance in steps)
    t=terrain();t.map_id=1000
    assert recovery_landing(t,(200,200),(230,200),(518,396)) is None


@pytest.mark.parametrize('map_id',[1036,1011])
@pytest.mark.parametrize('mode',['recover','blocked','manual_stop'])
def test_market_travel_recovers_early_and_remains_bounded(monkeypatch,mode,map_id):
    from conquest import scene_input,overnight,city_travel
    monkeypatch.setattr(scene_input,'memory_player_anchor',lambda *a:(518,396))
    monkeypatch.setattr(city_travel,'service_role',lambda *a:None)
    clock=[0.];monkeypatch.setattr(overnight.time,'monotonic',lambda:clock[0])
    loop=OvernightLoop.__new__(OvernightLoop);loop.route=RouteLibrary().load('bandit')
    loop.terrain=terrain();position=[200,200];steps=[];events=[]
    loop.terrain.map_id=map_id
    recovery_event='market_movement_recovery' if map_id==1036 else 'town_movement_recovery'
    def living():
        if mode=='manual_stop' and len(steps)>=2:raise OvernightStopped('Stopped by user')
        return {'embedded_controls':{'life':{'position':position.copy()}}}
    loop.living=living;loop.care=NS(check=lambda h:None,session=None)
    loop.record=lambda e,**kw:events.append((e,kw))
    def step(target,expected_position):
        steps.append(target);clock[0]+=1
        if mode=='recover' and len(steps)>2:
            position[:]=target;return {'reached':True}
        return {'reached':False}
    loop.stepper=NS(step_to=step)
    if mode=='recover':
        loop.travel((230,200));assert position==[230,200]
        assert next(kw for e,kw in events if e==recovery_event)['attempt']==1
        assert 8<=max(abs(a-b) for a,b in zip(steps[2],(200,200)))<=12
    elif mode=='blocked':
        with pytest.raises(ValueError,match='obstructed|no position progress'):loop.travel((230,200))
        assert 1<=sum(e==recovery_event for e,kw in events)<=3
        assert len(steps)<90
    else:
        with pytest.raises(OvernightStopped):loop.travel((230,200))
        assert len(steps)==2
