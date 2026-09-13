from types import SimpleNamespace
import pytest

from conquest.layout_revision import LayoutChanged,SharedLayoutRevision
from conquest.target_actionability import target_actionability


def native(state):
    return lambda target:(dict(state),(10,20),144,3,(0,0,1920,1080))


def test_structural_revision_ignores_cursor_but_rejects_panel_move():
    state={'hwnd':7,'root_hwnd':7,'client_size':[1000,800],
           'foreground':7,'minimized':False,'cursor':[1,2]}
    panels=[{'name':'Inventory','address':9,'geometry':[10,20,200,300],'scroll':[0,0]}]
    target=SimpleNamespace(hwnd=7)
    manager=SharedLayoutRevision(target,native_reader=native(state),windows=lambda:panels,
        gui_size=lambda:(1000,800),manual_active=lambda:False)
    revision=manager.read()
    state['cursor']=[400,500]
    manager.assert_current(revision)
    panels[0]['geometry'][0]+=1
    with pytest.raises(LayoutChanged,match='layout changed'):
        manager.assert_current(revision)


def test_actionability_rejects_hud_and_draggable_panel():
    assert target_actionability((736,400),(1000,800),(1500,1200))['actionable']
    hud=target_actionability((20,400),(1000,800),(1500,1200))
    assert not hud['actionable'] and hud['reason']=='scene_control'
    covered=target_actionability((736,400),(1000,800),(1500,1200),
        [{'name':'Inventory','geometry':[700,350,200,300]}])
    assert not covered['actionable'] and covered['reason']=='panel_occlusion'


def test_manual_input_and_offscreen_native_window_are_not_actionable():
    state={'hwnd':7,'root_hwnd':7,'client_size':[1000,800],
           'foreground':7,'minimized':False}
    manager=SharedLayoutRevision(SimpleNamespace(hwnd=7),native_reader=native(state),
        manual_active=lambda:True)
    with pytest.raises(LayoutChanged,match='Manual input'):
        manager.read()
    manager=SharedLayoutRevision(SimpleNamespace(hwnd=7),
        native_reader=lambda target:(state,(3000,20),96,1,(0,0,1920,1080)),
        manual_active=lambda:False)
    with pytest.raises(LayoutChanged,match='offscreen'):
        manager.read()
