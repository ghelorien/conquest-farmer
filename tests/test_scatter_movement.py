from types import SimpleNamespace
import numpy as np
from conquest.navigation import TerrainMap
from conquest.scatter_movement import scatter_landing,clear_jump
import pytest


def target(x,y,hp=100):
    return SimpleNamespace(world_position=(x,y),current_hp=hp)


def test_landing_prefers_dense_group_and_only_long_clear_segments():
    terrain=TerrainMap(1011,100,100,np.zeros((100,100),dtype=bool),'',(),())
    supervisor=SimpleNamespace(recovery=SimpleNamespace(terrain=terrain))
    targets=[target(61,50),target(62,50),target(63,51),target(38,50)]
    landing=scatter_landing(supervisor,targets,(50,50),(20,20,80,80),10)
    assert landing[0]>=58
    assert sum(max(abs(p.world_position[0]-landing[0]),abs(p.world_position[1]-landing[1]))<=10 for p in targets)>=3
    assert 8<=landing[0]-50<=12
    terrain.blocked[50,53]=True
    landing=scatter_landing(supervisor,targets,(50,50),(20,20,80,80),10)
    assert landing is None or clear_jump(terrain,(50,50),landing)


def test_no_short_jump_or_dead_target_chasing():
    terrain=TerrainMap(1011,100,100,np.zeros((100,100),dtype=bool),'',(),())
    supervisor=SimpleNamespace(recovery=SimpleNamespace(terrain=terrain))
    assert scatter_landing(supervisor,[target(51,50)],(50,50),(45,45,55,55),10) is None
    assert scatter_landing(supervisor,[target(60,50,0)],(50,50),(20,20,80,80),10) is None


def test_outside_hunt_groups_cannot_trap_jumps_along_the_boundary():
    terrain=TerrainMap(1011,100,100,np.zeros((100,100),dtype=bool),'',(),())
    outside=[target(71,50+i) for i in range(8)]
    supervisor=SimpleNamespace(recovery=SimpleNamespace(terrain=terrain),scatter_scene_targets=outside)
    assert scatter_landing(supervisor,outside,(70,50),(20,20,70,80),10) is None
    supervisor.scatter_scene_targets=outside+[target(55,50)]
    landing=scatter_landing(supervisor,[],(70,50),(20,20,70,80),5)
    assert landing is not None and max(abs(landing[0]-55),abs(landing[1]-50))<=5


def test_fewer_turns_still_uses_shortest_walkable_path():
    blocked=np.zeros((30,30),dtype=bool);blocked[:20,14]=True
    terrain=TerrainMap(1011,30,30,blocked,'',(),())
    before=terrain.path((3,3),(25,25));after=terrain.straight_path((3,3),(25,25))
    turns=lambda p:sum((b[0]-a[0],b[1]-a[1])!=(c[0]-b[0],c[1]-b[1]) for a,b,c in zip(p,p[1:],p[2:]))
    assert len(before)==len(after) and turns(after)<=turns(before)
    assert all(terrain.walkable(p) for p in after)
    assert all(abs(a[0]-b[0])+abs(a[1]-b[1])==1 for a,b in zip(after,after[1:]))
    assert (3,4) not in terrain.straight_path((3,3),(25,25),avoid=[(3,4)])


def test_full_memory_scene_steers_toward_larger_group_behind_current_hud():
    terrain=TerrainMap(1011,100,100,np.zeros((100,100),dtype=bool),'',(),())
    scene=[target(60,57),target(61,57),target(61,58),target(63,58),target(60,59)]
    supervisor=SimpleNamespace(recovery=SimpleNamespace(terrain=terrain),scatter_scene_targets=scene)
    landing=scatter_landing(supervisor,[target(42,50)],(50,50),(20,20,80,80),5,minimum_count=3)
    assert landing is not None and landing[0]>50 and landing[1]>50
    assert 8<=max(abs(a-b) for a,b in zip(landing,(50,50)))<=12
    assert clear_jump(terrain,(50,50),landing)


def test_diagonal_jump_cannot_cut_blocked_corner():
    terrain=TerrainMap(1011,30,30,np.zeros((30,30),dtype=bool),'',(),())
    assert clear_jump(terrain,(10,10),(18,14))
    terrain.blocked[10,11]=True
    assert not clear_jump(terrain,(10,10),(18,14))


@pytest.mark.parametrize('anchor',[(510,313),(510,250),(400,500)])
def test_flying_camera_anchor_never_selects_a_jump_the_input_guard_rejects(anchor):
    terrain=TerrainMap(1011,100,100,np.zeros((100,100),dtype=bool),'',(),())
    supervisor=SimpleNamespace(recovery=SimpleNamespace(terrain=terrain))
    targets=[target(40,40),target(41,39)]
    landing=scatter_landing(supervisor,targets,(50,50),(20,20,80,80),10,anchor=anchor)
    if landing:
        dx,dy=landing[0]-50,landing[1]-50
        px,py=anchor[0]+(dx-dy)*32,anchor[1]+(dx+dy)*16
        assert 80<px<956 and 140<py<667
        assert not(px<615 and (py>550 or py<170))
    if anchor==(510,313):assert landing is not None


def test_recently_failed_landing_is_not_reselected(monkeypatch):
    from conquest import scatter_movement as sm
    monkeypatch.setattr(sm.time,'monotonic',lambda:100.)
    terrain=TerrainMap(1011,100,100,np.zeros((100,100),dtype=bool),'',(),())
    supervisor=SimpleNamespace(recovery=SimpleNamespace(terrain=terrain))
    targets=[target(61,50),target(62,50),target(63,51)]
    first=scatter_landing(supervisor,targets,(50,50),(20,20,80,80),10)
    supervisor.movement_obstructions={(1011,first):130.}
    assert scatter_landing(supervisor,targets,(50,50),(20,20,80,80),10)!=first


def test_dense_pack_landing_avoids_actor_tiles_and_click_boxes():
    terrain=TerrainMap(1011,100,100,np.zeros((100,100),dtype=bool),'',(),())
    supervisor=SimpleNamespace(recovery=SimpleNamespace(terrain=terrain))
    targets=[target(60,50),target(61,50),target(60,51)]
    for t in targets:
        dx,dy=t.world_position[0]-50,t.world_position[1]-50
        t.x=518+(dx-dy)*32;t.y=396+(dx+dy)*16
    landing=scatter_landing(supervisor,targets,(50,50),(20,20,80,80),10)
    assert landing is not None and landing not in [t.world_position for t in targets]
    dx,dy=landing[0]-50,landing[1]-50;px,py=518+(dx-dy)*32,396+(dx+dy)*16
    assert not any(abs(px-t.x)<=24 and -40<=py-t.y<=10 for t in targets)


def test_finish_wounded_group_requires_fresh_same_identities_in_range(monkeypatch):
    from conquest import scatter_movement as sm
    now=[100.];monkeypatch.setattr(sm.time,'monotonic',lambda:now[0])
    supervisor=SimpleNamespace()
    targets=[SimpleNamespace(entity_id=i,object_address=1000+i,world_position=(52+i,50),current_hp=800) for i in (1,2)]
    sm.remember_scatter(supervisor,targets)
    assert not sm.wounded_group_in_range(supervisor,targets,(50,50),10)
    for t in targets:t.current_hp=180
    assert sm.wounded_group_in_range(supervisor,targets,(50,50),10)
    targets[0].object_address=9999
    assert not sm.wounded_group_in_range(supervisor,targets,(50,50),10)
    targets[0].object_address=1001
    assert not sm.wounded_group_in_range(supervisor,targets,(30,30),10)
    now[0]=104
    assert not sm.wounded_group_in_range(supervisor,targets,(50,50),10)
