"""Restore the selected client using window APIs; no game input or vision."""
import time
import pywintypes


def activate_client(hwnd, identity, *, api=None):
    from conquest.window_host import HostApi
    import win32api
    import win32process
    api = api or HostApi()
    gui = api.gui
    api.assert_owner(hwnd, identity)
    root = gui.GetAncestor(hwnd, 2)
    if gui.IsIconic(root):
        gui.ShowWindow(root, 9)
    if gui.GetForegroundWindow() == root:
        return True
    try:
        gui.SetForegroundWindow(root)
        if gui.GetForegroundWindow() == root:
            return True
    except pywintypes.error:
        pass
    # Windows can reject SetForegroundWindow from an unrelated input queue.
    # Temporarily share the current foreground queue, then always detach.
    foreground = gui.GetForegroundWindow()
    current = win32api.GetCurrentThreadId()
    other = win32process.GetWindowThreadProcessId(foreground)[0] if foreground else current
    attached = False
    try:
        if current != other:
            win32process.AttachThreadInput(current, other, True)
            attached = True
        api.assert_owner(hwnd, identity)
        gui.SetForegroundWindow(root)
    finally:
        if attached:
            win32process.AttachThreadInput(current, other, False)
    return gui.GetForegroundWindow() == root


class AutoRefocuser:
    def __init__(self, *, clock=time.monotonic):
        self.clock = clock
        self.lost_at = None
        self.next_attempt = 0

    def step(self, enabled, focused, activate):
        now = self.clock()
        if not enabled or focused:
            self.lost_at = None
            self.next_attempt = 0
            return None
        if self.lost_at is None:
            self.lost_at = now
        if now-self.lost_at < .5 or now < self.next_attempt:
            return None
        self.next_attempt = now+1
        try:
            return bool(activate())
        except (OSError, ValueError, pywintypes.error):
            return False
