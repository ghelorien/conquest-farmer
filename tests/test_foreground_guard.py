import pytest
from conquest.foreground import require_click_position


def test_expected_pointer_and_geometry_allow_click():
    require_click_position(dict(foreground=7, minimized=False, client_size=[100, 100], cursor=[50, 60]), 7, (100, 100), (50, 60))


@pytest.mark.parametrize("change", [dict(foreground=9), dict(minimized=True), dict(client_size=[120, 100]), dict(cursor=[80, 60])])
def test_cursor_interference_and_window_changes_reject_click(change):
    state = dict(foreground=7, minimized=False, client_size=[100, 100], cursor=[50, 60])
    state.update(change)
    with pytest.raises(ValueError, match="no button pressed"):
        require_click_position(state, 7, (100, 100), (50, 60))


@pytest.mark.parametrize('size,minimized',[([120,100],False),([100,100],True)])
def test_geometry_changes_pause_before_input(monkeypatch,size,minimized):
    from types import SimpleNamespace
    from conquest import foreground
    from conquest.capture import CaptureUnavailable
    monkeypatch.setattr(foreground,'require_idle',lambda:None)
    target=SimpleNamespace(hwnd=7,snapshot=lambda:{'foreground':7,'client_size':size,'minimized':minimized})
    with pytest.raises(CaptureUnavailable,match='no input sent'):
        foreground.foreground_click(target,50,50,(100,100),require_foreground=True)
    with pytest.raises(CaptureUnavailable,match='no input sent'):
        foreground.foreground_key(target,112,(100,100),require_foreground=True)


def test_escape_uses_the_same_focus_guard_and_other_keys_remain_restricted(monkeypatch):
    from types import SimpleNamespace
    from conquest import foreground
    from conquest.capture import CaptureUnavailable
    monkeypatch.setattr(foreground,'require_idle',lambda:None)
    target=SimpleNamespace(hwnd=7,snapshot=lambda:{'foreground':8,'client_size':[1420,1009],'minimized':False})
    with pytest.raises(CaptureUnavailable,match='lost focus'):
        foreground.foreground_key(target,0x1B,(1420,1009),require_foreground=True)
    for vk in (0x7B,0x41,True):
        with pytest.raises(ValueError,match='supports'):
            foreground.foreground_key(target,vk,(1420,1009))


def test_drag_layout_change_stops_held_movement_and_releases_once(monkeypatch):
    import ctypes as c
    from types import SimpleNamespace
    from conquest import foreground
    from conquest.layout_revision import LayoutChanged
    cursor=[0,0];events=[]
    def send(count,pointer,size):
        event=c.cast(pointer,c.POINTER(foreground.Input))[0]
        flags=event.data.mi.dwFlags;events.append(flags)
        if flags&1:
            cursor[:]=[round(event.data.mi.dx*100/65535),round(event.data.mi.dy*100/65535)]
        return 1
    def bind(user,name,args,result):
        if name=='ClientToScreen':return lambda hwnd,point:True
        if name=='GetSystemMetrics':return lambda index:{76:0,77:0,78:101,79:101}[index]
        if name=='SendInput':return send
        raise AssertionError(name)
    backend=SimpleNamespace(user=object(),foreground=lambda:7,error=lambda name:OSError(name))
    target=SimpleNamespace(hwnd=7,backend=backend,snapshot=lambda:{'foreground':7,
        'client_size':[100,100],'minimized':False,'cursor':list(cursor)})
    monkeypatch.setattr(foreground,'bind',bind)
    monkeypatch.setattr(foreground,'guarded_send',lambda value:value)
    monkeypatch.setattr(foreground,'require_idle',lambda:None)
    monkeypatch.setattr(foreground.time,'sleep',lambda seconds:None)
    checks=[]
    def layout_guard():
        checks.append(1)
        if len(checks)==3:raise LayoutChanged('resized')
    with pytest.raises(LayoutChanged,match='resized'):
        foreground.foreground_drag.__wrapped__(target,(10,10),(90,90),(100,100),
                                              layout_guard=layout_guard)
    assert events==[0xC001,0x2,0xC001,0x4]
