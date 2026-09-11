"""Local foreground runner with explicit logical-to-physical calibration."""
import ctypes
from contextlib import contextmanager

import cv2

from conquest.capture import DesktopFrames, Frame
from conquest.memory import MemorySession
from conquest.worker import Operations


@contextmanager
def physical_coordinates():
    user = ctypes.WinDLL('user32', use_last_error=True)
    setter = user.SetThreadDpiAwarenessContext
    setter.argtypes, setter.restype = [ctypes.c_void_p], ctypes.c_void_p
    previous = setter(ctypes.c_void_p(-4))
    if not previous:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        yield
    finally:
        setter(previous)


class LocalSession:
    def __init__(self, pid, hwnd, sha256, logical_size, physical_size):
        self.memory = MemorySession(pid, sha256).__enter__()
        self.operations = Operations(self.memory, hwnd, read_only=False)
        self.expected_sha256 = sha256
        self.modules, self.identity = self.memory.modules, self.memory.identity
        self.logical_size, self.physical_size = tuple(logical_size), tuple(physical_size)
        self.read = self.read_block = self.memory.read
        self.assert_identity = self.memory.assert_identity

    def request(self, operation, body=None):
        body = dict(body or {})
        if operation.startswith('foreground-'):
            if tuple(body.get('expected_size', ())) != self.logical_size:
                raise ValueError('Input calibration differs from the selected profile')
            body['expected_size'] = list(self.physical_size)
            for name in ('point', 'start', 'end'):
                if name in body:
                    body[name] = [round(value * actual / logical)
                        for value, actual, logical in zip(body[name], self.physical_size, self.logical_size)]
        return self.operations.dispatch(operation, body)

    def close(self):
        self.memory.close()


class NormalizedFrames:
    def __init__(self, hwnd, logical_size, output_idx, output_origin, *, physical_size):
        self.logical_size = tuple(logical_size)
        self.camera = DesktopFrames(hwnd, physical_size, output_idx, output_origin)

    def geometry(self):
        return self.camera.geometry()

    def read(self):
        frame = self.camera.read()
        normalized = cv2.resize(frame.image, self.logical_size, interpolation=cv2.INTER_AREA)
        return Frame(frame.timestamp, normalized, frame.origin)

    def close(self):
        self.camera.close()


class WindowGeometry:
    """Foreground input coordinates only. Never creates a camera or reads pixels."""
    def __init__(self, hwnd, physical_size):
        import win32gui
        self.gui,self.hwnd,self.size=win32gui,hwnd,tuple(physical_size)

    def geometry(self):
        from conquest.capture import CaptureUnavailable
        g,h=self.gui,self.hwnd
        if not g.IsWindow(h):
            raise ValueError('Game window disappeared')
        if g.IsIconic(h) or g.GetForegroundWindow()!=g.GetAncestor(h,2):
            raise CaptureUnavailable('Waiting for game focus')
        if tuple(g.GetClientRect(h)[2:])!=self.size:
            raise ValueError('Game client geometry changed')
        return g.ClientToScreen(h,(0,0))

    def close(self):
        pass
