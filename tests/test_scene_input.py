import pytest

from conquest.scene_input import BackgroundSceneStepper, SceneReading


class Scene:
    def __init__(self):
        self.position = (452,449)
        self.window = {'pid':1,'hwnd':10,'root_hwnd':20,'foreground':30,
                       'client_size':[1036,793], 'cursor':[900,500], 'minimized':False}
        self.calls=[]
        self.destination=(453,449)

    def observe(self):
        return SceneReading(self.position,dict(self.window),0)

    def click(self,point,viewport):
        self.calls.append((point,viewport))
        self.position=self.destination
        return {'messages_queued':True}

    def stepper(self, **kwargs):
        return BackgroundSceneStepper(self.observe,self.click,clock=lambda:0,
                                      sleep=kwargs.pop('sleep',lambda _:None),**kwargs)


def test_reached_requires_position_evidence_after_single_click():
    scene=Scene()
    result=scene.stepper().step(scene.position,scene.destination)
    assert result['reached'] and len(result['samples'])==3
    assert scene.calls==[((550,412),(1036,793))]


def test_queue_acceptance_does_not_establish_movement():
    scene=Scene()
    scene.destination=scene.position
    result=scene.stepper().step(scene.position,(453,449))
    assert not result['reached'] and result['outcome']=='destination_not_observed'
    assert len(scene.calls)==1


@pytest.mark.parametrize('change', ['focus','position','viewport'])
def test_change_before_click_defers_without_input(change):
    scene=Scene()
    def sleep(_):
        if change=='focus': scene.window['foreground']=20
        if change=='position': scene.position=(451,449)
        if change=='viewport': scene.window['client_size']=[800,600]
    with pytest.raises(ValueError):
        scene.stepper(sleep=sleep).step((452,449),(453,449))
    assert not scene.calls


def test_unexpected_effect_holds_without_retry():
    scene=Scene()
    scene.destination=(455,451)
    result=scene.stepper().step(scene.position,(453,449))
    assert not result['reached'] and 'unexpected tile' in result['error']
    assert len(scene.calls)==1


def test_focus_change_after_delivery_is_uncertain():
    scene=Scene()
    click=scene.click
    def changed(*args):
        result=click(*args)
        scene.window['foreground']=20
        return result
    stepper=scene.stepper()
    stepper.click=changed
    result=stepper.step(scene.position,scene.destination)
    assert not result['reached'] and result['outcome']=='uncertain'
    assert len(scene.calls)==1
    assert result['samples']==[[453,449]]
    assert result['observations'][0]['window']['foreground']==20


def test_post_click_cursor_movement_keeps_verifying_destination():
    scene=Scene()
    click=scene.click
    def changed(*args):
        result=click(*args)
        scene.window['cursor']=[123,456]
        return result
    stepper=scene.stepper()
    stepper.click=changed
    result=stepper.step(scene.position,scene.destination)
    assert result['reached'] and result['cursor_changed']
    assert result['observations'][0]['window_changes']['cursor']['after']==[123,456]
    assert len(scene.calls)==1


def test_adjacent_bound_prevents_long_uncalibrated_walk():
    scene=Scene()
    with pytest.raises(ValueError,match='bounded waypoint'):
        scene.stepper().step(scene.position,(460,449))
    assert not scene.calls


def test_map_change_blocks_the_route_before_any_click():
    scene=Scene()
    with pytest.raises(ValueError,match='map'):
        scene.stepper(expected_map=1002).step(scene.position,scene.destination)
    assert not scene.calls


def test_waypoint_must_stay_inside_explicit_bounds():
    scene=Scene()
    with pytest.raises(ValueError,match='bounds'):
        scene.stepper(allowed_bounds=(450,447,452,451)).step(scene.position,scene.destination)
    assert not scene.calls


def test_waypoint_allows_intermediate_positions_and_checks_arrival():
    scene=Scene()
    positions=iter([(453,449),(454,449),(455,449),(455,449),(455,449)])
    base=scene.observe
    def observe():
        if scene.calls: scene.position=next(positions)
        return base()
    stepper=scene.stepper(max_delta=6,allowed_bounds=(448,445,458,455))
    stepper.observe=observe
    result=stepper.step(scene.position,(455,449))
    assert result['reached'] and len(result['samples'])==5
    assert len(scene.calls)==1


def test_low_hp_never_dispatches():
    scene=Scene()
    def observe():
        return SceneReading(scene.position,dict(scene.window),0,1002,10,213)
    stepper=scene.stepper(expected_map=1002)
    stepper.observe=observe
    with pytest.raises(ValueError,match='HP'):
        stepper.step(scene.position,scene.destination)
    assert not scene.calls


def test_memory_anchor_tracks_map_edge_and_rejects_stale_projection():
    import struct
    from types import SimpleNamespace
    from conquest.scene_input import memory_player_anchor
    from conquest.memory_life import CLIENT_SHA256
    raw=struct.pack('<6i',953,557,0,0,524,457)
    adapter=SimpleNamespace(expected_sha256=CLIENT_SHA256,read_block=lambda a,n:raw)
    life=SimpleNamespace(position=(953,557),object_address=100000)
    observer=SimpleNamespace(adapter=adapter)
    assert memory_player_anchor(observer,life)==(524,457)
    life.position=(954,557)
    with pytest.raises(ValueError,match='changed'):memory_player_anchor(observer,life)
    life.position=(953,557)
    values=iter([raw,bytes(24)])
    adapter.read_block=lambda a,n:next(values)
    with pytest.raises(ValueError,match='changed'):memory_player_anchor(observer,life)


def test_map_edge_moves_shorten_without_clicking_hud():
    from conquest.scene_input import visible_route_delta
    assert visible_route_delta((0,-12),(352,192))==(0,-1)
    assert visible_route_delta((0,-12),(352,145)) is None
    assert visible_route_delta((12,0),(518,396))==(12,0)
    assert visible_route_delta((2,2),(518,396))==(2,2)
