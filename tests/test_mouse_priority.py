import pytest
from conquest.mouse_priority import MousePriority, MESSAGE
from conquest.capture import CaptureUnavailable

def fixture():
    now=[10.];state=[(100,100),0]
    guard=MousePriority(lambda:(state[0],state[1]),clock=lambda:now[0])
    assert not guard.active()
    return guard,state,now

def test_user_motion_and_drag_yield_then_resume_without_toggling_farming():
    guard,state,now=fixture()
    state[0]=(101,100)
    assert guard.active()
    now[0]+=1.9
    with pytest.raises(CaptureUnavailable,match='Mouse control is yours'):guard.require_idle()
    state[1]=1
    now[0]+=10
    assert guard.active()
    state[1]=0
    now[0]+=2.01
    assert not guard.active()

def test_bot_movement_and_owned_clicks_do_not_pause_itself():
    guard,state,now=fixture()
    def move():state[0]=(500,500);return 1
    guard.send(move,moving=True)
    assert not guard.active()
    def down():state[1]=1;return 1
    guard.send(down,down=1)
    now[0]+=.1
    assert not guard.active()
    def up():state[1]=0;return 1
    guard.send(up,release=True,up=1)
    assert not guard.active()

def test_takeover_between_bot_move_and_press_blocks_press_but_releases_held_input():
    guard,state,now=fixture()
    guard.owned_buttons=1
    state[0]=(400,400)
    calls=[]
    with pytest.raises(CaptureUnavailable):guard.send(lambda:calls.append('down'))
    guard.send(lambda:calls.append('up') or 1,release=True,up=1)
    assert calls==['up'] and guard.owned_buttons==0

def test_physical_right_button_during_bot_left_drag_takes_priority():
    guard,state,now=fixture()
    guard.owned_buttons=1;state[1]=3
    assert guard.active()


def test_guarded_send_never_releases_user_button_after_blocked_bot_press(monkeypatch):
    import ctypes
    from conquest import mouse_priority as module
    from conquest.foreground import Input, InputUnion, MouseInput
    guard,state,now=fixture()
    monkeypatch.setattr(module,'_guard',guard)
    calls=[]
    send=module.guarded_send(lambda *args:calls.append(args) or 1)
    state[1]=1
    down=Input(type=0,data=InputUnion(mi=MouseInput(0,0,0,2,0,0)))
    up=Input(type=0,data=InputUnion(mi=MouseInput(0,0,0,4,0,0)))
    with pytest.raises(CaptureUnavailable):send(1,ctypes.byref(down),ctypes.sizeof(down))
    assert send(1,ctypes.byref(up),ctypes.sizeof(up))==1
    assert not calls
