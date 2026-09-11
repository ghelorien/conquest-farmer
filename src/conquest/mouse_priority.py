"""Yield automation to external pointer activity without game hooks or vision."""
import ctypes as c
from ctypes import wintypes as w
import threading
import time
from conquest.capture import CaptureUnavailable

MESSAGE = 'Mouse control is yours; farming resumes after 2 seconds idle'

class MousePriority:
    def __init__(self, sample, *, clock=time.monotonic, idle_seconds=2):
        self.sample, self.clock, self.idle_seconds = sample, clock, idle_seconds
        self.lock=threading.RLock()
        self.position=None
        self.owned_buttons=0
        self.owned_keys=set()
        self.until=0

    def active(self):
        with self.lock:
            position,buttons=self.sample()
            now=self.clock()
            if ((self.position is not None and position!=self.position)
                    or buttons & ~self.owned_buttons):
                self.until=now+self.idle_seconds
            self.position=position
            return now<self.until

    def require_idle(self):
        if self.active():raise CaptureUnavailable(MESSAGE)

    def send(self, callback, *, release=False, moving=False, down=0, up=0):
        with self.lock:
            if not release:self.require_idle()
            result=callback()
            if result:
                self.owned_buttons=(self.owned_buttons|down)&~up
                if moving:self.position=self.sample()[0]
            return result

_guard=None

def install():
    global _guard
    if _guard is None:
        user=c.windll.user32
        user.SetThreadDpiAwarenessContext.argtypes=[c.c_void_p]
        user.SetThreadDpiAwarenessContext.restype=c.c_void_p
        user.GetPhysicalCursorPos.argtypes=[c.POINTER(w.POINT)]
        user.GetPhysicalCursorPos.restype=w.BOOL
        user.GetAsyncKeyState.argtypes=[c.c_int]
        user.GetAsyncKeyState.restype=c.c_short
        def sample():
            previous=user.SetThreadDpiAwarenessContext(c.c_void_p(-4))
            try:
                point=w.POINT()
                if not user.GetPhysicalCursorPos(c.byref(point)):raise c.WinError()
                buttons=sum(bit for key,bit in ((1,1),(2,2),(4,4),(5,8),(6,16))
                            if user.GetAsyncKeyState(key)&0x8000)
                return (point.x,point.y),buttons
            finally:
                if previous:user.SetThreadDpiAwarenessContext(previous)
        _guard=MousePriority(sample)
        _guard.active()
    return _guard

def active():
    return bool(_guard and _guard.active())

def require_idle():
    if _guard:_guard.require_idle()

def guarded_send(send):
    def wrapped(count, pointer, size):
        if _guard is None:return send(count,pointer,size)
        from conquest.foreground import Input
        events=c.cast(pointer,c.POINTER(Input))
        moving=False;down=up=0;release=True
        key_down=set();key_up=set()
        for index in range(count):
            event=events[index]
            if event.type==0:
                flags=event.data.mi.dwFlags
                moving=moving or bool(flags&1)
                for d,u,bit in ((2,4,1),(8,16,2),(32,64,4)):
                    if flags&d:down|=bit
                    if flags&u:up|=bit
                release=release and bool(flags & (4|16|64)) and not bool(flags & (1|2|8|32))
            else:
                key=(event.data.ki.wVk,event.data.ki.wScan,event.data.ki.dwFlags&~2)
                (key_up if event.data.ki.dwFlags&2 else key_down).add(key)
                release=release and bool(event.data.ki.dwFlags&2)
        with _guard.lock:
            if release and not (up & _guard.owned_buttons or key_up & _guard.owned_keys):
                return count  # Never release a physical button/key we did not press.
            result=_guard.send(lambda:send(count,pointer,size),release=release,
                               moving=moving,down=down,up=up)
            if result==count:
                _guard.owned_keys=(_guard.owned_keys|key_down)-key_up
            return result
    return wrapped
