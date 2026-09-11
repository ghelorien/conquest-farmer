import pytest

from conquest.input_probe import click_probe, key_probe, positioned_click_probe


class FakeTarget:
    hwnd = 100

    def __init__(self):
        self.messages = []
        self.state = {"pid": 1, "hwnd": 100, "client_size": [800, 600],
                      "foreground": 200, "cursor": [900, 300], "minimized": False}

    def snapshot(self):
        return dict(self.state)

    def post(self, message, wparam, lparam):
        self.messages.append((message, wparam, lparam))


class CursorTarget(FakeTarget):
    def __init__(self):
        super().__init__()
        self.moves = []
    def screen_point(self,x,y): return [x+50,y+50]
    def mouse_busy(self): return False
    def move_cursor(self,point):
        self.moves.append(list(point))
        self.state['cursor'] = list(point)


def test_positioned_probe_restores_cursor_without_activating_game():
    target = CursorTarget()
    result = positioned_click_probe(target,10,20,[800,600],sleep=lambda _:None)
    assert target.moves==[[60,70],[900,300]]
    assert [m[0] for m in target.messages]==[0x200,0x201,0x202]
    assert result['focus_unchanged'] and result['cursor_restored'] and not result['qualified']


def test_positioned_probe_defers_to_existing_mouse_activity():
    target = CursorTarget()
    def moving(_): target.state['cursor']=[5,6]
    with pytest.raises(ValueError,match='Desktop input is active'):
        positioned_click_probe(target,10,20,[800,600],sleep=moving)
    assert not target.moves and not target.messages


def test_positioned_probe_does_not_overwrite_new_user_cursor_position():
    target = CursorTarget()
    def moving(_):
        if target.moves: target.state['cursor']=[5,6]
    with pytest.raises(ValueError,match='User input'):
        positioned_click_probe(target,10,20,[800,600],sleep=moving)
    assert target.moves==[[60,70]]
    assert [m[0] for m in target.messages]==[0x200]


def test_positioned_probe_releases_button_on_interruption():
    target = CursorTarget()
    def interrupt(_):
        if any(m[0]==0x201 for m in target.messages):
            raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        positioned_click_probe(target,10,20,[800,600],sleep=interrupt)
    assert target.messages[-1][0]==0x202 and target.moves[-1]==[900,300]


def test_queue_success_never_qualifies_gameplay():
    target = FakeTarget()
    report = click_probe(target, 10, 20, [800, 600], sleep=lambda _: None)
    assert report["messages_queued"]
    assert report["sampled_desktop_state_unchanged"]
    assert report["outcome"] == "awaiting_game_observation"
    assert not report["qualified"]


@pytest.mark.parametrize('kind',['mouse','keyboard'])
def test_embedded_wrapper_foreground_is_not_a_background_test(kind):
    target = FakeTarget()
    target.state.update(root_hwnd=200)
    with pytest.raises(ValueError):
        if kind=='mouse':
            click_probe(target,10,20,[800,600],sleep=lambda _:None)
        else:
            key_probe(target,0x7A,0x57,[800,600],sleep=lambda _:None)
    assert not target.messages


def test_mouse_motion_can_settle_before_a_button_is_dispatched():
    target = FakeTarget()
    waits = []
    def sleep(delay): waits.append((delay,len(target.messages)))
    result = click_probe(target,10,20,[800,600],move_settle_seconds=.2,sleep=sleep)
    assert waits[0]==(.2,1)
    assert result['move_settle_seconds']==.2


def test_desktop_change_while_motion_settles_prevents_button_dispatch():
    target = FakeTarget()
    def sleep(delay): target.state['foreground']=100
    with pytest.raises(ValueError,match='only mouse movement'):
        click_probe(target,10,20,[800,600],move_settle_seconds=.2,sleep=sleep)
    assert [message[0] for message in target.messages]==[0x200]


@pytest.mark.parametrize("key,value", [("foreground", 100), ("minimized", True), ("client_size", [1000, 600])])
def test_invalid_window_state_sends_no_input(key, value):
    target = FakeTarget()
    target.state[key] = value
    with pytest.raises(ValueError):
        click_probe(target, 10, 20, [800, 600])
    assert not target.messages


def test_expired_action_sends_no_input():
    target = FakeTarget()
    ticks = iter([0, 1])
    with pytest.raises(ValueError, match="expired"):
        click_probe(target, 10, 20, [800, 600], clock=lambda: next(ticks))
    assert not target.messages


def test_interruption_releases_button():
    target = FakeTarget()

    def interrupted(_):
        raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        click_probe(target, 10, 20, [800, 600], sleep=interrupted)
    assert target.messages[-1][0] == 0x202


def test_desktop_changes_are_not_reported_as_isolated():
    target = FakeTarget()

    def changed(_):
        target.state["foreground"] = 300

    report = click_probe(target, 10, 20, [800, 600], sleep=changed)
    assert not report["sampled_desktop_state_unchanged"]
    assert report["outcome"] == "desktop_or_window_state_changed"


def test_keyboard_messages_have_scan_transition_bits():
    target = FakeTarget()
    result = key_probe(target, 0x7A, 0x57, [800, 600], sleep=lambda _: None)
    assert target.messages == [(0x100, 0x7A, 1 | (0x57 << 16)),
                               (0x101, 0x7A, 1 | (0x57 << 16) | (3 << 30))]
    assert result["sampled_desktop_state_unchanged"] and not result["qualified"]


@pytest.mark.parametrize("key,value", [("foreground", 100), ("minimized", True), ("client_size", [1000, 600])])
def test_keyboard_rejects_incompatible_window(key, value):
    target = FakeTarget()
    target.state[key] = value
    with pytest.raises(ValueError):
        key_probe(target, 0x7A, 0x57, [800, 600])
    assert not target.messages


def test_keyboard_release_on_interruption():
    target = FakeTarget()
    def interrupt(_):
        raise KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        key_probe(target, 0x7A, 0x57, [800, 600], sleep=interrupt)
    assert target.messages[-1][0] == 0x101


def test_keyboard_rejects_emergency_stop_key():
    target = FakeTarget()
    with pytest.raises(ValueError):
        key_probe(target, 0x7B, 0x58, [800, 600])
    assert not target.messages


def test_background_control_chord_uses_only_window_messages():
    target = FakeTarget()
    result = key_probe(target, 0x7A, 0x57, [800, 600], control_scan=0x1D, sleep=lambda _: None)
    assert [(message, vk) for message, vk, _ in target.messages] == [
        (0x100, 0x11), (0x100, 0x7A), (0x101, 0x7A), (0x101, 0x11)]
    assert result["control"] and result["sampled_desktop_state_unchanged"]


def test_modified_mouse_probe_queues_control_and_matching_mouse_flags():
    target=FakeTarget()
    result=click_probe(target,10,20,[800,600],control=True,sleep=lambda _:None)
    packed=(20<<16)|10
    assert target.messages==[
        (0x100,0x11,1 | 0x1D<<16),
        (0x200,0x8,packed),(0x201,0x9,packed),(0x202,0x8,packed),
        (0x101,0x11,1 | 0x1D<<16 | 3<<30)]
    assert result['control'] and not result['qualified']


def test_modified_mouse_probe_releases_both_buttons_after_interruption():
    target=FakeTarget()
    def interrupt(_):
        if any(message==0x201 for message,_,_ in target.messages):
            raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        click_probe(target,10,20,[800,600],control=True,sleep=interrupt)
    assert [message for message,_,_ in target.messages][-2:]==[0x202,0x101]


def test_modified_mouse_probe_releases_control_when_mouse_move_fails():
    target=FakeTarget()
    post=target.post
    def fail(message,wparam,lparam):
        if message==0x200: raise OSError('Mouse move queue failed')
        post(message,wparam,lparam)
    target.post=fail
    with pytest.raises(OSError):
        click_probe(target,10,20,[800,600],control=True,sleep=lambda _:None)
    assert [message for message,_,_ in target.messages]==[0x100,0x101]


def test_modified_mouse_probe_stops_if_user_changes_foreground_before_mouse():
    target=FakeTarget()
    def changed(_): target.state['foreground']=300
    with pytest.raises(ValueError,match='modifier preparation'):
        click_probe(target,10,20,[800,600],control=True,sleep=changed)
    assert [message for message,_,_ in target.messages]==[0x100,0x101]


def test_modified_mouse_probe_rejects_non_boolean_control():
    target=FakeTarget()
    with pytest.raises(ValueError,match='boolean'):
        click_probe(target,10,20,[800,600],control='yes')
    assert not target.messages
