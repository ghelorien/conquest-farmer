import json
import logging
import sqlite3
from types import SimpleNamespace as NS

import pytest
import yaml

from conquest import trial
from conquest.memory_inventory import InventorySnapshot,Item


@pytest.mark.parametrize('scan_delay',[.01,.4])
def test_native_targets_use_their_own_age_after_slow_skill_checks(tmp_path,monkeypatch,scan_delay):
    import win32api
    from conquest.vision import Target
    now=[10.];clicks=[]
    config=yaml.safe_load(open('profiles/pheasant-foreground-trial.yaml'))
    config.update(observation_mode='memory_only',route=[],healing_enabled=False,
        loot_allowlist=[],client_size=[1036,793],player_anchor=[518,396])
    path=tmp_path/'profile.yaml';path.write_text(yaml.safe_dump(config))
    monkeypatch.setattr(trial.time,'monotonic',lambda:now[0])
    monkeypatch.setattr(trial.time,'sleep',lambda dt:now.__setitem__(0,now[0]+dt))
    monkeypatch.setattr(win32api,'GetAsyncKeyState',lambda key:0)
    class Session:
        def request(self,operation,body=None):
            if operation=='health':return {'input_revision':7,'window':{'hwnd':1}}
            if operation=='sample':
                values=dict(name='Parasite',position=[423,455],max_hp=[100],kill_counter=[0],level=[7],map=[1002])
                return {'fields':[{'name':k,'value':v} for k,v in values.items()]}
            assert operation=='foreground-click'
            clicks.append(body);return {}
    class Inventory:
        def __init__(self,*args):pass
        def read(self):return InventorySnapshot(now[0],now[0],(),Item(2,1050000,200,200,None),0,40)
    monkeypatch.setattr(trial,'MemoryInventoryReader',Inventory)
    monkeypatch.setattr(trial,'resolve_player',lambda *a:dict.fromkeys(('name','position','max_hp','kill_counter','level','map'),1))
    def skill(dispatch):now[0]+=.4;return False
    def targets(*args):now[0]+=scan_delay;return [Target('Pheasant',600,400,1)]
    supervisor=NS(last_target=None,finish_target=lambda *a:None,recovery=NS(terrain=NS(width=1000,height=1000)),
        observe=lambda:{'waiting':False,'health_ratio':1},xp_step=skill,
        memory_targets=targets,loot_step=lambda *a:False,dispatch=lambda cb,**kw:cb())
    camera=NS(geometry=lambda:(0,0),close=lambda:None)
    result=trial.run_trial(path,None,tmp_path/'out',2,logging.getLogger('test'),
        session_override=Session(),camera_factory=lambda *a:camera,supervisor=supervisor)
    assert result['reason']=='duration_limit'
    assert bool(clicks)==(scan_delay<.35)


@pytest.mark.parametrize('manual_stop',[False,True])
def test_slow_native_samples_keep_recovery_alive_then_heal(tmp_path,monkeypatch,manual_stop):
    import win32api
    now=[10.];reads=[];keys=[];observations=[];revived=[]
    output=tmp_path/'output'
    profile=yaml.safe_load(open('profiles/pheasant-foreground-trial.yaml'))
    profile.update(observation_mode='memory_only',route=[])
    path=tmp_path/'profile.yaml';path.write_text(yaml.safe_dump(profile))
    monkeypatch.setattr(trial.time,'monotonic',lambda:now[0])
    monkeypatch.setattr(trial.time,'sleep',lambda s:now.__setitem__(0,now[0]+s))
    monkeypatch.setattr(win32api,'GetAsyncKeyState',lambda key:0)
    class Session:
        def __init__(self,*args):pass
        def request(self,name,args=None):
            if name=='health':return {'input_revision':6,'window':{'hwnd':1}}
            if name=='sample':
                values=dict(name='Parasite',position=[423,455],max_hp=[100],kill_counter=[0],level=[7],map=[1002])
                return {'fields':[{'name':k,'value':v} for k,v in values.items()]}
            assert name=='foreground-key' and args['vk']==112
            assert len(reads)>4, 'Never heal or attack from an expired inventory'
            keys.append(now[0])
    class Inventory:
        def __init__(self,*args):pass
        def read(self):
            reads.append(1);now[0]+=.1
            old=now[0]-.95 if len(reads)<=4 else now[0]
            if manual_stop and len(reads)==3:(output/'stop.request').write_text('Manual Stop')
            potions=() if keys else (Item(1,1000000,1,1,0),)
            return InventorySnapshot(old,now[0],potions,Item(99,1050000,200,200,None),0,40)
    def observe():
        observations.append(len(reads))
        if len(reads)==4 and not revived:
            revived.append(True)  # Stand-in for the supervisor's death/revival step.
            return {'waiting':True,'health_ratio':0}
        return {'waiting':False,'health_ratio':.9 if keys else .3}
    supervisor=NS(recovery=NS(terrain=NS(width=1000,height=1000)),observe=observe,
                  memory_targets=lambda *a:[],loot_step=lambda *a:False,
                  dispatch=lambda callback,**kw:callback())
    monkeypatch.setattr(trial,'WorkerPointerSession',Session)
    monkeypatch.setattr(trial,'MemoryInventoryReader',Inventory)
    monkeypatch.setattr(trial,'resolve_player',lambda *a:dict.fromkeys(('name','position','max_hp','kill_counter','level','map'),1))
    camera=NS(geometry=lambda:(0,0),close=lambda:None)
    monkeypatch.setattr(trial,'DesktopFrames',lambda *a:camera)
    result=trial.run_trial(path,'unused',output,4,logging.getLogger('test'),supervisor=supervisor)
    events=[e for e, in sqlite3.connect(output/'trial.sqlite3').execute('select event from events')]
    if manual_stop:
        assert result['reason']=='requested_stop' and not keys and not revived
    else:
        assert result['reason']=='duration_limit' and keys and revived
        assert 'state_recovery_wait' in events and 'state_recovery_resumed' in events
        assert len(observations)>4
