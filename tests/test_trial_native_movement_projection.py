"""Native hunting moves use the memory-verified camera anchor and checked terrain."""
import logging
from types import SimpleNamespace

import numpy as np
import pytest
import yaml

from conquest import trial
from conquest.memory_inventory import InventorySnapshot, Item
from conquest.navigation import TerrainMap


@pytest.mark.parametrize('obstruction',['none','failed_landing','solid_corner'])
def test_scatter_landing_keeps_actual_anchor_and_refuses_unchecked_segment(
        tmp_path,monkeypatch,obstruction):
    import win32api
    now=[10.0];clicks=[];failures=[]
    source=(423,455);landing=(411,444)
    ground=TerrainMap(1002,500,500,np.zeros((500,500),dtype=bool),'',(),())
    if obstruction=='solid_corner':
        ground.blocked[455,422]=True
    blocked=({(1002,(422,454)):100.0} if obstruction=='failed_landing' else {})
    config=yaml.safe_load(open('profiles/pheasant-foreground-trial.yaml',encoding='utf-8'))
    config.update(observation_mode='memory_only',adaptive_scatter=False,
                  jump_scatter=True,attack_button='right',route=[],
                  healing_enabled=False,loot_allowlist=[],interval=.15,
                  client_size=[1420,1009],player_anchor=[740,518])
    path=tmp_path/'profile.yaml';path.write_text(yaml.safe_dump(config))
    monkeypatch.setattr(trial.time,'monotonic',lambda:now[0])
    monkeypatch.setattr(trial.time,'sleep',lambda seconds:now.__setitem__(0,now[0]+seconds))
    monkeypatch.setattr(win32api,'GetAsyncKeyState',lambda _:0)
    from conquest.farmer_profile import CombatSpeed
    monkeypatch.setattr(trial,'load_combat_speed',lambda _:CombatSpeed(coherent_projection=True))
    monkeypatch.setattr('conquest.scatter_movement.scatter_landing',lambda *a,**kw:landing)
    class Session:
        def request(self,operation,body=None):
            if operation=='health':return {'input_revision':7,'window':{'hwnd':1}}
            if operation=='sample':
                fields=dict(name='Parasite',position=list(source),max_hp=[100],
                            kill_counter=[0],level=[18],map=[1002])
                return {'fields':[{'name':key,'value':value} for key,value in fields.items()]}
            assert operation=='foreground-click'
            clicks.append(body)
            now[0]+=1
            return {}
    class Inventory:
        def __init__(self,*args):pass
        def read(self):
            now[0]+=.01
            return InventorySnapshot(now[0],now[0],(Item(1,1000000,1,1,0),),
                                     Item(2,1050000,200,200,None),0,40)
    monkeypatch.setattr(trial,'MemoryInventoryReader',Inventory)
    monkeypatch.setattr(trial,'resolve_player',
                        lambda *a:dict.fromkeys(('name','position','max_hp','kill_counter','level','map'),1))
    supervisor=SimpleNamespace(recovery=SimpleNamespace(terrain=ground),
        map_id=1002,movement_obstructions=blocked,scatter_plan=None,
        targets_observation_available=True,
        observe=lambda:{'health_ratio':1.,'waiting':False,'defending':False},
        player_projection=lambda:(source,(740,518)),
        memory_targets=lambda *a:[],loot_step=lambda *a:False,
        dispatch=lambda callback,**kw:callback(),
        movement_failed=lambda source,destination:failures.append((source,destination)))
    camera=SimpleNamespace(geometry=lambda:(0,0),close=lambda:None)
    result=trial.run_trial(path,None,tmp_path/'run',1,logging.getLogger('test'),
        session_override=Session(),camera_factory=lambda *a:camera,supervisor=supervisor)
    assert result['reason']=='duration_limit'
    if obstruction=='none':
        # Centre-based clipping shortens this valid actual-anchor jump to
        # (-11,-10). The native anchor admits the checked (-12,-11) landing.
        assert clicks and clicks[0]['point']==[708,150]
        assert clicks[0]['control'] is True
        assert not failures
    else:
        assert clicks==[] and failures
