from contextlib import nullcontext
from dataclasses import dataclass,replace
from types import SimpleNamespace
import threading

import pytest

from conquest.control import FarmingControl
from conquest.capture import CaptureUnavailable
from conquest import native_farm


@dataclass
class Life:
    current_hp:int=3
    max_hp:int=213
    dead_candidate:bool=True
    ghost_candidate:bool=False
    position:tuple=(10,10)
    map_id:int=1002


def setup(monkeypatch):
    from conquest import monster_health
    monkeypatch.setattr(monster_health,'read_monster_health',lambda *args:81)
    life=Life()
    monkeypatch.setattr(native_farm,'logical_coordinates',nullcontext)
    monkeypatch.setattr(native_farm,'read_life',lambda *args:life)
    monkeypatch.setattr('conquest.scene_input.memory_player_anchor',lambda *args:(518,396))
    observer=SimpleNamespace(lock=threading.RLock(),adapter=None,health_layout=None,character='Parasite',
        operations=SimpleNamespace(target=SimpleNamespace(snapshot=lambda:{'foreground':1,'root_hwnd':1,'minimized':False})))
    control=FarmingControl()
    control.update({'enabled':True,'target_type_ids':[1]})
    notifications=[]
    recovery=SimpleNamespace(step=lambda state,focused:{'state':'waiting_for_revive','note':'Waiting for Revive'})
    supervisor=native_farm.NativeFarmSupervisor(observer,control,recovery,lambda *args:notifications.append(args))
    supervisor.ownership_guard=lambda:None
    return supervisor,control,life,notifications


def test_loot_click_uses_memory_camera_anchor_in_resized_viewport(monkeypatch):
    from conquest.memory_ground import GroundItem
    supervisor,_,life,_=setup(monkeypatch)
    life.dead_candidate=False
    supervisor.observer.adapter=SimpleNamespace(viewport_size=lambda:(1420,1009))
    monkeypatch.setattr('conquest.scene_input.memory_player_anchor',lambda *args:(950,600))
    meteor=GroundItem(1,1000,1088001,(11,10))
    supervisor.ground_items=lambda:(meteor,)
    clicks=[]
    supervisor.loot_step(SimpleNamespace(silver=0,items=(),capacity=40),(10,10),
                         lambda point,**kw:clicks.append(point))
    assert clicks==[(982,616)]


@pytest.mark.parametrize('kind',sorted(__import__('conquest.valuables',fromlist=['DRAGONBALL_TYPES']).DRAGONBALL_TYPES))
def test_every_dragonball_has_priority_over_closer_super_gear(monkeypatch,kind):
    from conquest.memory_ground import GroundItem
    supervisor,_,life,_=setup(monkeypatch)
    life.dead_candidate=False
    supervisor.observer.adapter=SimpleNamespace(viewport_size=lambda:(1420,1009))
    monkeypatch.setattr('conquest.scene_input.memory_player_anchor',lambda *args:(950,600))
    supervisor.ground_items=lambda:(GroundItem(1,1000,130009,(10,10)),GroundItem(2,2000,kind,(11,10)))
    clicks=[]
    supervisor.loot_step(SimpleNamespace(silver=0,items=(),capacity=40),(10,10),
                         lambda point,**kw:clicks.append(point))
    assert clicks==[(982,616)]


def test_positive_hp_death_blocks_combat_before_ghost_animation(monkeypatch):
    supervisor,control,life,notifications=setup(monkeypatch)
    assert supervisor.observe()['waiting']
    assert control.snapshot()['execution_state']=='waiting_for_revive'
    with pytest.raises(CaptureUnavailable):
        supervisor.dispatch(lambda:pytest.fail('Dead player must not attack'))


@pytest.mark.parametrize('change',[{'enabled':False},{'target_type_ids':[2]}])
def test_off_or_target_change_cancels_input_prepared_earlier(monkeypatch,change):
    supervisor,control,life,_=setup(monkeypatch)
    life.dead_candidate=False
    control.update(change)
    assert supervisor.observe()['stop']
    with pytest.raises(CaptureUnavailable):
        supervisor.dispatch(lambda:pytest.fail('Stale selected route must not attack'))


def test_recovery_completes_before_combat_resumes(monkeypatch):
    supervisor,control,life,notifications=setup(monkeypatch)
    life.dead_candidate=False
    life.current_hp=213
    assert supervisor.observe()['waiting']
    supervisor.recovery.step=lambda *args:None
    assert supervisor.observe()=={'waiting':False,'health_ratio':1}
    assert supervisor.dispatch(lambda:'attack')=='attack'


def test_focus_loss_and_death_do_not_change_farming_intent(monkeypatch):
    supervisor,control,life,_=setup(monkeypatch)
    supervisor.observer.operations.target.snapshot=lambda:{'foreground':99,'root_hwnd':1,'minimized':False}
    assert supervisor.observe()['waiting']
    assert control.snapshot()['enabled']
    life.dead_candidate=False
    supervisor.recovery.step=lambda *args:None
    assert supervisor.observe()['waiting']
    assert control.snapshot()['enabled']


def test_target_recheck_does_not_substitute_a_new_monster_at_same_point(monkeypatch):
    from conquest.vision import Target
    supervisor,control,life,_=setup(monkeypatch)
    life.dead_candidate=False
    monster=SimpleNamespace(name='Pheasant',entity_id=25,object_address=1000,type_id=1,draw_position=(500,400))
    supervisor.observer.entities=SimpleNamespace(layout=None,read=lambda:SimpleNamespace(monsters=[monster]))
    first=supervisor.match_targets([Target('Pheasant',500,390,.88)])[0]
    assert first.entity_id==25
    monster.entity_id=26
    with pytest.raises(CaptureUnavailable):
        supervisor.dispatch(lambda:pytest.fail('Replacement monster must not inherit old target'),target=first)


@pytest.mark.parametrize('change',[None,'replaced','far','hud','dead'])
def test_aim_refresh_tracks_same_live_monster_without_bypassing_guards(monkeypatch,change):
    from conquest.vision import Target
    from conquest import monster_health
    supervisor,_,life,_=setup(monkeypatch);life.dead_candidate=False;life.ghost_candidate=False
    monster=SimpleNamespace(name='Pheasant',entity_id=25,object_address=1000,type_id=1,
        position=(13,10),draw_position=(650,410))
    target=Target('Pheasant',600,390,1,25,1000,(12,10),100)
    supervisor.observer.entities=SimpleNamespace(layout=None,read=lambda:SimpleNamespace(monsters=[monster]))
    if change=='replaced':monster.object_address=2000
    if change=='far':monster.position=(24,10)
    if change=='hud':monster.draw_position=(650,730)
    if change=='dead':monkeypatch.setattr(monster_health,'read_monster_health',lambda *a:0)
    points=[];clicks=[]
    def execute():return supervisor.dispatch(lambda:clicks.append(True),target=target,
        retarget=lambda t:points.append((t.x,t.y)),attack_range=10)
    if change:
        with pytest.raises(CaptureUnavailable):execute()
        assert not points and not clicks
    else:
        execute();assert points==[(650,410)] and clicks==[True]
        assert supervisor.last_target.world_position==(13,10)


@pytest.mark.parametrize('operation',['observe','dispatch'])
def test_moving_during_memory_sample_retries_without_input_or_switching_off(monkeypatch,operation):
    supervisor,control,life,_=setup(monkeypatch)
    def moving(*args):
        raise ValueError('Life state changed during observation')
    monkeypatch.setattr(native_farm,'read_life',moving)
    with pytest.raises(CaptureUnavailable):
        if operation=='observe':
            supervisor.observe()
        else:
            supervisor.dispatch(lambda:pytest.fail('Incoherent life sample must not allow input'))
    assert control.snapshot()['enabled']


def test_stationary_damage_enters_defense_and_expires_after_damage_stops(monkeypatch):
    supervisor,_,life,_=setup(monkeypatch)
    now=[100.0]
    monkeypatch.setattr(native_farm.time,'monotonic',lambda:now[0])
    life.dead_candidate=False
    life.current_hp=213
    supervisor.recovery.step=lambda *args:None
    assert not supervisor.observe().get('defending')
    life.current_hp=190
    assert supervisor.observe()['defending']
    now[0]+=9
    assert not supervisor.observe().get('defending')


def test_damage_during_displacement_does_not_invent_a_stationary_attacker(monkeypatch):
    supervisor,_,life,_=setup(monkeypatch)
    life.dead_candidate=False
    life.current_hp=213
    supervisor.observe()
    life.current_hp=190
    life.position=(18,10)
    assert not supervisor.observe().get('defending')


def test_memory_targets_use_group_ids_and_coordinates_without_images(monkeypatch):
    from conquest.memory_entities import MonsterObservation
    supervisor,control,_,_=setup(monkeypatch)
    control.update({'target_type_ids':[2]})
    monsters=[MonsterObservation(1000,25,'Turtledove',(12,12),(600,400),81,7,type_id=2),
              MonsterObservation(2000,26,'Pheasant',(12,12),(650,400),33,1,type_id=1),
              MonsterObservation(3000,27,'Turtledove',(12,12),(450,580),81,7,type_id=2)]
    supervisor.observer.entities=SimpleNamespace(layout=None,read=lambda:SimpleNamespace(monsters=monsters))
    selected=supervisor.memory_targets()
    assert [(t.entity_id,t.object_address,t.x,t.y) for t in selected]==[(25,1000,600,400)]


def test_health_table_race_is_recoverable(monkeypatch):
    supervisor,control,_,_=setup(monkeypatch)
    def changed(*args):
        raise ValueError('Health fields or pointer topology changed during sampling')
    monkeypatch.setattr(native_farm,'read_life',changed)
    with pytest.raises(CaptureUnavailable):
        supervisor.observe()
    assert control.snapshot()['enabled']


def test_pickup_requires_both_inventory_increase_and_ground_disappearance(monkeypatch):
    from conquest.memory_ground import GroundItem
    supervisor,_,_,notifications=setup(monkeypatch)
    now=[10.]
    monkeypatch.setattr(native_farm.time,'monotonic',lambda:now[0])
    drop=GroundItem(10,1000,1088001,(11,10))
    ground=[drop]
    supervisor.ground_items=lambda:tuple(ground)
    before=SimpleNamespace(silver=100,items=(),capacity=40)
    calls=[]
    assert supervisor.loot_step(before,(10,10),lambda *a,**kw:calls.append(kw))
    assert calls==[{'drop':drop}]
    ground.clear()
    assert supervisor.loot_step(before,(10,10),lambda *a,**kw:None)
    assert supervisor.pickups==0
    after=SimpleNamespace(silver=100,items=(SimpleNamespace(uid=99,type_id=1088001,amount=1),),capacity=40)
    assert not supervisor.loot_step(after,(10,10),lambda *a,**kw:None)
    assert supervisor.pickups==1


def test_world_range_allows_sixteen_tiles_without_pixel_distance_limit():
    from conquest.trial import TrialConfig,choose_target
    from conquest.vision import Target
    import yaml
    config=TrialConfig.model_validate(yaml.safe_load(open('profiles/desktop-foreground.example.yaml')))
    config=config.model_copy(update={'monster':'Turtledove','boundary':(500,500,700,700),'player_anchor':(518,396)})
    target=Target('Turtledove',900,650,1,10,1000,(612,616))
    assert choose_target([target],1,(600,600),config,0)==target
    too_far=Target('Turtledove',900,650,1,11,2000,(612,617))
    assert choose_target([too_far],1,(600,600),config,0) is None



def test_unreachable_patrol_waypoint_tries_another_saved_point(monkeypatch):
    supervisor,_,_,_=setup(monkeypatch)
    calls=[]
    def path(source,destination,limit):
        calls.append(destination)
        if destination==(20,20):
            raise ValueError('Route search exceeded its node budget')
        return [(10,10),(11,10),(12,10),(13,10),(14,10)]
    supervisor.recovery.terrain=SimpleNamespace(path=path)
    assert supervisor.patrol_step((10,10),(20,20),(0,0,30,30),
        alternatives=((20,20),(14,10)))==(14,10)
    assert calls==[(20,20),(14,10)]


def test_alternate_patrol_path_cannot_leave_saved_boundary(monkeypatch):
    supervisor,_,_,_=setup(monkeypatch)
    supervisor.recovery.terrain=SimpleNamespace(path=lambda *a,**k:[(10,10),(31,10),(14,10)])
    with pytest.raises(CaptureUnavailable,match='traversable'):
        supervisor.patrol_step((10,10),(20,20),(0,0,30,30),alternatives=((14,10),))


def test_failed_landing_replans_direction_then_returns_to_long_jumps(monkeypatch):
    import numpy as np
    from conquest.navigation import TerrainMap
    supervisor,_,_,notifications=setup(monkeypatch)
    now=[100.]
    monkeypatch.setattr(native_farm.time,'monotonic',lambda:now[0])
    supervisor.recovery.terrain=TerrainMap(1002,40,40,np.zeros((40,40),dtype=bool),'',(),())
    assert supervisor.patrol_step((20,20),(5,20),(0,0,39,39))==(8,20)
    supervisor.movement_failed((20,20),(8,20))
    detour=supervisor.patrol_step((20,20),(5,20),(0,0,39,39))
    assert detour[1]!=20 and max(abs(a-b) for a,b in zip(detour,(20,20)))<8
    assert notifications[-1][0]=='movement_recovery'
    now[0]+=31
    assert supervisor.patrol_step((20,20),(5,20),(0,0,39,39))==(8,20)
    assert not supervisor.movement_obstructions


def test_blocked_approach_endpoint_allows_local_detour_inside_boundary(monkeypatch):
    import numpy as np
    from conquest.navigation import TerrainMap
    supervisor,_,_,_=setup(monkeypatch)
    supervisor.recovery.terrain=TerrainMap(1002,40,40,np.zeros((40,40),dtype=bool),'',(),())
    supervisor.movement_failed((20,20),(8,20))
    detour=supervisor.patrol_step((20,20),(8,20),(0,0,20,20),chase=False)
    assert detour!=(8,20) and all(0<=v<=20 for v in detour)


def test_all_departures_blocked_waits_without_repeating_input(monkeypatch):
    import numpy as np
    from conquest.navigation import TerrainMap
    supervisor,_,_,_=setup(monkeypatch)
    supervisor.recovery.terrain=TerrainMap(1002,40,40,np.zeros((40,40),dtype=bool),'',(),())
    for point in ((19,20),(21,20),(20,19),(20,21)):
        supervisor.movement_failed((20,20),point)
    with pytest.raises(CaptureUnavailable,match='traversable'):
        supervisor.patrol_step((20,20),(5,20),(0,0,39,39))


def test_chase_uses_health_checked_monsters_including_offscreen_not_corpse_records(monkeypatch):
    from conquest.memory_entities import MonsterObservation
    from conquest import monster_health
    supervisor,control,_,_=setup(monkeypatch)
    control.update({'target_type_ids':[2]})
    dead=MonsterObservation(1000,25,'Turtledove',(11,10),(600,400),81,7,type_id=2)
    living=MonsterObservation(2000,26,'Turtledove',(14,10),(1100,400),81,7,type_id=2)
    unknown=MonsterObservation(3000,27,'Turtledove',(12,10),(620,400),81,7,type_id=2)
    def hp(session,layout,monster):
        if monster is unknown:raise ValueError('Health observation changed')
        return 0 if monster is dead else 81
    monkeypatch.setattr(monster_health,'read_monster_health',hp)
    supervisor.observer.entities=SimpleNamespace(layout=None,read=lambda:SimpleNamespace(monsters=(dead,living,unknown)))
    assert supervisor.memory_targets()==[]  # Living target is not yet clickable.
    assert supervisor.chase_monsters==(replace(living,current_hp=81),)
    destinations=[]
    def path(source,destination,**kwargs):
        destinations.append(destination)
        return [(x,10) for x in range(10,15)]
    supervisor.recovery.terrain=SimpleNamespace(path=path)
    assert supervisor.patrol_step((10,10),(20,20),(0,0,30,30))==(14,10)
    assert destinations==[(14,10)]


def test_changed_ground_record_defers_pickup_without_starving_combat(monkeypatch):
    from conquest.memory_ground import GroundItem
    supervisor,_,_,_=setup(monkeypatch)
    now=[10.];monkeypatch.setattr(native_farm.time,'monotonic',lambda:now[0])
    drop=GroundItem(10,1000,1088001,(11,10))
    supervisor.ground_items=lambda:(drop,)
    inventory=SimpleNamespace(silver=100,items=(),capacity=40)
    calls=[]
    def changed(*args,**kwargs):
        calls.append(True)
        raise CaptureUnavailable('Ground item changed before pickup')
    assert not supervisor.loot_step(inventory,(10,10),changed)
    assert not supervisor.loot_step(inventory,(10,10),changed)
    assert len(calls)==1 and supervisor.pending_loot is None
    now[0]+=1.1
    assert supervisor.loot_step(inventory,(10,10),lambda *a,**k:None)
    assert supervisor.pending_loot is not None


def test_long_approach_budget_and_cache_follow_fresh_position(monkeypatch):
    supervisor,_,_,_=setup(monkeypatch)
    calls=[]
    def path(source,destination,limit,**kwargs):
        calls.append((source,limit,kwargs))
        assert limit==250000
        return [(x,10) for x in range(source[0],41)]
    supervisor.recovery.terrain=SimpleNamespace(path=path)
    assert supervisor.patrol_step((10,10),(40,10),(0,0,50,50),chase=False)==(22,10)
    assert supervisor.patrol_step((22,10),(40,10),(0,0,50,50),chase=False)==(34,10)
    assert len(calls)==1
    supervisor.movement_failed((22,10),(34,10))
    supervisor.patrol_step((22,10),(40,10),(0,0,50,50),chase=False)
    assert len(calls)==2 and calls[-1][2]['avoid']


def test_return_uses_fewer_turns_planner_and_keeps_checked_cache(monkeypatch):
    supervisor,_,_,_=setup(monkeypatch);calls=[]
    def straight(source,destination,**kwargs):
        calls.append(kwargs);return [(x,10) for x in range(source[0],41)]
    supervisor.recovery.terrain=SimpleNamespace(straight_path=straight,
        path=lambda *a,**kw:pytest.fail('Return must use fewer-turn path'))
    assert supervisor.patrol_step((10,10),(40,10),(0,0,50,50),chase=False)==(22,10)
    assert supervisor.patrol_step((22,10),(40,10),(0,0,50,50),chase=False)==(34,10)
    assert len(calls)==1


def test_successful_detour_restores_jumps_without_forgetting_failed_tiles(monkeypatch):
    supervisor,_,_,_=setup(monkeypatch)
    supervisor.movement_failed((10,10),(22,10));avoided=dict(supervisor.movement_obstructions)
    supervisor.movement_succeeded((10,10),(10,11),arrived=False)
    assert supervisor.movement_run_until>0
    supervisor.movement_succeeded((10,10),(10,14),arrived=False)
    assert supervisor.movement_run_until==0 and supervisor.movement_obstructions==avoided


def test_ranged_escape_requires_fresh_living_threats_clear_path_and_boundary(monkeypatch):
    import numpy as np
    from conquest.navigation import TerrainMap
    supervisor,_,_,_=setup(monkeypatch)
    monkeypatch.setattr(native_farm.time,'monotonic',lambda:100.)
    supervisor.scene_timestamp=100.
    supervisor.recovery.terrain=TerrainMap(1002,50,50,np.zeros((50,50),dtype=bool),'',(),())
    supervisor.escape_monsters=(SimpleNamespace(position=(21,20)),SimpleNamespace(position=(20,21)))
    point=supervisor.ranged_escape((20,20),(5,5,40,40))
    assert point is not None and max(abs(a-b) for a,b in zip(point,(20,20)))>=8
    assert min(max(abs(a-b) for a,b in zip(point,m.position)) for m in supervisor.escape_monsters)>=6
    assert supervisor.ranged_escape((20,20),(19,19,23,23)) is None
    supervisor.recovery.terrain.blocked[:]=True
    assert supervisor.ranged_escape((20,20),(5,5,40,40)) is None
    supervisor.scene_timestamp=98.
    assert supervisor.ranged_escape((20,20),(5,5,40,40)) is None


def test_chase_retains_last_seen_location_briefly_without_claiming_a_live_target(monkeypatch):
    from conquest.memory_entities import MonsterObservation
    supervisor,control,_,_=setup(monkeypatch)
    control.update({'target_type_ids':[2]})
    now=[100.];monkeypatch.setattr(native_farm.time,'monotonic',lambda:now[0])
    supervisor.scene_timestamp=100.
    supervisor.chase_monsters=(MonsterObservation(1000,25,'Turtledove',(20,10),(1100,400),81,7,type_id=2),)
    destinations=[]
    def path(source,destination,**kwargs):
        destinations.append(destination)
        step=1 if destination[0]>source[0] else -1
        return [(x,10) for x in range(source[0],destination[0]+step,step)]
    supervisor.recovery.terrain=SimpleNamespace(path=path)
    assert supervisor.patrol_step((10,10),(5,10),(0,0,40,40))==(20,10)
    supervisor.chase_monsters=();now[0]=100.5
    assert supervisor.patrol_step((12,10),(5,10),(0,0,40,40))==(20,10)
    assert supervisor.chase_monsters==()  # No target is fabricated for attack.
    now[0]=102.1
    supervisor.patrol_step((12,10),(5,10),(0,0,40,40))
    assert destinations[-1]==(5,10)


def test_escape_avoids_landing_inside_another_living_group(monkeypatch):
    import numpy as np
    from conquest.navigation import TerrainMap
    supervisor,_,_,_=setup(monkeypatch)
    monkeypatch.setattr(native_farm.time,'monotonic',lambda:100.)
    supervisor.scene_timestamp=100.
    supervisor.recovery.terrain=TerrainMap(1002,50,50,np.zeros((50,50),dtype=bool),'',(),())
    supervisor.escape_monsters=tuple(SimpleNamespace(position=p) for p in
        ((21,20),(20,21),(8,20),(8,21),(9,20)))
    point=supervisor.ranged_escape((20,20),(1,1,45,45))
    assert point is not None
    assert sum(max(abs(a-b) for a,b in zip(point,m.position))<=4 for m in supervisor.escape_monsters)<2
    supervisor.escape_ready_at=101.
    assert supervisor.ranged_escape((20,20),(1,1,45,45)) is None


def test_escape_rechecks_source_position_before_input(monkeypatch):
    supervisor,_,life,_=setup(monkeypatch)
    life.dead_candidate=False;life.ghost_candidate=False
    with pytest.raises(CaptureUnavailable,match='Player moved'):
        supervisor.dispatch(lambda:pytest.fail('Moving source must be reobserved'),expected_position=(9,10))
    calls=[]
    supervisor.dispatch(lambda:calls.append(True),expected_position=life.position)
    assert calls==[True]


def test_looter_skips_nearby_junk_and_selects_allowed_enhanced_item(monkeypatch):
    from conquest.memory_ground import GroundItem
    supervisor,_,_,_=setup(monkeypatch)
    junk=GroundItem(10,1000,480003,(10,10),plus=0)
    wanted=GroundItem(11,2000,480003,(12,10),plus=1)
    supervisor.ground_items=lambda:(junk,wanted)
    calls=[]
    assert supervisor.loot_step(SimpleNamespace(items=(),capacity=40),(10,10),
                                lambda point,**kw:calls.append(kw['drop']))
    assert calls==[wanted]


def test_scatter_group_at_four_to_twelve_tiles_does_not_trigger_retreat(monkeypatch):
    supervisor,_,_,_=setup(monkeypatch)
    now=[100.];monkeypatch.setattr(native_farm.time,'monotonic',lambda:now[0])
    supervisor.scene_timestamp=100.
    supervisor.escape_monsters=tuple(SimpleNamespace(position=p) for p in ((24,20),(20,25),(28,20),(32,20)))
    supervisor.recovery.terrain=SimpleNamespace(walkable=lambda p:True)
    assert supervisor.ranged_escape((20,20),(0,0,50,50)) is None
    supervisor.last_damage_at=100.
    assert supervisor.ranged_escape((20,20),(0,0,50,50)) is not None
    assert supervisor.escape_context['recent_damage']
    supervisor.escape_damage_consumed_at=100.
    assert supervisor.ranged_escape((20,20),(0,0,50,50)) is None
    supervisor.escape_damage_consumed_at=99.
    now[0]=102.;supervisor.scene_timestamp=102.
    assert supervisor.ranged_escape((20,20),(0,0,50,50)) is None


def test_one_adjacent_enemy_without_damage_does_not_trigger_retreat(monkeypatch):
    supervisor,_,_,_=setup(monkeypatch)
    monkeypatch.setattr(native_farm.time,'monotonic',lambda:100.)
    supervisor.scene_timestamp=100.
    supervisor.escape_monsters=(SimpleNamespace(position=(21,20)),SimpleNamespace(position=(22,20)))
    assert supervisor.ranged_escape((20,20),(0,0,50,50)) is None


def test_scatter_counter_increase_does_not_exclude_a_surviving_aim_target(monkeypatch):
    from conquest.vision import Target
    supervisor,_,_,_=setup(monkeypatch)
    target=Target('Pheasant',600,400,1,25,1000,(12,10))
    supervisor.last_target=target
    supervisor.finish_target('kill_counter_increased')
    assert (25,1000) not in supervisor.excluded_targets
    supervisor.last_target=target
    supervisor.finish_target('no_attack_progress')
    assert (25,1000) in supervisor.excluded_targets


def test_changing_target_memory_is_not_a_cleared_group(monkeypatch):
    from conquest import monster_health
    from conquest.memory_entities import MonsterObservation
    supervisor,_,_,_=setup(monkeypatch)
    monster=MonsterObservation(1000,25,'Pheasant',(12,12),(600,400),81,7,type_id=1)
    supervisor.observer.entities=SimpleNamespace(layout=None,read=lambda:SimpleNamespace(monsters=[monster]))
    assert supervisor.memory_targets() and supervisor.targets_observation_available
    def changed(*a):raise ValueError('Monster changed during observation')
    monkeypatch.setattr(monster_health,'read_monster_health',changed)
    assert supervisor.memory_targets()==[] and not supervisor.targets_observation_available
    supervisor.observer.entities.read=lambda:SimpleNamespace(monsters=[])
    assert supervisor.memory_targets()==[] and supervisor.targets_observation_available


def test_scatter_approach_stops_before_jumping_onto_a_group(monkeypatch):
    from conquest.memory_entities import MonsterObservation
    supervisor,control,_,_=setup(monkeypatch)
    monkeypatch.setattr(native_farm.time,'monotonic',lambda:100.)
    supervisor.scene_timestamp=100.;supervisor.scatter_standoff=6
    supervisor.chase_monsters=(MonsterObservation(1000,25,'Pheasant',(22,10),(900,400),81,7,type_id=1),)
    supervisor.recovery.terrain=SimpleNamespace(path=lambda source,destination,**k:[(x,10) for x in range(10,23)])
    assert supervisor.patrol_step((10,10),(30,30),(0,0,40,40))==(16,10)


def test_currency_is_ignored_during_combat_and_idle(monkeypatch):
    from conquest.memory_ground import GroundItem
    supervisor,_,_,_=setup(monkeypatch)
    now=[10.]
    monkeypatch.setattr(native_farm.time,'monotonic',lambda:now[0])
    supervisor.ground_items=lambda:tuple(GroundItem(i+1,1000+i*100,kind,(11+i,10))
        for i,kind in enumerate((1090000,1090010,1090020,1091000,1091010,1091020)))
    bag=SimpleNamespace(silver=100,items=(),capacity=40)
    clicks=[]
    click=lambda point,**kw:clicks.append(kw['drop'])
    assert not supervisor.combat_loot_step(bag,(10,10),click)
    now[0]=18
    assert not supervisor.combat_loot_step(bag,(10,10),click)
    assert not supervisor.loot_step(bag,(10,10),click)
    assert clicks==[] and supervisor.pending_loot is None


def test_both_bandit_groups_selected_but_king_is_only_observed_for_escape(monkeypatch):
    from conquest.memory_entities import MonsterObservation
    supervisor,control,_,_=setup(monkeypatch)
    control.update({'target_type_ids':[7,66]})
    supervisor.position=(10,10);supervisor.defending=True
    monsters=[MonsterObservation(1000,1,'Bandit',(12,12),(600,400),817,32,type_id=7),
              MonsterObservation(2000,2,'BanditL33',(12,11),(650,400),2451,33,type_id=66),
              MonsterObservation(3000,3,'BanditKing',(11,10),(550,400),45000,32,type_id=8302)]
    supervisor.observer.entities=SimpleNamespace(layout=None,read=lambda:SimpleNamespace(monsters=monsters))
    targets=supervisor.memory_targets()
    assert [t.name for t in targets]==['Bandit','BanditL33']
    assert 'BanditKing' in [m.name for m in supervisor.escape_monsters]
    assert 'BanditKing' not in [m.name for m in supervisor.chase_monsters]


def test_target_name_filter_accepts_only_explicit_route_variants():
    from conquest.trial import choose_target
    from conquest.vision import Target
    config=SimpleNamespace(minimum_health=.4,boundary=(0,0,100,100),player_anchor=(518,396),
        max_distance_pixels=380,attack_range_tiles=12,monster='Bandit',monster_variants=('BanditL33',))
    for name,wanted in [('Bandit',True),('BanditL33',True),('BanditKing',False),('BanditL97',False),('BanditL34',False)]:
        target=Target(name,550,412,1,1,1000,(11,10),100)
        assert (choose_target([target],1,(10,10),config,0) is target)==wanted


def test_system_ownership_rejection_skips_same_drop_and_continues_other_loot(monkeypatch):
    from conquest.memory_ground import GroundItem
    from conquest.loot_ownership import SystemMessage,OWNERSHIP_MESSAGE
    supervisor,_,_,notes=setup(monkeypatch)
    now=[10.];messages=[];denied=set()
    monkeypatch.setattr(native_farm.time,'monotonic',lambda:now[0])
    drop=GroundItem(1,1000,1088001,(11,10),spawn_tick=1)
    other=GroundItem(2,2000,1088001,(12,10),spawn_tick=2)
    ground=[drop]
    supervisor.ground_items=lambda:tuple(ground)
    guard=SimpleNamespace(snapshot=lambda:tuple(messages),blocked=lambda d,m:d in denied,reject=lambda d,m:denied.add(d))
    supervisor.ownership_guard=lambda:guard
    bag=SimpleNamespace(silver=100,items=(),capacity=40);clicks=[]
    click=lambda point,**kw:clicks.append(kw['drop'])
    assert supervisor.loot_step(bag,(10,10),click)
    messages.append(SystemMessage(0x9000,10001,OWNERSHIP_MESSAGE));now[0]+=.1
    assert not supervisor.loot_step(bag,(10,10),click)
    assert drop in denied and supervisor.pending_loot is None and supervisor.pickups==0
    now[0]+=100
    assert not supervisor.loot_step(bag,(10,10),click)
    assert clicks==[drop]
    ground.append(other)
    assert supervisor.loot_step(bag,(10,10),click)
    assert clicks==[drop,other]
    assert any(event=='memory_pickup_owned' for event,_ in notes)


def test_chase_does_not_stop_at_world_range_while_aim_is_obscured(monkeypatch):
    from conquest.memory_entities import MonsterObservation
    supervisor,_,_,_=setup(monkeypatch)
    monkeypatch.setattr(native_farm.time,'monotonic',lambda:100.)
    supervisor.scene_timestamp=100.;supervisor.scatter_standoff=10
    supervisor.chase_monsters=(MonsterObservation(1000,25,'Pheasant',(16,16),(518,588),81,7,type_id=1),)
    path=[(x,10) for x in range(10,17)]+[(16,y) for y in range(11,17)]
    supervisor.recovery.terrain=SimpleNamespace(path=lambda *a,**k:path)
    # First two tiles leave the target behind the lower-left game UI.
    assert supervisor.patrol_step((10,10),(30,30),(0,0,40,40))==(13,10)


def test_unselected_distant_attacker_is_available_for_escape_not_attack(monkeypatch):
    from conquest.memory_entities import MonsterObservation
    supervisor,_,_,_=setup(monkeypatch)
    supervisor.position=(10,10);supervisor.defending=True
    threat=MonsterObservation(1000,25,'HeavyGhostL23',(18,10),(774,524),1096,23,type_id=64)
    supervisor.observer.entities=SimpleNamespace(layout=None,read=lambda:SimpleNamespace(monsters=[threat]))
    assert supervisor.memory_targets()==[]
    assert list(supervisor.escape_monsters)==[replace(threat,current_hp=81)]
    assert not supervisor.chase_monsters

def test_unreachable_sweep_point_advances_to_forward_alternative(monkeypatch):
    from conquest.patrol_search import patrol_step
    supervisor,_,_,_=setup(monkeypatch)
    calls=[]
    route=((5,10),(20,20),(14,10),(18,10))
    def path(source,destination,limit):
        calls.append(destination)
        if destination==(20,20):raise ValueError('Unreachable within boundary')
        direction=1 if destination[0]>source[0] else -1
        return [(x,10) for x in range(source[0],destination[0]+direction,direction)]
    supervisor.recovery.terrain=SimpleNamespace(path=path)
    step,index=patrol_step(supervisor,(10,10),route,1,(0,0,30,30))
    assert step==(14,10) and index==2
    assert calls==[(20,20),(14,10)]
    # Arrival now advances through the sweep instead of returning to its start.
    step,index=patrol_step(supervisor,(14,10),route,(index+1)%len(route),(0,0,30,30))
    assert step==(18,10) and index==3


def test_valuable_loot_is_attempted_during_combat_without_waiting_for_clear_scene(monkeypatch):
    from conquest.memory_ground import GroundItem
    supervisor,_,_,_=setup(monkeypatch)
    meteor=GroundItem(1,1000,1088001,(15,10))
    supervisor.ground_items=lambda:(meteor,)
    clicks=[]
    assert supervisor.combat_loot_step(SimpleNamespace(silver=0,items=(),capacity=40),(10,10),
        lambda point,**kw:clicks.append(kw['drop']))
    assert clicks==[meteor]


def test_meteor_appearing_between_scatters_bypasses_old_poll_delay(monkeypatch):
    from conquest.memory_ground import GroundItem
    supervisor,_,_,_=setup(monkeypatch)
    ground=[];supervisor.ground_items=lambda:tuple(ground)
    bag=SimpleNamespace(silver=0,items=(),capacity=40);clicks=[]
    click=lambda point,**kw:clicks.append(kw)
    assert not supervisor.combat_loot_step(bag,(10,10),click)
    ground.append(GroundItem(1,1000,1088001,(11,10)))
    assert supervisor.combat_loot_step(bag,(10,10),click)
    assert clicks[0]['drop'].type_id==1088001


def test_meteor_cooldown_and_disappearance_are_explained_in_audit(monkeypatch):
    from conquest.memory_ground import GroundItem
    supervisor,_,_,notes=setup(monkeypatch)
    meteor=GroundItem(1,1000,1088001,(11,10))
    supervisor.ground_items=lambda:(meteor,)
    supervisor.loot_cooldowns[(1,1000)]=native_farm.time.monotonic()+60
    bag=SimpleNamespace(silver=0,items=(),capacity=40)
    supervisor.loot_step(bag,(10,10),lambda *a,**kw:pytest.fail('Cooldown must not click'))
    observation=next(fields for event,fields in notes if event=='memory_loot_observed')
    assert observation['valuable_drops'][0]['reason']=='pickup_cooldown'
    assert observation['player_position']==(10,10)
    supervisor.ground_items=lambda:()
    supervisor.loot_step(bag,(10,10),lambda *a,**kw:pytest.fail('Absent drop must not click'))
    assert notes[-1][1]['valuable_drops']==[]


def test_unavailable_approach_logs_once_without_movement(monkeypatch):
    from conquest.memory_ground import GroundItem
    supervisor,_,_,notes=setup(monkeypatch)
    meteor=GroundItem(1,1000,1088001,(30,30))
    for _ in range(2):
        assert supervisor.approach_loot(meteor,(10,10),lambda *a,**kw:pytest.fail('No terrain')) is False
    assert len(notes)==1
    assert notes[0][1]['detail']=='Loot terrain unavailable'


def test_obscured_meteor_is_approached_on_checked_terrain(monkeypatch):
    import numpy as np
    from conquest.navigation import TerrainMap
    from conquest.memory_ground import GroundItem
    supervisor,_,life,notes=setup(monkeypatch)
    terrain=TerrainMap(1002,100,100,np.zeros((100,100),dtype=bool),'',(),())
    supervisor.recovery.terrain=terrain;life.position=(50,50)
    supervisor.read_life=lambda:life
    meteor=GroundItem(1,1000,1088001,(50,63));supervisor.ground_items=lambda:(meteor,)
    clicks=[]
    assert supervisor.loot_step(SimpleNamespace(silver=0,items=(),capacity=40),(50,50),
                               lambda point,**kw:clicks.append((point,kw)))
    assert clicks[0][1]['control'] is True
    assert any(event=='memory_pickup_approach' for event,_ in notes)
    assert supervisor.pending_loot is None


def test_recycled_valuable_does_not_authorize_approach(monkeypatch):
    import numpy as np
    from conquest.navigation import TerrainMap
    from conquest.memory_ground import GroundItem
    supervisor,_,life,_=setup(monkeypatch)
    supervisor.recovery.terrain=TerrainMap(1002,100,100,np.zeros((100,100),dtype=bool),'',(),())
    life.position=(50,50);supervisor.read_life=lambda:life
    supervisor.ground_items=lambda:()
    assert not supervisor.approach_loot(GroundItem(1,1000,1088001,(50,63)),(50,50),
        lambda *a,**kw:pytest.fail('Stale valuable must not move the player'))


def test_inventory_gain_records_plus_item_without_ground_click_or_ground_reader(monkeypatch):
    supervisor,_,_,events=setup(monkeypatch)
    existing=SimpleNamespace(uid=1,type_id=1088001,amount=1,plus=0)
    new=SimpleNamespace(uid=2,type_id=530013,amount=1599,plus=2)
    before=SimpleNamespace(items=(existing,))
    supervisor.observe_inventory(before)
    supervisor.observe_inventory(SimpleNamespace(items=(existing,new)))
    supervisor.observe_inventory(SimpleNamespace(items=(existing,new)))
    gains=[v for e,v in events if e=='memory_pickup_verified']
    assert len(gains)==1 and gains[0]['increase']==1
    assert gains[0]['inventory_uid']==2 and gains[0]['plus']==2
    assert gains[0]['source']=='inventory_gain'
    assert supervisor.pickups==1


def test_inventory_tracker_ignores_currency_and_old_items_and_does_not_repeat_returned_uid(monkeypatch):
    supervisor,_,_,events=setup(monkeypatch)
    meteor=SimpleNamespace(uid=1,type_id=1088001,amount=1,plus=0)
    money=SimpleNamespace(uid=2,type_id=1090020,amount=10,plus=0)
    ordinary=SimpleNamespace(uid=3,type_id=530013,amount=100,plus=0)
    supervisor.observe_inventory(SimpleNamespace(items=(meteor,)))
    supervisor.observe_inventory(SimpleNamespace(items=(money,ordinary)))
    supervisor.observe_inventory(SimpleNamespace(items=(meteor,money,ordinary)))
    assert not events and supervisor.pickups==0


def test_pending_ground_pickup_and_inventory_gain_emit_one_event(monkeypatch):
    from conquest.memory_ground import GroundItem
    supervisor,_,_,events=setup(monkeypatch)
    drop=GroundItem(10,1000,1088001,(11,10),plus=0)
    before=SimpleNamespace(silver=100,items=(),capacity=40)
    after=SimpleNamespace(silver=100,items=(SimpleNamespace(uid=99,type_id=1088001,amount=1,plus=0),),capacity=40)
    supervisor.observe_inventory(before)
    supervisor.ground_items=lambda:(drop,)
    supervisor.loot_step(before,(10,10),lambda *a,**k:None)
    supervisor.observe_inventory(after)
    supervisor.ground_items=lambda:()
    supervisor.loot_step(after,(10,10),lambda *a,**k:None)
    gains=[v for e,v in events if e=='memory_pickup_verified']
    assert len(gains)==1 and gains[0]['inventory_uid']==99
    assert supervisor.pickups==1 and supervisor.pending_loot is None


@pytest.mark.parametrize('error,retryable', [('Inventory opening unverified',True),
    ('Equip unverified; no repeat equip issued',False)])
def test_arrow_panel_open_failure_retries_but_uncertain_equip_does_not(monkeypatch,error,retryable):
    monkeypatch.setattr(native_farm,'logical_coordinates',nullcontext)
    supervisor=native_farm.NativeFarmSupervisor.__new__(native_farm.NativeFarmSupervisor)
    calls=[]
    def trade(body):
        calls.append(body)
        if body['action']=='equip-arrows':raise ValueError(error)
    supervisor.observer=SimpleNamespace(town_trade=trade)
    supervisor.dispatch=lambda callback:callback()
    inventory=SimpleNamespace(items=[SimpleNamespace(type_id=1050001,amount=1000,uid=123)])
    with pytest.raises(CaptureUnavailable if retryable else ValueError):
        supervisor.reload_arrows(inventory,1050001)
    assert calls==[{'action':'equip-arrows','uid':123},{'action':'close','window':'Inventory'}]
