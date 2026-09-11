import tkinter as tk
import pytest
from conquest.nearby_monsters import NearbyMonsters


@pytest.fixture(scope='module')
def tk_root():
    root = tk.Tk()
    root.withdraw()
    yield root
    root.destroy()


@pytest.fixture
def picker(tk_root):
    toggles = []
    widget = NearbyMonsters(tk_root,toggles.append)
    yield widget,toggles
    widget.destroy()


def test_refresh_groups_ids_and_retains_species_selection(picker):
    widget,toggles = picker
    monsters = [{'name':'Pheasant','entity_id':81,'type_id':1},{'name':'Pheasant','entity_id':82,'type_id':1}]
    widget.refresh(monsters,[1])
    assert widget.tree.get_children()==('1',)
    assert widget.tree.item('1','values')==('✓','Pheasant','2')
    widget.tree.focus('1')
    widget.keyboard_toggle()
    assert toggles==[1]
    widget.refresh([],[1])
    assert widget.tree.item('1','values')==('✓','Pheasant','0')
    widget.refresh(monsters+[{'name':'Pheasant','entity_id':83,'type_id':1}],[1])
    assert widget.tree.item('1','values')==('✓','Pheasant','3')


def test_missing_observations_clear_rows_without_changing_targets(picker):
    widget,toggles = picker
    monsters = [{'name':'Pheasant','entity_id':81,'type_id':1}]
    widget.refresh(monsters,[1])
    widget.refresh(monsters,[1],available=False)
    assert widget.tree.item('1','values')==('✓','Pheasant','—')
    assert 'Waiting' in widget.note.get() and '1 groups selected' in widget.note.get()
    assert not toggles


def test_unchanged_refresh_does_not_move_rows_or_rewrite_note(picker, monkeypatch):
    widget, _ = picker
    monsters = [{'name':'Pheasant','entity_id':81,'type_id':1}]
    widget.refresh(monsters,[1])
    monkeypatch.setattr(widget.tree,'move',lambda *a,**k:pytest.fail('Unnecessary row move'))
    monkeypatch.setattr(widget.note,'set',lambda *a:pytest.fail('Unnecessary text redraw'))
    widget.refresh(monsters,[1])


def test_unavailable_sample_keeps_unselected_rows_and_note_height(picker):
    widget, _ = picker
    widget.refresh([{'name':'Pheasant','entity_id':81,'type_id':1}],[])
    widget.update_idletasks()
    height = widget.winfo_reqheight()
    widget.refresh([],[],available=False)
    widget.update_idletasks()
    assert widget.tree.get_children()==('1',)
    assert widget.tree.item('1','values')[-1]=='—'
    assert widget.winfo_reqheight()==height


def test_live_labels_and_ids_cannot_change_embedded_sidebar_width(tk_root, tmp_path, monkeypatch):
    from types import SimpleNamespace
    from conquest.desktop_app import DesktopApp
    from conquest import mouse_priority
    monkeypatch.setattr(mouse_priority,'_guard',None)
    monkeypatch.setattr(tk_root,'after',lambda *args:None)
    app = DesktopApp(tk_root,'unused.yaml',catalog=SimpleNamespace(windows=lambda:[]),
                     output=tmp_path,requires_elevation=False)
    try:
        app.embedded_layout()
        for count, note in [(0,'Waiting'),(128,'Current HP and monster life state need live validation; '*3),(1,'Ready')]:
            app.memory_text.set(note)
            app.render_matched_ids({'observations_available':True,'control':{'resolved_target_ids':list(range(count))}})
            app.nearby.refresh([{'name':'Pheasant','entity_id':i,'type_id':1} for i in range(count)],[1])
            tk_root.update_idletasks()
            assert app.sidebar.winfo_reqwidth()==500
        writes = []
        app.ids.trace_add('write',lambda *args:writes.append(True))
        app.render_matched_ids({'observations_available':True,'control':{'resolved_target_ids':[0]}})
        app.render_matched_ids({'observations_available':False,'control':{'resolved_target_ids':[]}})
        assert not writes and app.ids.get()=='0'
        assert 'refresh pending' in app.ids_label.cget('text')
        app.compact()
        assert app.sidebar.pack_propagate()
    finally:
        app.sidebar.destroy()
        app.pane.destroy()
