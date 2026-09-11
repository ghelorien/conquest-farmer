import json

import numpy as np

from conquest.control import FarmingControl
from conquest.navigation import TerrainMap
from conquest.route_recovery import RouteRecovery


def setup(tmp_path):
    control=FarmingControl()
    control.update({'enabled':True,'target_type_ids':[1]})
    time=[0.0]
    calls=[]
    terrain=TerrainMap(1002,20,20,np.zeros((20,20),dtype=bool),'a'*64,(),())
    dispatch=lambda kind,target,life,revision:control.dispatch_recovery(revision,lambda:calls.append((kind,target)))
    recovery=RouteRecovery(control,{'pid':1},terrain,dispatch,tmp_path/'run.json',clock=lambda:time[0])
    def observe(position=(10,10),dead=True,focused=True,**extra):
        time[0]+=.6
        data={'timestamp':time[0],'map_id':1002,'position':position,'current_hp':3 if dead else 213,
            'max_hp':213,'ghost_candidate':dead,'revive_ready_candidate':dead,
            'status':0x420 if dead else 0x200,'appearance':98 if dead else 0}
        return recovery.step({**data,**extra},focused)
    return control,time,calls,recovery,observe


def test_positive_hp_death_saves_location_and_does_not_repeat_revive(tmp_path):
    _,_,calls,recovery,observe=setup(tmp_path)
    assert observe()['state']=='verifying_revive'
    assert calls==[('revive',None)]
    observe(position=(11,10))
    assert calls==[('revive',None)]
    assert json.loads(recovery.path.read_text())['death_position']==[10,10]


def test_immediate_death_records_location_before_ghost_or_revive_ready(tmp_path):
    _,_,calls,recovery,observe=setup(tmp_path)
    result=observe(status=0x20,ghost_candidate=False,revive_ready_candidate=False,appearance=0)
    assert result['state']=='waiting_for_revive'
    assert recovery.episode['death_position']==[10,10] and not calls
    observe(position=(11,10),status=0x420)
    assert calls==[('revive',None)]
    assert recovery.episode['death_position']==[10,10]


def test_return_requires_life_confirmation_and_exact_destination(tmp_path):
    _,_,calls,recovery,observe=setup(tmp_path)
    observe()
    observe((1,1),False)
    observe((1,1),False)
    assert len(calls)==1
    observe((1,1),False)
    assert calls[-1][0]=='jump'
    for _ in range(20):
        target=calls[-1][1]
        result=observe(target,False)
        if result['state']=='recovered':
            break
    assert result['state']=='recovered'
    assert target==(10,10)
    assert recovery.episode['phase']=='completed'
    assert observe(target,False) is None


def test_off_pauses_and_focus_loss_preserves_enabled_intent(tmp_path):
    control,_,calls,recovery,observe=setup(tmp_path)
    assert observe(focused=False)['state']=='waiting_for_recovery_focus'
    assert control.snapshot()['enabled'] and not calls
    control.update({'enabled':False})
    assert observe() is None and not calls
    control.update({'enabled':True})
    observe()
    assert calls==[('revive',None)]


def test_return_jumps_at_least_eight_tiles_then_runs_short_corner(tmp_path):
    _,_,calls,recovery,observe=setup(tmp_path)
    observe()
    for _ in range(3): observe((3,1),False)
    kind,target=calls[-1]
    assert kind=='jump' and 8<=sum(abs(a-b) for a,b in zip(target,(3,1)))<=12
    observe(target,False)
    assert calls[-1][0]=='run'


def test_preinput_pause_does_not_permanently_block_recovery(tmp_path):
    from conquest.capture import CaptureUnavailable
    _,_,calls,recovery,observe=setup(tmp_path)
    original=recovery.dispatch
    def held(*args):
        raise CaptureUnavailable('Physical Control key is held; no click sent')
    recovery.dispatch=held
    observe()
    assert recovery.episode['phase']!='recovery_uncertain' and not calls
    recovery.dispatch=original
    observe()
    assert calls==[('revive',None)]


def test_map_change_blocks_and_obstructed_step_replans_a_different_path(tmp_path):
    _,time,calls,recovery,observe=setup(tmp_path)
    observe()
    assert observe((1,1),False,map_id=1003)['state']=='recovery_blocked'
    for _ in range(3): observe((1,1),False)
    assert calls[-1][0]=='jump'
    count=len(calls)
    time[0]+=6
    old_target=calls[-1][1]
    assert observe((1,1),False)['state']=='returning_after_revive'
    assert len(calls)==count+1 and calls[-1][1]!=old_target
    assert recovery.avoided


def test_new_death_during_return_records_new_location(tmp_path):
    _,_,_,recovery,observe=setup(tmp_path)
    observe()
    for _ in range(3): observe((1,1),False)
    observe((2,2),True)
    assert recovery.episode['death_position']==[2,2]
    assert recovery.episode['revive_attempts']==1


def test_restart_preserves_location_but_does_not_start_while_off(tmp_path):
    control,time,calls,recovery,observe=setup(tmp_path)
    observe()
    control.update({'enabled':False})
    other=RouteRecovery(control,{'pid':1},recovery.terrain,recovery.dispatch,recovery.path,clock=lambda:time[0])
    assert other.episode['death_position']==[10,10]
    assert other.step(None,True) is None
    different=RouteRecovery(control,{'pid':2},recovery.terrain,recovery.dispatch,recovery.path)
    assert different.episode is None


def test_town_input_accepts_checked_diagonal_jumps_and_rejects_walls(monkeypatch):
    from conquest import route_input
    from types import SimpleNamespace
    from dataclasses import dataclass
    import pytest
    @dataclass
    class Life:
        position:tuple=(10,10)
        map_id:int=1002
        dead_candidate:bool=False
    monkeypatch.setattr(route_input,'read_life',lambda *args:Life())
    calls=[]
    monkeypatch.setattr(route_input.EmbeddedRecoveryInput,'send',lambda self,*args:calls.append(args))
    terrain=TerrainMap(1002,40,40,np.zeros((40,40),dtype=bool),'',(),())
    observer=SimpleNamespace(adapter=None,health_layout=None,character='Parasite')
    send=route_input.RouteJumpInput(observer,terrain)
    body={'source':[10,10],'destination':[12,12],'map_id':1002,'expires_at':999}
    assert send(body)['movement']=='run'
    assert calls[-1][0]=='run'
    assert send({**body,'destination':[18,18]})['movement']=='jump'
    assert calls[-1][0]=='jump'
    terrain.blocked[:,11]=True
    with pytest.raises(ValueError):send(body)
    assert len(calls)==2


def test_recovery_input_dispatches_bounded_corner_runs_with_life_and_focus_guards(monkeypatch,tmp_path):
    from conquest.route_recovery import EmbeddedRecoveryInput
    from conquest import memory_life,desktop_runtime,foreground,scene_input
    from contextlib import nullcontext
    from types import SimpleNamespace
    import pytest
    life=SimpleNamespace(position=(10,10),map_id=1002,ghost_candidate=False,
        current_hp=100,max_hp=100,status=512)
    monkeypatch.setattr(memory_life,'read_life',lambda *args:life)
    monkeypatch.setattr(desktop_runtime,'physical_coordinates',nullcontext)
    monkeypatch.setattr(scene_input,'memory_player_anchor',lambda *args:(524,457))
    calls=[]
    monkeypatch.setattr(foreground,'foreground_click',lambda *a,**kw:calls.append((a,kw)))
    monkeypatch.chdir(tmp_path);(tmp_path/'reports').mkdir()
    target=SimpleNamespace(snapshot=lambda:dict(client_size=[1036,793],foreground=1,root_hwnd=1,minimized=False))
    observer=SimpleNamespace(adapter=None,health_layout=None,character='Parasite',
        bridge=SimpleNamespace(operations=SimpleNamespace(target=target)),focus_client=lambda:None)
    terrain=TerrainMap(1002,40,40,np.zeros((40,40),dtype=bool),'',(),())
    send=EmbeddedRecoveryInput(observer,None,terrain=terrain)
    observed=vars(life)
    send.send('run',(12,12),observed)
    assert len(calls)==1 and not calls[0][1]['control']
    assert calls[0][0][1:3]==(524,521)
    with pytest.raises(ValueError,match='Short return'):send.send('jump',(12,12),observed)
    terrain.blocked[12,13]=True
    with pytest.raises(ValueError,match='blocked terrain'):send.send('run',(13,13),observed)
    terrain.blocked[12,13]=False
    assert len(calls)==1
    life.current_hp=1
    send.send('run',(12,12),vars(life))
    assert len(calls)==2  # Escape remains available after the last potion.
    life.current_hp=0
    from conquest.capture import CaptureUnavailable
    with pytest.raises(CaptureUnavailable,match='Life state changed'):
        send.send('run',(12,12),vars(life))
    assert len(calls)==2


def test_town_revive_preinput_focus_race_retries_without_spending_attempt(monkeypatch):
    from conquest import travel_care
    import pytest
    care=travel_care.TravelCare.__new__(travel_care.TravelCare)
    care.info='unused';care.health_layout={};care.last_revive=-1000.;care.pending=None
    def request(*args,**kwargs):raise ValueError('Recovery waiting for game focus; no input sent')
    monkeypatch.setattr(travel_care,'request',request)
    health={'embedded_controls':{'control':{'enabled':False},
        'life':{'dead_candidate':True,'revive_ready_candidate':True,'position':[10,10]}}}
    with pytest.raises(travel_care.TravelStateChanged):care.check(health)
    assert care.last_revive==-1000.


def test_native_return_hands_control_to_healing_farmer_after_verified_revive(tmp_path):
    _,_,calls,recovery,observe=setup(tmp_path)
    recovery.delegate_return=True
    observe(position=(19,19))
    for _ in range(2):
        assert observe((1,1),False,current_hp=20)['state']=='verifying_revive'
    assert observe((1,1),False,current_hp=20) is None
    assert calls==[('revive',None)]
    assert recovery.episode['phase']=='returning_with_farmer'
    assert observe((2,2),False,current_hp=10) is None
    assert recovery.episode['phase']=='returning_with_farmer'
    observe((19,19),False)
    assert recovery.episode['phase']=='completed'


def test_new_death_during_delegated_return_preserves_fresh_death_location(tmp_path):
    _,_,calls,recovery,observe=setup(tmp_path)
    recovery.delegate_return=True;observe(position=(19,19))
    for _ in range(3):observe((1,1),False)
    observe((3,3),True)
    assert recovery.episode['death_position']==[3,3]
    assert calls==[('revive',None),('revive',None)]
