"""Owned hosting must preserve native activation and reversible window state."""
from types import SimpleNamespace
from queue import Queue

import pytest

from conquest.window_host import EmbeddedWindow, HostApi, WindowState, WS_CHILD, WS_POPUP


GAME, PANE, WRAPPER, ORIGINAL_OWNER = 20, 30, 10, 90


class WindowGui:
    def __init__(self, state):
        self.calls = []
        self.style, self.exstyle, self.owner = state.style, state.exstyle, state.owner
        self.parent = 0
        self.foreground = GAME
        self.placement = state.placement
        self.screen_origin = (-600, 80)
        self.rect = (0, 0, 100, 100)
        self.reject_owner = False
        self.visible = {GAME: True, PANE: True, WRAPPER: True}
        self.minimized = False

    def GetAncestor(self, hwnd, flag):
        assert flag == 2  # GA_ROOT must not silently become GA_ROOTOWNER.
        return WRAPPER if hwnd == PANE or (hwnd == GAME and self.style & WS_CHILD) else hwnd

    def GetForegroundWindow(self):
        return self.foreground

    def ShowWindow(self, hwnd, command):
        self.calls.append(('show', hwnd, command))
        self.visible[hwnd] = command != 0

    def IsWindowVisible(self, hwnd):
        return self.visible.get(hwnd, True)

    def IsIconic(self, hwnd):
        return hwnd == WRAPPER and self.minimized

    def SetWindowLong(self, hwnd, index, value):
        self.calls.append(('long', hwnd, index, value))
        if index == -16:
            self.style = value & 0xffffffff
        elif index == -20:
            self.exstyle = value & 0xffffffff
        elif index == -8 and not self.reject_owner:
            self.owner = value

    SetWindowLongPtr = SetWindowLong

    def GetWindowLong(self, hwnd, index):
        return {-16: self.style, -20: self.exstyle, -8: self.owner}[index]

    def GetWindow(self, hwnd, flag):
        assert flag == 4
        return self.owner

    def SetParent(self, hwnd, parent):
        self.calls.append(('parent', hwnd, parent))
        self.parent = parent

    def GetParent(self, hwnd):
        return self.parent if self.style & WS_CHILD else self.owner

    def ClientToScreen(self, hwnd, point):
        assert hwnd == PANE
        return tuple(a+b for a, b in zip(self.screen_origin, point))

    def SetWindowPos(self, hwnd, after, x, y, width, height, flags):
        self.calls.append(('position', hwnd, after, x, y, width, height, flags))
        self.rect = (x, y, x+width, y+height)

    def GetWindowRect(self, hwnd):
        return self.rect

    def SetWindowPlacement(self, hwnd, placement):
        self.calls.append(('placement', hwnd, placement))
        self.placement = placement

    def IsChild(self, parent, child):
        return parent == GAME and child == 21


def owned_api():
    state = WindowState({'pid': 42, 'creation_time_100ns': 123}, GAME,
                        0x10cf0000, 0x00040000, ORIGINAL_OWNER,
                        (0, 1, (0, 0), (0, 0), (100, 100, 900, 700)))
    api = HostApi.__new__(HostApi)
    api.gui = WindowGui(state)
    api.assert_owner = lambda hwnd, identity: None
    api.owns_window = lambda hwnd, identity: True
    api.require_matching_dpi = lambda *args: None
    api.snapshot = lambda hwnd, identity: state
    return api, state


def test_owned_host_keeps_game_top_level_and_assigns_wrapper_owner():
    api, state = owned_api()
    api.embed_owned(state, PANE)
    assert not api.gui.style & WS_CHILD
    assert api.gui.style & WS_POPUP
    assert not api.gui.style & (0x00c00000 | 0x00040000)
    assert not api.gui.exstyle & (0x00040000 | 0x08000000 | 0x00000008)
    assert api.gui.owner == WRAPPER
    assert api.gui.GetAncestor(GAME, 2) == GAME
    assert ('parent', GAME, PANE) not in api.gui.calls


def test_owned_host_rejects_failed_owner_assignment():
    api, state = owned_api()
    api.gui.reject_owner = True
    with pytest.raises(ValueError):
        api.embed_owned(state, PANE)


def test_owned_resize_uses_screen_coordinates_without_activation():
    api, state = owned_api()
    api.resize_owned(state, PANE, 1036, 793)
    positioned = [call for call in api.gui.calls if call[0] == 'position'][-1]
    assert positioned[1] == GAME
    assert positioned[3:7] == (-600, 80, 1036, 793)
    assert positioned[-1] & 0x10  # SWP_NOACTIVATE
    assert positioned[-1] & 0x4  # SWP_NOZORDER
    assert api.gui.foreground == GAME


def test_unchanged_owned_geometry_does_not_repeat_window_mutations():
    api, state = owned_api()
    api.resize_owned(state, PANE, 1036, 793)
    api.gui.calls.clear()
    for _ in range(4):
        api.resize_owned(state, PANE, 1036, 793)
    assert api.gui.calls == []


@pytest.mark.parametrize('hidden', ['pane', 'wrapper', 'minimized'])
def test_owned_client_tracks_host_visibility_without_taking_focus(hidden):
    api, state = owned_api()
    api.gui.foreground = 777
    if hidden == 'minimized':
        api.gui.minimized = True
    else:
        api.gui.visible[PANE if hidden == 'pane' else WRAPPER] = False
    api.resize_owned(state, PANE, 1036, 793)
    assert not api.gui.visible[GAME]
    assert not any(call[0] == 'position' for call in api.gui.calls)
    api.gui.minimized = False
    api.gui.visible[PANE] = api.gui.visible[WRAPPER] = True
    api.resize_owned(state, PANE, 1036, 793)
    assert api.gui.visible[GAME]
    assert ('show', GAME, 4) in api.gui.calls  # SW_SHOWNOACTIVATE
    assert api.gui.foreground == 777


def test_owned_restore_recovers_original_owner_styles_and_placement():
    api, state = owned_api()
    api.embed_owned(state, PANE)
    api.resize_owned(state, PANE, 1036, 793)
    api.restore(state)
    assert (api.gui.style, api.gui.exstyle, api.gui.owner, api.gui.placement) == (
        state.style, state.exstyle, state.owner, state.placement)


@pytest.mark.parametrize('foreground', [WRAPPER, 777])
def test_owned_focus_rejects_wrapper_or_other_app_foreground(foreground):
    api, state = owned_api()
    api.gui.foreground = foreground
    api.thread_info = lambda hwnd: pytest.fail('Must reject before keyboard handoff')
    with pytest.raises(ValueError):
        api.focus(state)
    assert api.gui.foreground == foreground


def test_owned_focus_preserves_game_text_field_when_game_is_foreground():
    api, state = owned_api()
    api.thread_info = lambda hwnd: (200, SimpleNamespace(hwndFocus=21))
    assert api.focus(state) == 21
    assert api.gui.foreground == GAME


@pytest.mark.parametrize('restore_fails', [False, True])
def test_app_close_restores_owned_client_before_destroying_owner(restore_fails):
    from conquest.desktop_app import DesktopApp

    api, state = owned_api()
    host = EmbeddedWindow(api, mode='owned')
    host.attach(GAME, state.identity, PANE, 1036, 793)
    assert api.gui.owner == WRAPPER
    destroyed = []
    if restore_fails:
        def fail_restore(state):
            raise OSError('Owner restoration failed')
        api.restore = fail_restore

    def destroy_owner():
        # At this point Windows may destroy any HWND still owned by the wrapper.
        assert api.gui.owner == ORIGINAL_OWNER
        assert api.gui.style == state.style
        assert api.gui.placement == state.placement
        destroyed.append(WRAPPER)

    app = SimpleNamespace(host=host, runtime=None, thread=None, closing=True,
        messages=Queue(), launch_watch=SimpleNamespace(pending=False),
        stop_observer=lambda: None, record=lambda **fields: None,
        root=SimpleNamespace(destroy=destroy_owner),
        state_text=SimpleNamespace(set=lambda message: None))
    keep_open = DesktopApp._poll(app)
    if restore_fails:
        assert keep_open and not destroyed and host.saved is state
    else:
        assert not keep_open and destroyed == [WRAPPER] and host.saved is None
