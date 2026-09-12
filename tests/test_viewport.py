import struct
from types import SimpleNamespace as NS
import pytest
from conquest.viewport import size_for,clear_scene,scene_bounds,revive_point


def test_large_viewport_allows_new_scene_but_excludes_bottom_controls():
    size=(1420,1009)
    assert size_for(NS(viewport_size=lambda:size))==size
    assert clear_scene((1100,750),size)
    assert not clear_scene((1100,750))
    assert not clear_scene((710,950),size)
    assert scene_bounds(size)==(80,140,1340,883)


@pytest.mark.parametrize('size',[(1036,793),(1420,1009),(1920,1080)])
def test_world_targets_avoid_fly_descend_and_skill_popups(size):
    width,height=size
    for dx in (-61,0,61):
        assert not clear_scene((width/2+dx,height-152),size)
    assert not clear_scene((width-100,height-150),size)
    # Ordinary scene space next to/above the popup remains available.
    if width>=1420:
        assert clear_scene((width/2+120,height-152),size)
        assert clear_scene((width/2,height-181),size)


def test_shortened_movement_cannot_reenter_descend_popup():
    from conquest.scene_input import visible_route_delta,clear_route_point
    size=(1420,1009);bounds=scene_bounds(size)
    assert not clear_route_point((710,832),bounds)
    delta=visible_route_delta((10,10),(710,512),bounds)
    assert delta==(9,9)
    assert clear_scene((710,512+sum(delta)*16),size)


def test_final_world_click_guard_rejects_popup_before_input():
    from conquest.capture import CaptureUnavailable
    from conquest.viewport import require_world_point
    with pytest.raises(CaptureUnavailable,match='HUD'):
        require_world_point((710,832),(1420,1009))
    require_world_point((1100,750),(1420,1009))


def test_live_memory_gui_accepts_fly_popup_below_old_viewport():
    from test_memory_shop import gui_fixture
    gui,record=gui_fixture()
    gui.session.viewport_size=lambda:(1420,1009)
    struct.pack_into('<4f',record,0x18,682,829,56,56)
    assert gui.read('Shop').position==(682,829)


def test_gui_resize_during_read_is_rejected():
    from test_memory_shop import gui_fixture
    gui,_=gui_fixture();sizes=iter([(1420,1009),(1600,1100)])
    gui.session.viewport_size=lambda:next(sizes)
    with pytest.raises(ValueError,match='resized'):gui.read('Shop')


def test_revive_tracks_memory_control_bar_without_stretching_coordinates(monkeypatch):
    control=NS(position=(245.,907.),size=(930.,102.),scroll=(0.,0.))
    monkeypatch.setattr('conquest.memory_shop.MemoryGui',lambda s:NS(read=lambda name:control))
    assert revive_point(NS(),(1420,1009))==(710,856)
    control.position=(250.,907.)
    with pytest.raises(ValueError,match='anchoring'):revive_point(NS(),(1420,1009))


def test_player_anchor_and_jump_projection_use_new_viewport():
    from conquest.scene_input import memory_player_anchor
    from conquest.memory_life import CLIENT_SHA256
    from conquest.navigation import native_movement_delta
    raw=struct.pack('<6i',214,259,0,0,1100,820)
    adapter=NS(expected_sha256=CLIENT_SHA256,viewport_size=lambda:(1420,1009),read_block=lambda a,n:raw)
    assert memory_player_anchor(NS(adapter=adapter),NS(position=(214,259),object_address=100000))==(1100,820)
    assert native_movement_delta(12,0,viewport=(1420,1009))==(12,0)


def test_fly_point_follows_relocated_popup(monkeypatch):
    from test_xp_skill import fixture
    from conquest.xp_skill import read_xp,fly_point
    observer,_,window=fixture(monkeypatch)
    window.position=(682.,829.)
    assert fly_point(observer,read_xp(observer))==(710,857)


def test_scatter_can_use_new_right_hand_scene_without_shortening_jump():
    import numpy as np
    from conquest.navigation import TerrainMap
    from conquest.scatter_movement import scatter_landing
    terrain=TerrainMap(1011,100,100,np.ones((100,100),dtype=bool),'',(),())
    terrain.blocked[50,50:64]=False
    supervisor=NS(recovery=NS(terrain=terrain),observer=NS(viewport_size=lambda:(1420,1009)))
    targets=[NS(world_position=(63,50),current_hp=100)]
    landing=scatter_landing(supervisor,targets,(50,50),(20,20,80,80),2,anchor=(710,504))
    assert landing in ((61,50),(62,50))
    supervisor.observer.viewport_size=lambda:(1036,793)
    assert scatter_landing(supervisor,targets,(50,50),(20,20,80,80),2,anchor=(710,504)) is None


def test_market_detour_uses_new_visible_right_corridor():
    import numpy as np
    from conquest.navigation import TerrainMap
    from conquest.market_navigation import recovery_landing
    terrain=TerrainMap(1011,100,100,np.ones((100,100),dtype=bool),'',(),())
    terrain.blocked[50,50:81]=False
    args=(terrain,(50,50),(80,50),(710,504))
    assert recovery_landing(*args,viewport=(1420,1009))==(62,50)
    assert recovery_landing(*args) is None


@pytest.mark.parametrize('resize',[False,True])
def test_warehouse_typing_accepts_current_size_but_stops_if_resized(monkeypatch,resize):
    import ctypes
    from conquest.warehouse_money import type_amount
    from conquest.capture import CaptureUnavailable
    from conquest.foreground import Input
    events=[];sizes=[[1420,1009]]
    target=NS(hwnd=1,backend=NS(user=None,foreground=lambda:2),
              snapshot=lambda:{'client_size':sizes[0],'minimized':False,'root_hwnd':2})
    def send(*args):
        key=ctypes.cast(args[1],ctypes.POINTER(Input)).contents.data.ki
        events.append((key.wScan,key.dwFlags))
        if resize:sizes[0]=[1600,1100]
        return 1
    monkeypatch.setattr('conquest.mouse_priority.require_idle',lambda:None)
    monkeypatch.setattr('conquest.mouse_priority.guarded_send',lambda fn:fn)
    monkeypatch.setattr('conquest.win32.bind',lambda user,name,*args:send if name=='SendInput' else lambda vk:0)
    if resize:
        with pytest.raises(CaptureUnavailable,match='focus'):type_amount(target,123)
        # A held modifier is released even when the next key is blocked.
        assert [scan for scan,flags in events if not flags&2]==[0x1d]
        assert (0x1d,10) in events
    else:
        type_amount(target,123)
        assert len(events)==10
