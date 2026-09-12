from types import SimpleNamespace
import sys

import pytest

from conquest.background_surface import BackgroundSurface, monitor_rectangles
from conquest.window_host import WindowState


class Native:
    def __init__(self):
        self.identity = {'pid': 7, 'creation_time_100ns': 100}
        self.style, self.exstyle, self.owner = 0x10cf0000, 0x40000, 30
        self.rect = (40, 50, 1092, 882)
        self.size = (1036, 793)
        self.placement = (0, 1, (-1, -1), (-1, -1), self.rect)
        self.visible, self.iconic, self.zoomed = True, False, False
        self.foreground = 99
        self.calls = []
        self.accept = True
        self.gui = self

    def snapshot(self, hwnd, identity):
        self.assert_owner(hwnd, identity)
        return WindowState(dict(identity), hwnd, self.style, self.exstyle, self.owner, self.placement)

    def assert_owner(self, hwnd, identity):
        if identity != self.identity:
            raise ValueError('Identity changed')

    def GetForegroundWindow(self): return self.foreground
    def GetAncestor(self, hwnd, flag): return hwnd
    def IsChild(self, hwnd, other): return other == 21
    def GetWindow(self, hwnd, flag): return self.owner if hwnd == 20 else 0
    def IsIconic(self, hwnd): return self.iconic
    def IsWindowVisible(self, hwnd): return self.visible
    def GetClientRect(self, hwnd): return (0, 0, *self.size)
    def GetWindowRect(self, hwnd): return self.rect
    def GetWindowPlacement(self, hwnd):
        return (self.placement[0], 3, *self.placement[2:]) if self.zoomed else self.placement
    def GetWindowLong(self, hwnd, index): return {-16: self.style, -20: self.exstyle}[index]

    def SetWindowLong(self, hwnd, index, value):
        self.calls.append(('long', index, value))
        if index == -8: self.owner = value
        if index == -16: self.style = value & 0xffffffff
        if index == -20: self.exstyle = value & 0xffffffff

    def SetWindowPos(self, hwnd, after, x, y, width, height, flags):
        assert flags & 0x10
        self.calls.append(('position', flags))
        if self.accept:
            self.rect = (x, y, x+width, y+height)
            if flags & 0x40: self.visible = True

    def SetWindowPlacement(self, hwnd, placement):
        assert placement[1] in (0, 4)
        self.calls.append(('placement', placement))
        self.placement = placement
        self.visible = placement[1] != 0


def setup():
    api = Native()
    env = SimpleNamespace(layout=[(-1920, 0, 0, 1080), (0, -200, 2560, 1440)], now=0)
    def sleep(seconds): env.now += seconds
    surface = BackgroundSurface(api, monitors=lambda: env.layout,
                                clock=lambda: env.now, sleep=sleep, timeout=.1)
    return api, env, surface


def test_parking_is_unowned_fixed_size_offscreen_and_nonactivating():
    api, env, surface = setup()
    result = surface.park(20, api.identity, expected_size=(1036, 793))
    assert result['rect'] == [2624, -200, 3660, 593]
    assert api.owner == 0
    assert api.foreground == 99
    assert api.size == (1036, 793)
    assert api.exstyle & 0x08000000 and api.exstyle & 0x80
    assert not api.exstyle & 0x40000
    assert surface.check() == result


@pytest.mark.parametrize('visible', [False, True])
def test_restore_preserves_style_owner_placement_fields_and_visibility(visible):
    api, env, surface = setup()
    api.visible = visible
    before = api.snapshot(20, api.identity)
    surface.park(20, api.identity)
    surface.restore()
    assert (api.style, api.exstyle, api.owner) == (before.style, before.exstyle, before.owner)
    assert api.placement[:1] == before.placement[:1]
    assert api.placement[2:] == before.placement[2:]
    assert api.visible == visible and api.foreground == 99
    assert surface.saved is None
    calls = list(api.calls)
    surface.restore()
    assert api.calls == calls


@pytest.mark.parametrize('field,value', [('foreground', 20), ('foreground', 21), ('iconic', True), ('zoomed', True)])
def test_manual_or_nonrestored_window_is_rejected_without_mutation(field, value):
    api, env, surface = setup()
    setattr(api, field, value)
    with pytest.raises(ValueError): surface.park(20, api.identity)
    assert not api.calls and surface.saved is None


def test_expected_viewport_must_match_before_mutation():
    api, env, surface = setup()
    with pytest.raises(ValueError, match='viewport'):
        surface.park(20, api.identity, expected_size=(800, 600))
    assert not api.calls


def test_pid_reuse_blocks_checks_and_restoration_to_replacement():
    api, env, surface = setup()
    surface.park(20, api.identity)
    api.identity = {'pid': 7, 'creation_time_100ns': 101}
    calls = list(api.calls)
    with pytest.raises(ValueError, match='Identity'): surface.check()
    with pytest.raises(ValueError, match='Identity'): surface.restore()
    assert api.calls == calls and surface.saved is not None


def test_monitor_change_invalidates_surface_but_allows_nonactivating_restore():
    api, env, surface = setup()
    surface.park(20, api.identity)
    env.layout = [(0, 0, 4096, 2160)]
    with pytest.raises(ValueError, match='Monitor layout'): surface.check()
    surface.restore()
    assert surface.saved is None


def test_manual_activation_yields_and_retains_recovery_record():
    api, env, surface = setup()
    surface.park(20, api.identity)
    api.foreground = 20
    calls = list(api.calls)
    with pytest.raises(ValueError, match='manual'): surface.check()
    with pytest.raises(ValueError, match='manual'): surface.restore()
    assert api.calls == calls and surface.saved is not None


def test_missing_native_acknowledgement_does_not_qualify_surface():
    api, env, surface = setup()
    api.accept = False
    with pytest.raises(ValueError, match='acknowledged'): surface.park(20, api.identity)
    assert not surface.ready and surface.saved is not None
    api.accept = True
    surface.restore()


def test_background_geometry_change_stops_messages_without_repairing_it():
    api, env, surface = setup()
    surface.park(20, api.identity)
    api.rect = (0, 0, 1036, 793)
    calls = list(api.calls)
    with pytest.raises(ValueError, match='geometry'): surface.check()
    assert api.calls == calls


def test_monitor_enumeration_order_is_not_a_layout_change():
    api, env, surface = setup()
    surface.park(20, api.identity)
    env.layout.reverse()
    assert surface.check()['hwnd'] == 20


@pytest.mark.skipif(sys.platform != 'win32', reason='Read-only installed Win32 API smoke check')
def test_installed_native_dependencies_are_available_without_window_mutation():
    surface = BackgroundSurface()
    gui = surface.api.gui
    for name in ('GetWindowPlacement', 'GetForegroundWindow', 'GetAncestor',
                 'IsChild', 'GetWindow', 'IsIconic', 'GetClientRect',
                 'GetWindowRect', 'IsWindowVisible', 'SetWindowLong',
                 'SetWindowPos', 'SetWindowPlacement', 'GetWindowLong'):
        assert callable(getattr(gui, name))
    assert monitor_rectangles() == surface._layout()
    hwnd = gui.GetForegroundWindow()
    if hwnd:
        assert len(gui.GetWindowPlacement(hwnd)) == 5
    assert surface.saved is None
