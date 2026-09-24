import logging
from types import SimpleNamespace
import pytest
import yaml
from conquest import trial
from conquest.memory_inventory import InventorySnapshot,Item
from conquest.vision import Target


def _open_terrain():
    return SimpleNamespace(map_id=1002,width=1000,height=1000,
                           walkable=lambda point:0<=point[0]<1000 and 0<=point[1]<1000)


@pytest.mark.parametrize("urgent",[False,True])
@pytest.mark.parametrize("attack_button",["left","right"])
def test_surround_interrupts_unfinished_attack_without_waiting_for_damage_or_timeout(tmp_path,monkeypatch,attack_button,urgent):
    import win32api
    now=[10.];calls=[];position=[423,455];jumped=[None]
    config=yaml.safe_load(open('profiles/pheasant-foreground-trial.yaml'))
    config.update(observation_mode='memory_only',kite_when_surrounded=True,attack_button=attack_button,
        route=[],healing_enabled=False,loot_allowlist=[],attack_progress_timeout=5,
        client_size=[1036,793],player_anchor=[518,396])
    path=tmp_path/'profile.yaml';path.write_text(yaml.safe_dump(config))
    monkeypatch.setattr(trial.time,'monotonic',lambda:now[0])
    monkeypatch.setattr(trial.time,'sleep',lambda t:now.__setitem__(0,now[0]+t))
    monkeypatch.setattr(win32api,'GetAsyncKeyState',lambda _:0)
    def no_pixels(*a,**k):pytest.fail('Surround escape must only use memory')
    monkeypatch.setattr(trial.cv2,'imread',no_pixels)
    class Session:
        def request(self,operation,body=None):
            if operation=='health':return {'input_revision':7,'window':{'hwnd':1}}
            if operation=='sample':
                counter=int(jumped[0] is not None and now[0]-jumped[0]>.6)
                values=dict(name='Parasite',position=list(position),max_hp=[100],kill_counter=[counter],level=[18],map=[1002])
                return {'fields':[{'name':k,'value':v} for k,v in values.items()]}
            assert operation=='foreground-click'
            calls.append((now[0],body))
            if body['control']:
                position[:]=[433,455];jumped[0]=now[0]
            return {}
    class Inventory:
        def __init__(self,*a):pass
        def read(self):
            now[0]+=.01
            return InventorySnapshot(now[0],now[0],(Item(1,1000000,1,1,0),),Item(2,1050000,200-len(calls),200,None),0,40)
    monkeypatch.setattr(trial,'MemoryInventoryReader',Inventory)
    monkeypatch.setattr(trial,'resolve_player',lambda *a:dict.fromkeys(('name','position','max_hp','kill_counter','level','map'),1))
    camera=SimpleNamespace(geometry=lambda:(0,0),read=no_pixels,close=lambda:None)
    supervisor=SimpleNamespace(last_target=None,recovery=SimpleNamespace(terrain=_open_terrain()),
        observe=lambda:{'health_ratio':1.,'waiting':False,'defending':False},
        memory_targets=lambda *a:[] if calls else [Target('Pheasant',600,400,1)],
        ranged_escape=lambda *a:(433,455) if calls and jumped[0] is None else None,
        dispatch=lambda callback,**kwargs:callback(),loot_step=lambda *a:False,finish_target=lambda *a:None)
    supervisor.urgent_banking=lambda inventory:urgent
    result=trial.run_trial(path,None,tmp_path/'run',2,logging.getLogger('test'),
        session_override=Session(),camera_factory=lambda *a:camera,supervisor=supervisor)
    if urgent:
        assert result['reason']=='valuable_banking_required'
        assert calls==[]  # No further attack or movement after inventory confirmation.
        return
    assert result['reason']=='duration_limit'
    assert len(calls)==2 and not calls[0][1]['control'] and calls[1][1]['control']
    assert calls[1][0]-calls[0][0]<.3
    assert calls[0][1]['button']==attack_button
    assert result['confirmed_kills']==1  # In-flight hit after escape remains counted.


@pytest.mark.parametrize('adaptive,jump_scatter',[(False,False),(True,False),(True,True)])
@pytest.mark.parametrize('reuse_case',['disabled','fresh','expired','unavailable'])
def test_scatter_keeps_casting_on_survivors_before_looting_or_patrolling(tmp_path,monkeypatch,adaptive,jump_scatter,reuse_case):
    import win32api
    now=[10.];calls=[];counter=[0]
    config=yaml.safe_load(open('profiles/pheasant-foreground-trial.yaml'))
    config.update(observation_mode='memory_only',kite_when_surrounded=True,attack_button='right',adaptive_scatter=adaptive,jump_scatter=jump_scatter,
        route=[],healing_enabled=False,loot_allowlist=[],attack_progress_timeout=5,
        interval=.15,client_size=[1036,793],player_anchor=[518,396])
    path=tmp_path/'profile.yaml';path.write_text(yaml.safe_dump(config))
    monkeypatch.setattr(trial.time,'monotonic',lambda:now[0])
    monkeypatch.setattr(trial.time,'sleep',lambda t:now.__setitem__(0,now[0]+t))
    monkeypatch.setattr(win32api,'GetAsyncKeyState',lambda _:0)
    def forbidden(*a,**k):pytest.fail('Live survivors must get Scatter before looting or visual reads')
    monkeypatch.setattr(trial.cv2,'imread',forbidden)
    monkeypatch.setattr('conquest.scatter_movement.scatter_landing',lambda *a,**kw:None)
    class Session:
        def request(self,operation,body=None):
            if operation=='health':return {'input_revision':7,'window':{'hwnd':1}}
            if operation=='sample':
                values=dict(name='Parasite',position=[423,455],max_hp=[100],kill_counter=[counter[0]],level=[25],map=[1002])
                return {'fields':[{'name':k,'value':v} for k,v in values.items()]}
            assert operation=='foreground-click'
            expected='left' if adaptive and len(calls)>=3 else 'right'
            assert body['button']==expected and not body['control']
            calls.append(now[0])
            if len(calls)==1:counter[0]+=1  # Another monster dies, aim target survives.
            return {}
    class Inventory:
        def __init__(self,*a):pass
        def read(self):
            now[0]+=.01
            return InventorySnapshot(now[0],now[0],(Item(1,1000000,1,1,0),),Item(2,1050000,200-len(calls),200,None),0,40)
    monkeypatch.setattr(trial,'MemoryInventoryReader',Inventory)
    monkeypatch.setattr(trial,'resolve_player',lambda *a:dict.fromkeys(('name','position','max_hp','kill_counter','level','map'),1))
    camera=SimpleNamespace(geometry=lambda:(0,0),read=forbidden,close=lambda:None)
    from conquest.attack_strategy import AttackStrategy
    strategy=AttackStrategy();strategy.set_context('level25')
    def survivor():return Target('Pheasant',600,400,1,25,1000,(427,455),100-20*len(calls))
    from conquest.farmer_profile import CombatSpeed
    # Exercise adaptive fallback explicitly; the default now forces jump/Scatter.
    monkeypatch.setattr(trial,'load_combat_speed',lambda _:CombatSpeed(force_jump_scatter=False,
        scene_reuse_seconds=0 if reuse_case=='disabled' else .15))
    scans=[];escape_scans=[]
    def scan(*args):
        scans.append(now[0]);return [survivor()]
    def escape(*args):
        escape_scans.append(len(scans))
        if reuse_case=='expired':now[0]+=.151
        return None
    loot_calls=[]
    def loot(*args):
        assert adaptive and strategy.button('Pheasant')=='left'
        loot_calls.append(len(calls))
        return False

    supervisor=SimpleNamespace(last_target=None,recovery=SimpleNamespace(terrain=_open_terrain()),
        observe=lambda:{'health_ratio':1.,'waiting':False,'defending':False},
        memory_targets=scan,ranged_escape=escape,attack_strategy=lambda:strategy,
        targets_observation_available=reuse_case!='unavailable',
        dispatch=lambda callback,**kwargs:callback(),loot_step=loot,finish_target=lambda *a:None)
    result=trial.run_trial(path,None,tmp_path/'run',3,logging.getLogger('test'),
        session_override=Session(),camera_factory=lambda *a:camera,supervisor=supervisor)
    assert result['reason']=='duration_limit' and result['confirmed_kills']==1
    assert len(calls)>=3
    if reuse_case=='fresh':
        assert escape_scans==list(range(1,len(escape_scans)+1))
    else:
        assert any(b-a>1 for a,b in zip(escape_scans,escape_scans[1:]))
    assert all(.8<=b-a<1.2 for a,b in zip(calls,calls[1:]))
    if adaptive:
        assert len(calls)==4 and strategy.button('Pheasant')=='left'
        assert loot_calls and min(loot_calls)>=3
    else:assert not loot_calls


def test_defense_without_attackable_target_keeps_patrol_movement(tmp_path,monkeypatch):
    import win32api
    now=[10.];calls=[]
    config=yaml.safe_load(open('profiles/pheasant-foreground-trial.yaml'))
    config.update(observation_mode='memory_only',route=[[435,455]],healing_enabled=False,
        loot_allowlist=[],client_size=[1036,793],player_anchor=[518,396])
    path=tmp_path/'profile.yaml';path.write_text(yaml.safe_dump(config))
    monkeypatch.setattr(trial.time,'monotonic',lambda:now[0])
    monkeypatch.setattr(trial.time,'sleep',lambda t:now.__setitem__(0,now[0]+t))
    monkeypatch.setattr(win32api,'GetAsyncKeyState',lambda _:0)
    class Session:
        def request(self,operation,body=None):
            if operation=='health':return {'input_revision':7,'window':{'hwnd':1}}
            if operation=='sample':
                values=dict(name='Parasite',position=[423,455],max_hp=[100],kill_counter=[0],level=[18],map=[1002])
                return {'fields':[{'name':k,'value':v} for k,v in values.items()]}
            assert operation=='foreground-click'
            calls.append(body);return {}
    class Inventory:
        def __init__(self,*a):pass
        def read(self):
            now[0]+=.01
            return InventorySnapshot(now[0],now[0],(Item(1,1000000,1,1,0),),Item(2,1050000,200,200,None),0,40)
    monkeypatch.setattr(trial,'MemoryInventoryReader',Inventory)
    monkeypatch.setattr(trial,'resolve_player',lambda *a:dict.fromkeys(('name','position','max_hp','kill_counter','level','map'),1))
    camera=SimpleNamespace(geometry=lambda:(0,0),close=lambda:None)
    supervisor=SimpleNamespace(last_target=None,recovery=SimpleNamespace(terrain=_open_terrain()),
        observe=lambda:{'health_ratio':1.,'waiting':False,'defending':True},
        memory_targets=lambda *a:[],patrol_step=lambda *a,**k:(435,455),
        dispatch=lambda callback,**kwargs:callback())
    trial.run_trial(path,None,tmp_path/'run',1,logging.getLogger('test'),
        session_override=Session(),camera_factory=lambda *a:camera,supervisor=supervisor)
    assert calls and all(call['control'] is True for call in calls)


@pytest.mark.parametrize('airborne',[False,True,'short'])
@pytest.mark.parametrize('character',['Parasite','OtherFarmer'])
@pytest.mark.parametrize('group_size',[1,3])
@pytest.mark.parametrize('fail_first_jump',[False,True])
def test_jump_scatter_repositions_then_casts_again(tmp_path,monkeypatch,group_size,fail_first_jump,character,airborne):
    import win32api
    attack_button='right'
    now=[10.];calls=[];position=[423,455];jumped=[None];failed=[False];flight=[None]
    config=yaml.safe_load(open('profiles/pheasant-foreground-trial.yaml'))
    config.update(character=character,observation_mode='memory_only',kite_when_surrounded=True,jump_scatter=True,attack_button=attack_button,
        route=[],healing_enabled=False,loot_allowlist=[],attack_progress_timeout=5,
        client_size=[1036,793],player_anchor=[518,396])
    path=tmp_path/'profile.yaml';path.write_text(yaml.safe_dump(config))
    monkeypatch.setattr(trial.time,'monotonic',lambda:now[0])
    monkeypatch.setattr(trial.time,'sleep',lambda t:now.__setitem__(0,now[0]+t))
    monkeypatch.setattr(win32api,'GetAsyncKeyState',lambda _:0)
    def no_pixels(*a,**k):pytest.fail('Surround escape must only use memory')
    monkeypatch.setattr(trial.cv2,'imread',no_pixels)
    class Session:
        def request(self,operation,body=None):
            if operation=='health':return {'input_revision':7,'window':{'hwnd':1}}
            if operation=='sample':
                if flight[0]:
                    source,destination,issued=flight[0]
                    if now[0]-issued>=.9:
                        position[:]=[round((source[0]+destination[0])/2),source[1]] if airborne=='short' else destination
                        flight[0]=None
                    elif now[0]-issued>=.1:position[:]=[round((source[0]+destination[0])/2),source[1]]
                counter=int(jumped[0] is not None and now[0]-jumped[0]>.6)
                values=dict(name=character,position=list(position),max_hp=[100],kill_counter=[counter],level=[18],map=[1002])
                return {'fields':[{'name':k,'value':v} for k,v in values.items()]}
            assert operation=='foreground-click'
            if body['control'] and fail_first_jump and not failed[0]:
                from conquest.capture import CaptureUnavailable
                failed[0]=True
                raise CaptureUnavailable('Game lost focus; no input sent')
            calls.append((now[0],body))
            if body['control']:
                destination=[position[0]+10 if position[0]<=423 else position[0]-10,455]
                if airborne:flight[0]=(list(position),destination,now[0])
                else:position[:]=destination
                if jumped[0] is None:jumped[0]=now[0]
            return {}
    class Inventory:
        def __init__(self,*a):pass
        def read(self):
            now[0]+=.01
            return InventorySnapshot(now[0],now[0],(Item(1,1000000,1,1,0),),Item(2,1050000,200-len(calls),200,None),0,40)
    monkeypatch.setattr(trial,'MemoryInventoryReader',Inventory)
    monkeypatch.setattr(trial,'resolve_player',lambda *a:dict.fromkeys(('name','position','max_hp','kill_counter','level','map'),1))
    camera=SimpleNamespace(geometry=lambda:(0,0),read=no_pixels,close=lambda:None)
    supervisor=SimpleNamespace(last_target=None,recovery=SimpleNamespace(terrain=_open_terrain()),
        observe=lambda:{'health_ratio':1.,'waiting':False,'defending':False},
        memory_targets=lambda *a:[Target('Pheasant',600+i,400,1,1+i,1000+i,(435,455),100) for i in range(group_size)],
        ranged_escape=lambda *a:None,
        dispatch=lambda callback,**kwargs:callback(),loot_step=lambda *a:False,finish_target=lambda *a:None)
    # A better landing is always available, even when fewer than three targets
    # are in range. It must not cause repeated jumps without a cast.
    monkeypatch.setattr('conquest.scatter_movement.scatter_landing',lambda *a,**kw:(position[0]+10 if position[0]<=423 else position[0]-10,455))
    result=trial.run_trial(path,None,tmp_path/'run',4 if airborne=='short' else 3,logging.getLogger('test'),
        session_override=Session(),camera_factory=lambda *a:camera,supervisor=supervisor)
    assert result['reason']=='duration_limit'
    assert len(calls)>=3 and not calls[0][1]['control'] and calls[1][1]['control']
    assert calls[2][1]['button']=='right' and not calls[2][1]['control']
    gap=calls[2][0]-calls[1][0]
    if airborne=='short' and character!='Parasite':assert 1.5<=gap<1.7
    elif airborne and character!='Parasite':assert .9<=gap<1.1
    else:assert (.28 if character=='Parasite' else .44)<=gap<(.44 if character=='Parasite' else .55)
    if airborne=='short':
        movements=[t for t,body in calls if body['control']]
        assert len(movements)>=2  # A partial landing must never retain 'moving' forever.
        assert 1.5<=movements[1]-movements[0]<(2.2 if character=='Parasite' else 2.6)
    else:
        assert all(a[1]['control']!=b[1]['control'] for a,b in zip(calls,calls[1:]))
    assert .8<=calls[1][0]-calls[0][0]<1.2
    assert calls[0][1]['button']==attack_button
    assert result['confirmed_kills']==1  # In-flight hit after escape remains counted.

