import pytest
from conquest.foreground import require_click_position


def test_expected_pointer_and_geometry_allow_click():
    require_click_position(
        dict(foreground=7, minimized=False, client_size=[100, 100], cursor=[50, 60]),
        7,
        (100, 100),
        (50, 60),
    )


@pytest.mark.parametrize(
    "change",
    [
        dict(foreground=9),
        dict(minimized=True),
        dict(client_size=[120, 100]),
        dict(cursor=[80, 60]),
    ],
)
def test_cursor_interference_and_window_changes_reject_click(change):
    state = dict(foreground=7, minimized=False, client_size=[100, 100], cursor=[50, 60])
    state.update(change)
    with pytest.raises(ValueError, match="no button pressed"):
        require_click_position(state, 7, (100, 100), (50, 60))


@pytest.mark.parametrize("size,minimized", [([120, 100], False), ([100, 100], True)])
def test_geometry_changes_pause_before_input(monkeypatch, size, minimized):
    from types import SimpleNamespace
    from conquest import foreground
    from conquest.capture import CaptureUnavailable

    monkeypatch.setattr(foreground, "require_idle", lambda: None)
    target = SimpleNamespace(
        hwnd=7,
        snapshot=lambda: {"foreground": 7, "client_size": size, "minimized": minimized},
    )
    with pytest.raises(CaptureUnavailable, match="no input sent"):
        foreground.foreground_click(target, 50, 50, (100, 100), require_foreground=True)
    with pytest.raises(CaptureUnavailable, match="no input sent"):
        foreground.foreground_key(target, 112, (100, 100), require_foreground=True)


def test_escape_uses_the_same_focus_guard_and_other_keys_remain_restricted(monkeypatch):
    from types import SimpleNamespace
    from conquest import foreground
    from conquest.capture import CaptureUnavailable

    monkeypatch.setattr(foreground, "require_idle", lambda: None)
    target = SimpleNamespace(
        hwnd=7,
        snapshot=lambda: {
            "foreground": 8,
            "client_size": [1420, 1009],
            "minimized": False,
        },
    )
    with pytest.raises(CaptureUnavailable, match="lost focus"):
        foreground.foreground_key(target, 0x1B, (1420, 1009), require_foreground=True)
    for vk in (0x7B, 0x41, True):
        with pytest.raises(ValueError, match="supports"):
            foreground.foreground_key(target, vk, (1420, 1009))


def test_drag_layout_change_stops_held_movement_and_releases_once(monkeypatch):
    import ctypes as c
    from types import SimpleNamespace
    from conquest import foreground
    from conquest.layout_revision import LayoutChanged

    cursor = [0, 0]
    events = []

    def send(count, pointer, size):
        event = c.cast(pointer, c.POINTER(foreground.Input))[0]
        flags = event.data.mi.dwFlags
        events.append(flags)
        if flags & 1:
            cursor[:] = [
                round(event.data.mi.dx * 100 / 65535),
                round(event.data.mi.dy * 100 / 65535),
            ]
        return 1

    def bind(user, name, args, result):
        if name == "ClientToScreen":
            return lambda hwnd, point: True
        if name == "GetSystemMetrics":
            return lambda index: {76: 0, 77: 0, 78: 101, 79: 101}[index]
        if name == "SendInput":
            return send
        if name == "GetAsyncKeyState":
            return lambda key: 0
        raise AssertionError(name)

    backend = SimpleNamespace(
        user=object(), foreground=lambda: 7, error=lambda name: OSError(name)
    )
    target = SimpleNamespace(
        hwnd=7,
        backend=backend,
        snapshot=lambda: {
            "foreground": 7,
            "client_size": [100, 100],
            "minimized": False,
            "cursor": list(cursor),
        },
    )
    monkeypatch.setattr(foreground, "bind", bind)
    monkeypatch.setattr(foreground, "guarded_send", lambda value: value)
    monkeypatch.setattr(foreground, "require_idle", lambda: None)
    monkeypatch.setattr(foreground.time, "sleep", lambda seconds: None)
    checks = []

    def layout_guard():
        checks.append(1)
        if len(checks) == 4:
            raise LayoutChanged("resized")

    with pytest.raises(LayoutChanged, match="resized"):
        foreground.foreground_drag.__wrapped__(
            target, (10, 10), (90, 90), (100, 100), layout_guard=layout_guard
        )
    assert events == [0xC001, 0x2, 0xC001, 0x4]


@pytest.mark.parametrize("race", ["permission", "origin"])
def test_drag_rechecks_after_destination_settle_before_one_release(monkeypatch, race):
    import ctypes as c
    from types import SimpleNamespace
    from conquest import foreground
    from conquest.layout_revision import LayoutChanged

    cursor = [0, 0]
    events = []
    allowed = [True]
    settles = []
    origin = [0]

    def send(count, pointer, size):
        event = c.cast(pointer, c.POINTER(foreground.Input))[0]
        flags = event.data.mi.dwFlags
        events.append(flags)
        if flags & 1:
            cursor[:] = [
                round(event.data.mi.dx * 100 / 65535),
                round(event.data.mi.dy * 100 / 65535),
            ]
        return 1

    def bind(user, name, args, result):
        if name == "ClientToScreen":

            def to_screen(hwnd, pointer):
                point = c.cast(pointer, c.POINTER(foreground.w.POINT))[0]
                point.x += origin[0]
                return True

            return to_screen
        if name == "GetSystemMetrics":
            return lambda index: {76: 0, 77: 0, 78: 101, 79: 101}[index]
        if name == "SendInput":
            return send
        if name == "GetAsyncKeyState":
            return lambda key: 0
        raise AssertionError(name)

    def sleep(seconds):
        if seconds == 0.1:
            settles.append(seconds)
            if len(settles) == 2:
                if race == "permission":
                    allowed[0] = False
                else:
                    origin[0] = 20

    def layout_guard():
        if not allowed[0]:
            raise LayoutChanged("permission changed during destination settle")

    backend = SimpleNamespace(
        user=object(), foreground=lambda: 7, error=lambda name: OSError(name)
    )
    target = SimpleNamespace(
        hwnd=7,
        backend=backend,
        snapshot=lambda: {
            "foreground": 7,
            "client_size": [100, 100],
            "minimized": False,
            "cursor": list(cursor),
        },
    )
    monkeypatch.setattr(foreground, "bind", bind)
    monkeypatch.setattr(foreground, "guarded_send", lambda value: value)
    monkeypatch.setattr(foreground, "require_idle", lambda: None)
    monkeypatch.setattr(foreground.time, "sleep", sleep)
    failure = LayoutChanged if race == "permission" else foreground.CaptureUnavailable
    message = "destination settle" if race == "permission" else "Game moved during drag"
    with pytest.raises(failure, match=message):
        foreground.foreground_drag.__wrapped__(
            target, (10, 10), (90, 90), (100, 100), layout_guard=layout_guard
        )
    assert events.count(0x2) == 1 and events.count(0x4) == 1 and events[-1] == 0x4


def test_scroll_layout_change_after_pointer_move_sends_no_wheel(monkeypatch):
    import ctypes as c
    from types import SimpleNamespace
    from conquest import foreground
    from conquest.layout_revision import LayoutChanged

    cursor = [0, 0]
    events = []

    def send(count, pointer, size):
        event = c.cast(pointer, c.POINTER(foreground.Input))[0]
        flags = event.data.mi.dwFlags
        events.append(flags)
        if flags & 1:
            cursor[:] = [
                round(event.data.mi.dx * 100 / 65535),
                round(event.data.mi.dy * 100 / 65535),
            ]
        return 1

    def bind(user, name, args, result):
        if name == "ClientToScreen":
            return lambda hwnd, point: True
        if name == "GetSystemMetrics":
            return lambda index: {76: 0, 77: 0, 78: 101, 79: 101}[index]
        if name == "SendInput":
            return send
        raise AssertionError(name)

    backend = SimpleNamespace(
        user=object(), foreground=lambda: 7, error=lambda name: OSError(name)
    )
    target = SimpleNamespace(
        hwnd=7,
        backend=backend,
        snapshot=lambda: {
            "foreground": 7,
            "client_size": [100, 100],
            "minimized": False,
            "cursor": list(cursor),
        },
    )
    monkeypatch.setattr(foreground, "bind", bind)
    monkeypatch.setattr(foreground, "guarded_send", lambda value: value)
    monkeypatch.setattr(foreground, "require_idle", lambda: None)
    monkeypatch.setattr(foreground.time, "sleep", lambda seconds: None)
    checks = []

    def layout_guard():
        checks.append(1)
        if len(checks) == 2:
            raise LayoutChanged("resized after move")

    with pytest.raises(LayoutChanged, match="after move"):
        foreground.foreground_scroll.__wrapped__(
            target, (50, 50), -2, (100, 100), layout_guard=layout_guard
        )
    assert events == [0xC001]


@pytest.mark.parametrize("race", ["origin", "foreground", "emergency_stop"])
def test_click_rechecks_client_geometry_and_focus_after_final_callbacks(
    monkeypatch, race
):
    import ctypes as c
    from types import SimpleNamespace
    from conquest import foreground, window_host
    from conquest.capture import CaptureUnavailable

    offset = [0]
    cursor = [0, 0]
    events = []
    control_held = [False]
    foreground_hwnd = [7]
    stopped = [False]

    def to_screen(hwnd, pointer):
        point = c.cast(pointer, c.POINTER(foreground.w.POINT))[0]
        point.x += offset[0]
        return True

    def send(count, pointer, size):
        event = c.cast(pointer, c.POINTER(foreground.Input))[0]
        if event.type == 0:
            flags = event.data.mi.dwFlags
            events.append(("mouse", flags))
            if flags & 1:
                cursor[:] = [
                    round(event.data.mi.dx * 100 / 65535),
                    round(event.data.mi.dy * 100 / 65535),
                ]
        else:
            events.append(("key", event.data.ki.dwFlags))
            control_held[0] = not bool(event.data.ki.dwFlags & 2)
        return 1

    def key_state(key):
        if key == 0x7B and stopped[0]:
            return 0x8000
        return 0x8000 if control_held[0] and key in (0x11, 0xA2) else 0

    functions = {
        "SetForegroundWindow": lambda hwnd: True,
        "ClientToScreen": to_screen,
        "GetSystemMetrics": lambda index: {76: 0, 77: 0, 78: 101, 79: 101}[index],
        "SendInput": send,
        "GetAsyncKeyState": key_state,
    }
    monkeypatch.setattr(foreground, "bind", lambda user, name, *args: functions[name])
    monkeypatch.setattr(foreground, "guarded_send", lambda value: value)
    monkeypatch.setattr(foreground, "require_idle", lambda: None)
    monkeypatch.setattr(foreground.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        window_host,
        "HostApi",
        lambda: SimpleNamespace(
            thread_info=lambda hwnd: (1, SimpleNamespace(hwndFocus=7, hwndActive=7))
        ),
    )
    backend = SimpleNamespace(
        user=object(),
        foreground=lambda: foreground_hwnd[0],
        error=lambda name: OSError(name),
    )
    target = SimpleNamespace(
        hwnd=7,
        backend=backend,
        snapshot=lambda: {
            "foreground": foreground_hwnd[0],
            "root_hwnd": 7,
            "client_size": [100, 100],
            "minimized": False,
            "cursor": list(cursor),
        },
    )

    def before_press():
        if race == "origin":
            offset[0] = 25

    layout_checks = []

    def layout_guard():
        layout_checks.append(1)
        if race == "foreground" and len(layout_checks) == 2:
            foreground_hwnd[0] = 8
        if race == "emergency_stop" and len(layout_checks) == 2:
            stopped[0] = True

    message = {
        "origin": "Game moved before click",
        "foreground": "lost focus before click",
        "emergency_stop": "Emergency stop before click",
    }[race]
    with pytest.raises((CaptureUnavailable, ValueError), match=message):
        foreground.foreground_click.__wrapped__(
            target,
            50,
            60,
            (100, 100),
            control=True,
            require_foreground=True,
            expected_origin=(0, 0),
            before_press=before_press,
            layout_guard=layout_guard,
        )
    assert ("mouse", 0x2) not in events
    assert events[-1] == ("key", 10) and not control_held[0]
