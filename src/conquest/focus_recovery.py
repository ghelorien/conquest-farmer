"""Restore the selected client using window APIs; no game input or vision."""

import time
import pywintypes


from conquest.merchants.coordination import coordinated_input


def settled_foreground(api, hwnd, identity, root, seconds=0.2):
    """Allow a queued window activation to settle without sending game input."""
    from conquest.merchants.coordination import check_input

    deadline = time.monotonic() + seconds
    while True:
        check_input()
        api.assert_owner(hwnd, identity)
        if api.gui.GetForegroundWindow() == root:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.02)


@coordinated_input
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
    if settled_foreground(api, hwnd, identity, root):
        return True
    # Windows can reject SetForegroundWindow from an unrelated input queue.
    # Temporarily share the current foreground queue, then always detach.
    foreground = gui.GetForegroundWindow()
    current = win32api.GetCurrentThreadId()
    other = (
        win32process.GetWindowThreadProcessId(foreground)[0] if foreground else current
    )
    attached = False
    try:
        if current != other:
            try:
                win32process.AttachThreadInput(current, other, True)
            except pywintypes.error as error:
                code = getattr(error, "winerror", None)
                if code is None and error.args:
                    code = error.args[0]
                if code != 5:  # Access denied: try only a qualified client caption.
                    raise
            else:
                attached = True
        if current == other or attached:
            api.assert_owner(hwnd, identity)
            try:
                gui.SetForegroundWindow(root)
            except pywintypes.error:
                # A denied foreground request often carries Windows error 0.
                # This is an ordinary focus denial, not a client or permission failure.
                pass
    finally:
        if attached:
            win32process.AttachThreadInput(current, other, False)
    if gui.GetForegroundWindow() == root:
        return True
    fallback = getattr(api, "activate_owned_caption", None)
    native_fallback = getattr(api, "activate_native_caption", None)
    if (fallback and fallback(hwnd, identity)) or (
        native_fallback and native_fallback(hwnd, identity)
    ):
        api.assert_owner(hwnd, identity)
        if gui.GetForegroundWindow() == root:
            return True
        try:
            gui.SetForegroundWindow(root)
        except pywintypes.error:
            pass
    return settled_foreground(api, hwnd, identity, root)


@coordinated_input
def activate_focused_client(hwnd, identity, *, api=None, focus=None):
    """Activate an exact client and, when hosted, prove its keyboard focus."""
    from conquest.merchants.coordination import check_input
    from conquest.window_host import HostApi

    api = api or HostApi()
    api.assert_owner(hwnd, identity)
    if not activate_client(hwnd, identity, api=api):
        return False
    # Activation may have waited or used the owner's verified caption. A Stop,
    # pointer takeover or replaced client must win before keyboard focus.
    check_input()
    api.assert_owner(hwnd, identity)
    root = api.gui.GetAncestor(hwnd, 2)
    if api.gui.GetForegroundWindow() != root:
        return False
    focused = True if focus is None else focus()
    check_input()
    api.assert_owner(hwnd, identity)
    if api.gui.GetForegroundWindow() != root:
        return False
    return focused


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
        if now - self.lost_at < 0.5 or now < self.next_attempt:
            return None
        self.next_attempt = now + 1
        try:
            return bool(activate())
        except (OSError, ValueError, pywintypes.error):
            return False
