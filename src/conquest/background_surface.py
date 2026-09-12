"""Reversible diagnostic surface; this does not qualify background game input.

Call from the app's established DPI context. Disable ordinary embedding/layout
callbacks for the target for the whole lease. No focus, cursor or game input API
is used here. Render/frame progression remains the caller's memory-only check.
"""
import ctypes
import time

from conquest.window_host import HostApi, FRAME_STYLES, WS_POPUP


def monitor_rectangles():
    import win32api
    return tuple(sorted(tuple(rect) for _, _, rect in win32api.EnumDisplayMonitors()))


class BackgroundSurface:
    def __init__(self, api=None, *, monitors=monitor_rectangles,
                 clock=time.monotonic, sleep=time.sleep, timeout=2):
        self.api = api or HostApi()
        self.monitors, self.clock, self.sleep = monitors, clock, sleep
        self.timeout = timeout
        self.saved = None
        self.original_rect = self.original_visible = None
        self.layout = self.rect = self.size = None
        self.ready = False
        self.restoring = False

    def _layout(self):
        layout = tuple(sorted(tuple(r) for r in self.monitors()))
        if not layout or any(len(r) != 4 or any(type(x) is not int for x in r)
                             or r[2] <= r[0] or r[3] <= r[1] for r in layout):
            raise ValueError('Monitor geometry is unavailable')
        return layout

    def _guard(self):
        state, gui = self.saved, self.api.gui
        self.api.assert_owner(state.hwnd, state.identity)
        foreground = gui.GetForegroundWindow()
        # Include native dialogs owned by the game, not only the main HWND.
        visited = set()
        while foreground and foreground not in visited:
            if foreground == state.hwnd or gui.IsChild(state.hwnd, foreground):
                raise ValueError('Background surface yielded to manual game activation')
            visited.add(foreground)
            foreground = gui.GetWindow(gui.GetAncestor(foreground, 2), 4)

    def _write(self, callback, *args):
        self._guard()
        if not self.restoring and self._layout() != self.layout:
            raise ValueError('Monitor layout changed; background surface invalid')
        return callback(*args)

    def _wait(self, predicate, *, check_layout=True):
        deadline = self.clock() + self.timeout
        while True:
            self._guard()
            if check_layout and self._layout() != self.layout:
                raise ValueError('Monitor layout changed; background surface invalid')
            if predicate():
                return
            if self.clock() >= deadline:
                raise ValueError('Native surface change was not acknowledged')
            self.sleep(.025)

    def park(self, hwnd, identity, *, expected_size=None):
        """Park the existing client size. Retains recovery state on failure."""
        if self.saved:
            raise ValueError('A background surface is already reserved')
        gui = self.api.gui
        state = self.api.snapshot(hwnd, identity)
        self.saved = state
        try:
            self._guard()
            if gui.IsIconic(hwnd) or gui.GetWindowPlacement(hwnd)[1] == 3:
                raise ValueError('Background parking requires a restored game window')
            self.layout = self._layout()
            l, t, r, b = gui.GetClientRect(hwnd)
            self.size = (r-l, b-t)
            if min(self.size) <= 0 or (expected_size is not None and tuple(expected_size) != self.size):
                raise ValueError('Background viewport differs from qualified geometry')
            self.original_rect = tuple(gui.GetWindowRect(hwnd))
            self.original_visible = bool(gui.IsWindowVisible(hwnd))
        except BaseException:
            self.saved = None
            raise
        x, y = max(r[2] for r in self.layout)+64, min(r[1] for r in self.layout)
        self.rect = (x, y, x+self.size[0], y+self.size[1])
        # Unowned avoids implicit queue linkage to Tk and its other games.
        self._write(gui.SetWindowLong, hwnd, -8, 0)
        style = (state.style & ~FRAME_STYLES) | WS_POPUP
        exstyle = (state.exstyle & ~(0x40000 | 8)) | 0x80 | 0x08000000
        self._write(gui.SetWindowLong, hwnd, -16, ctypes.c_int32(style).value)
        self._write(gui.SetWindowLong, hwnd, -20, ctypes.c_int32(exstyle).value)
        self._write(gui.SetWindowPos, hwnd, -2, x, y, *self.size,
                    0x4000 | 0x10 | 0x200 | 0x20 | 0x40)
        self._wait(self._parked)
        self.ready = True
        return self.check()

    def _parked(self):
        gui, hwnd = self.api.gui, self.saved.hwnd
        l, t, r, b = gui.GetClientRect(hwnd)
        return (gui.GetWindow(hwnd, 4) == 0 and gui.IsWindowVisible(hwnd)
                and not gui.IsIconic(hwnd) and gui.GetWindowPlacement(hwnd)[1] != 3
                and tuple(gui.GetWindowRect(hwnd)) == self.rect
                and (r-l, b-t) == self.size)

    def check(self):
        """Call before each diagnostic message; does not certify rendering."""
        if not self.saved or not self.ready:
            raise ValueError('Background surface is not ready')
        self._guard()
        if self._layout() != self.layout:
            raise ValueError('Monitor layout changed; background surface invalid')
        if not self._parked():
            raise ValueError('Background surface geometry or ownership changed')
        return {'hwnd': self.saved.hwnd, 'identity': dict(self.saved.identity),
                'client_size': list(self.size), 'rect': list(self.rect),
                'monitor_rectangles': [list(r) for r in self.layout]}

    def restore(self):
        """Restore without activation; retain state if recovery cannot complete.

        If the user activates the target, yield instead of repositioning it.
        The caller can retry restoration after manual control has ended.
        """
        if not self.saved:
            return
        state, gui = self.saved, self.api.gui
        self.ready = False
        self._guard()
        self.restoring = True
        try:
            self._restore(state, gui)
        finally:
            self.restoring = False

    def _restore(self, state, gui):
        self._write(gui.SetWindowLong, state.hwnd, -16, ctypes.c_int32(state.style).value)
        self._write(gui.SetWindowLong, state.hwnd, -20, ctypes.c_int32(state.exstyle).value)
        self._write(gui.SetWindowLong, state.hwnd, -8, state.owner)
        placement = list(state.placement)
        # SW_SHOWNOACTIVATE restores normal show state without borrowing focus.
        # Preserve flags/min/max positions and normal workspace rectangle.
        placement[1] = 4 if self.original_visible else 0
        self._write(gui.SetWindowPlacement, state.hwnd, tuple(placement))
        l, t, r, b = self.original_rect
        flags = 0x4000 | 0x10 | 0x200 | 0x20
        after = -1 if state.exstyle & 8 else 0
        self._write(gui.SetWindowPos, state.hwnd, after, l, t, r-l, b-t,
                    flags if state.exstyle & 8 else flags | 0x4)
        def restored():
            current = gui.GetWindowPlacement(state.hwnd)
            hwnd = state.hwnd
            return (gui.GetWindow(hwnd, 4) == state.owner
                    and gui.GetWindowLong(hwnd, -16) & 0xffffffff == state.style
                    and gui.GetWindowLong(hwnd, -20) & 0xffffffff == state.exstyle
                    and tuple(gui.GetWindowRect(hwnd)) == self.original_rect
                    and bool(gui.IsWindowVisible(hwnd)) == self.original_visible
                    and tuple(current[:1]) == tuple(state.placement[:1])
                    and tuple(current[2:]) == tuple(state.placement[2:]))
        self._wait(restored, check_layout=False)
        self.saved = None
