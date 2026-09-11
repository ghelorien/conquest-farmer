"""Host an external top-level HWND with reversible Win32 window ownership."""
from dataclasses import dataclass
import ctypes
from ctypes import wintypes

from conquest.win32 import WindowsBackend, bind

WS_CHILD = 0x40000000
WS_POPUP = 0x80000000
FRAME_STYLES = 0x00C00000 | 0x00040000 | 0x00080000 | 0x00030000 | 0x21000000


class GuiThreadInfo(ctypes.Structure):
    _fields_ = [('cbSize', wintypes.DWORD), ('flags', wintypes.DWORD),
                ('hwndActive', wintypes.HWND), ('hwndFocus', wintypes.HWND),
                ('hwndCapture', wintypes.HWND), ('hwndMenuOwner', wintypes.HWND),
                ('hwndMoveSize', wintypes.HWND), ('hwndCaret', wintypes.HWND),
                ('rcCaret', wintypes.RECT)]


@dataclass(frozen=True)
class WindowState:
    identity: dict
    hwnd: int
    style: int
    exstyle: int
    owner: int
    placement: tuple


class HostApi:
    def __init__(self):
        import win32gui
        self.gui = win32gui
        self.backend = WindowsBackend()
        user = self.backend.user
        self.get_dpi = bind(user, 'GetWindowDpiAwarenessContext', [wintypes.HWND], ctypes.c_void_p)
        self.same_dpi = bind(user, 'AreDpiAwarenessContextsEqual', [ctypes.c_void_p,ctypes.c_void_p], wintypes.BOOL)
        self.get_thread_info = bind(user, 'GetGUIThreadInfo', [wintypes.DWORD,ctypes.POINTER(GuiThreadInfo)], wintypes.BOOL)
        self.attach_input = bind(user, 'AttachThreadInput', [wintypes.DWORD,wintypes.DWORD,wintypes.BOOL], wintypes.BOOL)
        self.current_thread = bind(self.backend.kernel, 'GetCurrentThreadId', [], wintypes.DWORD)
        self.key_state = bind(user, 'GetAsyncKeyState', [ctypes.c_int], ctypes.c_short)

    def thread_info(self, hwnd):
        owner = wintypes.DWORD()
        thread = self.backend.window_pid(hwnd,ctypes.byref(owner))
        info = GuiThreadInfo(cbSize=ctypes.sizeof(GuiThreadInfo))
        if not thread or not self.get_thread_info(thread,ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        return thread, info

    def contains(self, parent, child):
        return bool(child) and (child == parent or self.gui.IsChild(parent,child))

    def focus(self, state):
        """Transfer keyboard focus only inside the already active wrapper."""
        self.assert_owner(state.hwnd,state.identity)
        root = self.gui.GetAncestor(state.hwnd,2)
        if self.gui.GetForegroundWindow()!=root:
            raise ValueError('Activate the wrapper before focusing Conquer')
        thread, info = self.thread_info(state.hwnd)
        # Preserve a native edit control that the client has already focused.
        if self.contains(state.hwnd,info.hwndFocus):
            return int(info.hwndFocus)
        current = self.current_thread()
        attached = False
        try:
            if current!=thread:
                if not self.attach_input(current,thread,True):
                    raise ctypes.WinError(ctypes.get_last_error())
                attached = True
            # Do not reactivate the app if the user switched away meanwhile.
            if self.gui.GetForegroundWindow()!=root:
                raise ValueError('The wrapper lost focus')
            self.gui.SetFocus(state.hwnd)
        finally:
            if attached and not self.attach_input(current,thread,False):
                raise ctypes.WinError(ctypes.get_last_error())
        _, info = self.thread_info(state.hwnd)
        if not self.contains(state.hwnd,info.hwndFocus):
            raise ValueError('Windows did not give the embedded client keyboard focus')
        return int(info.hwndFocus)

    def pointer_in_client(self, hwnd):
        if self.gui.GetForegroundWindow()!=self.gui.GetAncestor(hwnd,2):
            return False
        # WindowFromPoint also rejects an overlapping popup or sidebar control.
        return self.contains(hwnd,self.gui.WindowFromPoint(self.gui.GetCursorPos()))

    def embed_owned(self, state, parent):
        """Host a borderless top-level client, preserving native input activation."""
        self.assert_owner(state.hwnd,state.identity)
        owner = self.gui.GetAncestor(parent,2)
        self.gui.ShowWindow(state.hwnd,9)
        style = (state.style & ~(WS_CHILD | FRAME_STYLES)) | WS_POPUP
        self.gui.SetWindowLong(state.hwnd,-16,ctypes.c_int32(style).value)
        self.gui.SetWindowLong(state.hwnd,-20,ctypes.c_int32(state.exstyle & ~0x00040000).value)
        self.gui.SetWindowLong(state.hwnd,-8,owner)
        if self.gui.GetAncestor(state.hwnd,2)!=state.hwnd or self.gui.GetWindow(state.hwnd,4)!=owner:
            raise ValueError('Windows did not retain a top-level game owned by this wrapper')

    def resize_owned(self, state, parent, width, height):
        self.assert_owner(state.hwnd,state.identity)
        owner = self.gui.GetAncestor(parent,2)
        visible = (self.gui.IsWindowVisible(parent) and self.gui.IsWindowVisible(owner)
                   and not self.gui.IsIconic(owner) and min(width,height)>0)
        if not visible:
            if self.gui.IsWindowVisible(state.hwnd):
                self.gui.ShowWindow(state.hwnd,0)
            return
        x,y = self.gui.ClientToScreen(parent,(0,0))
        if self.gui.GetWindowRect(state.hwnd)!=(x,y,x+width,y+height):
            self.gui.SetWindowPos(state.hwnd,0,x,y,width,height,0x10 | 0x4 | 0x20)
        if not self.gui.IsWindowVisible(state.hwnd):
            self.gui.ShowWindow(state.hwnd,4)  # Show without taking keyboard focus.

    def assert_owner(self, hwnd, identity):
        if not self.owns_window(hwnd,identity):
            raise ValueError('The selected game window closed or changed process')

    def owns_window(self, hwnd, identity):
        if not self.gui.IsWindow(hwnd):
            return False
        owner = wintypes.DWORD()
        if not self.backend.window_pid(hwnd,ctypes.byref(owner)) or owner.value!=identity['pid']:
            return False
        try:
            return self.backend.identity(owner.value)==identity
        except OSError:
            if not self.gui.IsWindow(hwnd):
                return False
            raise  # Access failure must not be interpreted as a dead client.

    def snapshot(self, hwnd, identity):
        self.assert_owner(hwnd, identity)
        if self.gui.GetWindowLong(hwnd, -16) & WS_CHILD:
            raise ValueError('This client already belongs to another window')
        return WindowState(identity, hwnd, self.gui.GetWindowLong(hwnd,-16) & 0xffffffff,
            self.gui.GetWindowLong(hwnd,-20) & 0xffffffff,
            self.gui.GetWindow(hwnd, 4), self.gui.GetWindowPlacement(hwnd))

    def require_matching_dpi(self, child, parent):
        first, second = self.get_dpi(child), self.get_dpi(parent)
        if not first or not second or not self.same_dpi(first,second):
            raise ValueError('Host and game DPI modes differ; embedding was not attempted')

    def embed(self, state, parent):
        self.assert_owner(state.hwnd, state.identity)
        self.gui.ShowWindow(state.hwnd, 9)  # Restore before removing the desktop frame.
        self.gui.SetWindowLong(state.hwnd, -16,
            ((state.style & ~(WS_POPUP | FRAME_STYLES)) | WS_CHILD) & 0xffffffff)
        self.gui.SetWindowLong(state.hwnd, -20, ctypes.c_int32(state.exstyle & ~0x00040000).value)
        self.gui.SetParent(state.hwnd, parent)
        if self.gui.GetParent(state.hwnd) != parent:
            raise ValueError('Windows did not embed the game in the requested pane')

    def resize(self, hwnd, width, height):
        self.gui.SetWindowPos(hwnd, 0, 0, 0, width, height, 0x4000 | 0x10 | 0x4 | 0x20)

    def restore(self, state):
        self.assert_owner(state.hwnd, state.identity)
        self.gui.SetParent(state.hwnd, 0)
        self.gui.SetWindowLong(state.hwnd, -16, ctypes.c_int32(state.style).value)
        self.gui.SetWindowLong(state.hwnd, -20, ctypes.c_int32(state.exstyle).value)
        self.gui.SetWindowLong(state.hwnd, -8, state.owner)
        self.gui.SetWindowPlacement(state.hwnd, state.placement)
        self.gui.SetWindowPos(state.hwnd, 0, 0, 0, 0, 0, 0x27 | 0x4000)


class EmbeddedWindow:
    def __init__(self, api=None, *, mode='child'):
        if mode not in ('child','owned'):
            raise ValueError('Unknown client hosting mode')
        self.api = api or HostApi()
        self.mode = mode
        self.saved = None
        self.parent = None

    def attach(self, hwnd, identity, parent, width, height):
        if self.saved is not None:
            raise ValueError('Release the current game before embedding another client')
        state = self.api.snapshot(hwnd, identity)
        self.api.require_matching_dpi(hwnd, parent)
        self.saved, self.parent = state, parent
        try:
            if self.mode=='owned':
                self.api.embed_owned(state,parent)
            else:
                self.api.embed(state, parent)
            self.resize(width, height)
        except Exception as error:
            try:
                self.detach()
            except Exception as restore_error:
                raise RuntimeError(f'Embedding failed and the game could not be restored: {restore_error}') from error
            raise

    def resize(self, width, height):
        if self.saved is None or min(width,height) < 1:
            return
        self.api.assert_owner(self.saved.hwnd, self.saved.identity)
        if self.mode=='owned':
            self.api.resize_owned(self.saved,self.parent,int(width),int(height))
        else:
            self.api.resize(self.saved.hwnd, int(width), int(height))

    def detach(self):
        if self.saved is not None:
            if self.api.owns_window(self.saved.hwnd,self.saved.identity):
                try:
                    self.api.restore(self.saved)
                except Exception:
                    if self.api.owns_window(self.saved.hwnd,self.saved.identity):
                        raise
            self.saved = self.parent = None

    def is_alive(self):
        return self.saved is not None and self.api.owns_window(self.saved.hwnd,self.saved.identity)

    def focus(self):
        if self.saved is None:
            raise ValueError('No embedded client is open')
        return self.api.focus(self.saved)


def use_unaware_dpi():
    """Match this inspected client's DPI mode before constructing host HWNDs."""
    user = ctypes.WinDLL('user32', use_last_error=True)
    set_context = bind(user, 'SetProcessDpiAwarenessContext', [ctypes.c_void_p], wintypes.BOOL)
    if not set_context(ctypes.c_void_p(-1)) and ctypes.get_last_error() != 5:
        raise ctypes.WinError(ctypes.get_last_error())
