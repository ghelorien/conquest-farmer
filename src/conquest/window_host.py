"""Host an external top-level HWND with reversible Win32 window ownership."""

from dataclasses import dataclass
import ctypes
from ctypes import wintypes

from conquest.win32 import WindowsBackend, bind

WS_CHILD = 0x40000000
WS_POPUP = 0x80000000
FRAME_STYLES = 0x00C00000 | 0x00040000 | 0x00080000 | 0x00030000 | 0x21000000


class GuiThreadInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("hwndActive", wintypes.HWND),
        ("hwndFocus", wintypes.HWND),
        ("hwndCapture", wintypes.HWND),
        ("hwndMenuOwner", wintypes.HWND),
        ("hwndMoveSize", wintypes.HWND),
        ("hwndCaret", wintypes.HWND),
        ("rcCaret", wintypes.RECT),
    ]


@dataclass(frozen=True)
class WindowState:
    identity: dict
    hwnd: int
    style: int
    exstyle: int
    owner: int
    placement: tuple


class HostApi:
    def activate_owned_caption(self, hwnd, identity):
        from conquest.caption_focus import activate_owner_caption

        return activate_owner_caption(hwnd, identity, self)

    def activate_native_caption(self, hwnd, identity):
        from conquest.caption_focus import activate_native_caption

        return activate_native_caption(hwnd, identity, self)

    def __init__(self):
        import win32gui

        self.gui = win32gui
        self.backend = WindowsBackend()
        user = self.backend.user
        self.get_dpi = bind(
            user, "GetWindowDpiAwarenessContext", [wintypes.HWND], ctypes.c_void_p
        )
        self.same_dpi = bind(
            user,
            "AreDpiAwarenessContextsEqual",
            [ctypes.c_void_p, ctypes.c_void_p],
            wintypes.BOOL,
        )
        self.get_thread_info = bind(
            user,
            "GetGUIThreadInfo",
            [wintypes.DWORD, ctypes.POINTER(GuiThreadInfo)],
            wintypes.BOOL,
        )
        self.attach_input = bind(
            user,
            "AttachThreadInput",
            [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL],
            wintypes.BOOL,
        )
        self.current_thread = bind(
            self.backend.kernel, "GetCurrentThreadId", [], wintypes.DWORD
        )
        self.key_state = bind(user, "GetAsyncKeyState", [ctypes.c_int], ctypes.c_short)
        self.show_async = bind(
            user, "ShowWindowAsync", [wintypes.HWND, ctypes.c_int], wintypes.BOOL
        )

    def thread_info(self, hwnd):
        owner = wintypes.DWORD()
        thread = self.backend.window_pid(hwnd, ctypes.byref(owner))
        info = GuiThreadInfo(cbSize=ctypes.sizeof(GuiThreadInfo))
        if not thread or not self.get_thread_info(thread, ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        return thread, info

    def contains(self, parent, child):
        return bool(child) and (child == parent or self.gui.IsChild(parent, child))

    def focus(self, state):
        """Transfer keyboard focus only inside the already active wrapper."""
        self.assert_owner(state.hwnd, state.identity)
        root = self.gui.GetAncestor(state.hwnd, 2)
        if self.gui.GetForegroundWindow() != root:
            raise ValueError("Activate the wrapper before focusing Conquer")
        thread, info = self.thread_info(state.hwnd)
        # Preserve a native edit control that the client has already focused.
        if self.contains(state.hwnd, info.hwndFocus):
            return int(info.hwndFocus)
        current = self.current_thread()
        attached = False
        try:
            if current != thread:
                if not self.attach_input(current, thread, True):
                    raise ctypes.WinError(ctypes.get_last_error())
                attached = True
            # Do not reactivate the app if the user switched away meanwhile.
            if self.gui.GetForegroundWindow() != root:
                raise ValueError("The wrapper lost focus")
            self.gui.SetFocus(state.hwnd)
        finally:
            if attached and not self.attach_input(current, thread, False):
                raise ctypes.WinError(ctypes.get_last_error())
        _, info = self.thread_info(state.hwnd)
        if not self.contains(state.hwnd, info.hwndFocus):
            raise ValueError("Windows did not give the embedded client keyboard focus")
        return int(info.hwndFocus)

    def pointer_in_client(self, hwnd):
        if self.gui.GetForegroundWindow() != self.gui.GetAncestor(hwnd, 2):
            return False
        # WindowFromPoint also rejects an overlapping popup or sidebar control.
        return self.contains(hwnd, self.gui.WindowFromPoint(self.gui.GetCursorPos()))

    def activate_owned_click(self, state, parent):
        """Complete a user's click from the active wrapper into its owned game."""
        self.assert_owner(state.hwnd, state.identity)
        owner = self.gui.GetAncestor(parent, 2)
        if (
            self.gui.GetWindow(state.hwnd, 4) != owner
            or self.gui.GetForegroundWindow() not in (owner, state.hwnd)
            or not self.gui.IsWindowVisible(state.hwnd)
            or not self.contains(
                state.hwnd, self.gui.WindowFromPoint(self.gui.GetCursorPos())
            )
        ):
            return False
        if self.gui.GetForegroundWindow() != state.hwnd:
            self.gui.SetForegroundWindow(state.hwnd)
        if self.gui.GetForegroundWindow() != state.hwnd:
            return False
        self.focus(state)
        return True

    def embed_owned(self, state, parent):
        """Host a borderless top-level client, preserving native input activation."""
        self.assert_owner(state.hwnd, state.identity)
        owner = self.gui.GetAncestor(parent, 2)
        if self.gui.IsIconic(state.hwnd):
            self.show_async(state.hwnd, 9)
        style = (state.style & ~(WS_CHILD | FRAME_STYLES)) | WS_POPUP
        self.gui.SetWindowLong(state.hwnd, -16, ctypes.c_int32(style).value)
        self.gui.SetWindowLong(
            state.hwnd, -20, ctypes.c_int32(state.exstyle & ~0x00040000).value
        )
        self.gui.SetWindowLong(state.hwnd, -8, owner)
        if (
            self.gui.GetAncestor(state.hwnd, 2) != state.hwnd
            or self.gui.GetWindow(state.hwnd, 4) != owner
        ):
            raise ValueError(
                "Windows did not retain a top-level game owned by this wrapper"
            )

    def resize_owned(self, state, parent, width, height):
        self.assert_owner(state.hwnd, state.identity)
        owner = self.gui.GetAncestor(parent, 2)
        visible = (
            self.gui.IsWindowVisible(parent)
            and self.gui.IsWindowVisible(owner)
            and not self.gui.IsIconic(owner)
            and min(width, height) > 0
        )
        if not visible:
            if self.gui.IsWindowVisible(state.hwnd):
                self.show_async(state.hwnd, 0)
            return
        x, y = self.gui.ClientToScreen(parent, (0, 0))
        factor = getattr(self, "height_scale", 1.0)
        # An owned top-level window is above the whole wrapper and cannot be
        # clipped by the Tk pane. Unified mode therefore keeps it inside the
        # pane; otherwise upward expansion covers the shared header and tabs.
        if 1 < factor <= 1.15 and not getattr(self, "constrain_owned_to_parent", False):
            import win32api

            work = win32api.GetMonitorInfo(win32api.MonitorFromWindow(state.hwnd))[
                "Work"
            ]
            taller = min(round(height * factor), work[3] - work[1])
            y = max(work[1], y + height - taller)
            height = taller
        if self.gui.GetWindowRect(state.hwnd) != (x, y, x + width, y + height):
            self.gui.SetWindowPos(
                state.hwnd, 0, x, y, width, height, 0x4000 | 0x10 | 0x4 | 0x20
            )
        if not self.gui.IsWindowVisible(state.hwnd):
            self.show_async(
                state.hwnd, 4
            )  # Do not wait for the client's render thread or activate it.
        self.ensure_above_owner(state.hwnd, owner)

    def is_above(self, hwnd, other):
        current = other
        for _ in range(2048):
            current = self.gui.GetWindow(
                current, 3
            )  # GW_HWNDPREV: toward the top of the desktop Z order.
            if not current:
                return False
            if current == hwnd:
                return True
        return False  # A changing desktop must not trap the UI in a traversal.

    def ensure_above_owner(self, hwnd, owner):
        if self.is_above(hwnd, owner):
            return
        previous = self.gui.GetWindow(owner, 3)
        # Insert immediately above our wrapper, preserving all unrelated apps
        # that are already above it. Crossing a topmost-band boundary must not
        # accidentally make a normal merchant permanently topmost.
        owner_topmost = bool(self.gui.GetWindowLong(owner, -20) & 8)
        if (
            previous
            and bool(self.gui.GetWindowLong(previous, -20) & 8) != owner_topmost
        ):
            previous = -1 if owner_topmost else 0
        self.gui.SetWindowPos(
            hwnd, previous or 0, 0, 0, 0, 0, 0x4000 | 0x200 | 0x10 | 0x1 | 0x2
        )  # async, no owner reorder/activation/size/move

    def assert_owner(self, hwnd, identity):
        if not self.owns_window(hwnd, identity):
            raise ValueError("The selected game window closed or changed process")

    def owns_window(self, hwnd, identity):
        if not self.gui.IsWindow(hwnd):
            return False
        owner = wintypes.DWORD()
        if (
            not self.backend.window_pid(hwnd, ctypes.byref(owner))
            or owner.value != identity["pid"]
        ):
            return False
        try:
            return self.backend.identity(owner.value) == identity
        except OSError:
            if not self.gui.IsWindow(hwnd):
                return False
            raise  # Access failure must not be interpreted as a dead client.

    def snapshot(self, hwnd, identity):
        self.assert_owner(hwnd, identity)
        if self.gui.GetWindowLong(hwnd, -16) & WS_CHILD:
            raise ValueError("This client already belongs to another window")
        return WindowState(
            identity,
            hwnd,
            self.gui.GetWindowLong(hwnd, -16) & 0xFFFFFFFF,
            self.gui.GetWindowLong(hwnd, -20) & 0xFFFFFFFF,
            self.gui.GetWindow(hwnd, 4),
            self.gui.GetWindowPlacement(hwnd),
        )

    def require_matching_dpi(self, child, parent):
        first, second = self.get_dpi(child), self.get_dpi(parent)
        if not first or not second or not self.same_dpi(first, second):
            raise ValueError(
                "Host and game DPI modes differ; embedding was not attempted"
            )

    def embed(self, state, parent):
        self.assert_owner(state.hwnd, state.identity)
        self.gui.ShowWindow(state.hwnd, 9)  # Restore before removing the desktop frame.
        self.gui.SetWindowLong(
            state.hwnd,
            -16,
            ((state.style & ~(WS_POPUP | FRAME_STYLES)) | WS_CHILD) & 0xFFFFFFFF,
        )
        self.gui.SetWindowLong(
            state.hwnd, -20, ctypes.c_int32(state.exstyle & ~0x00040000).value
        )
        self.gui.SetParent(state.hwnd, parent)
        if self.gui.GetParent(state.hwnd) != parent:
            raise ValueError("Windows did not embed the game in the requested pane")

    def resize(self, hwnd, width, height):
        self.gui.SetWindowPos(hwnd, 0, 0, 0, width, height, 0x4000 | 0x10 | 0x4 | 0x20)

    def restore(self, state):
        self.assert_owner(state.hwnd, state.identity)
        # Owned top-level hosting never reparents the game. Avoid a needless
        # cross-process SetParent call and its possible DPI-context reset.
        if self.gui.GetWindowLong(state.hwnd, -16) & WS_CHILD:
            self.gui.SetParent(state.hwnd, 0)
        self.gui.SetWindowLong(state.hwnd, -16, ctypes.c_int32(state.style).value)
        self.gui.SetWindowLong(state.hwnd, -20, ctypes.c_int32(state.exstyle).value)
        self.gui.SetWindowLong(state.hwnd, -8, state.owner)
        self.gui.SetWindowPlacement(state.hwnd, state.placement)
        self.gui.SetWindowPos(state.hwnd, 0, 0, 0, 0, 0, 0x27 | 0x4000)


class EmbeddedWindow:
    def __init__(self, api=None, *, mode="child"):
        if mode not in ("child", "owned"):
            raise ValueError("Unknown client hosting mode")
        self.api = api or HostApi()
        self.mode = mode
        self.saved = None
        self.parent = None

    def attach(self, hwnd, identity, parent, width, height):
        if self.saved is not None:
            raise ValueError("Release the current game before embedding another client")
        state = self.api.snapshot(hwnd, identity)
        self.api.require_matching_dpi(hwnd, parent)
        self.saved, self.parent = state, parent
        try:
            if self.mode == "owned":
                self.api.embed_owned(state, parent)
            else:
                self.api.embed(state, parent)
            self.resize(width, height)
        except Exception as error:
            try:
                self.detach()
            except Exception as restore_error:
                raise RuntimeError(
                    f"Embedding failed and the game could not be restored: {restore_error}"
                ) from error
            raise

    def resize(self, width, height):
        if self.saved is None or min(width, height) < 1:
            return
        self.api.assert_owner(self.saved.hwnd, self.saved.identity)
        if self.mode == "owned":
            self.api.resize_owned(self.saved, self.parent, int(width), int(height))
        else:
            self.api.resize(self.saved.hwnd, int(width), int(height))

    def detach(self):
        if self.saved is not None:
            if self.api.owns_window(self.saved.hwnd, self.saved.identity):
                try:
                    self.api.restore(self.saved)
                except Exception:
                    if self.api.owns_window(self.saved.hwnd, self.saved.identity):
                        raise
            self.saved = self.parent = None

    def is_alive(self):
        return self.saved is not None and self.api.owns_window(
            self.saved.hwnd, self.saved.identity
        )

    def focus(self):
        if self.saved is None:
            raise ValueError("No embedded client is open")
        return self.api.focus(self.saved)


def use_unaware_dpi():
    """Match this inspected client's DPI mode before constructing host HWNDs."""
    user = ctypes.WinDLL("user32", use_last_error=True)
    set_context = bind(
        user, "SetProcessDpiAwarenessContext", [ctypes.c_void_p], wintypes.BOOL
    )
    if not set_context(ctypes.c_void_p(-1)) and ctypes.get_last_error() != 5:
        raise ctypes.WinError(ctypes.get_last_error())
