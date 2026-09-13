"""Foreground-only SendInput diagnostics. Never used as background input."""

import ctypes as c
import time
from ctypes import wintypes as w

from conquest.win32 import bind
from conquest.capture import CaptureUnavailable
from conquest.mouse_priority import require_idle, guarded_send
from conquest.merchants.coordination import coordinated_input


class MouseInput(c.Structure):
    _fields_ = [("dx", w.LONG), ("dy", w.LONG), ("mouseData", w.DWORD),
                ("dwFlags", w.DWORD), ("time", w.DWORD), ("dwExtraInfo", c.c_size_t)]


class KeyboardInput(c.Structure):
    _fields_ = [("wVk", w.WORD), ("wScan", w.WORD), ("dwFlags", w.DWORD),
                ("time", w.DWORD), ("dwExtraInfo", c.c_size_t)]


class InputUnion(c.Union):
    _fields_ = [("mi", MouseInput), ("ki", KeyboardInput)]


class Input(c.Structure):
    _fields_ = [("type", w.DWORD), ("data", InputUnion)]


def scan_key_event(scan_code, released=False):
    if type(scan_code) is not int or not 1 <= scan_code <= 0x7F:
        raise ValueError("Invalid non-extended keyboard scan code")
    return Input(type=1, data=InputUnion(ki=KeyboardInput(0, scan_code, 0x0008 | (2 if released else 0), 0, 0)))


def press_scan_sequence(send, scans, sleep=time.sleep):
    """Release every attempted key in reverse order, including partial failures."""
    attempted = []
    try:
        for scan in scans:
            attempted.append(scan)
            send(scan_key_event(scan))
            sleep(0.05)
        sleep(0.15)
    finally:
        first_error = None
        for scan in reversed(attempted):
            try:
                send(scan_key_event(scan, True))
            except OSError as error:
                first_error = first_error or error
        if first_error is not None:
            raise first_error


@coordinated_input
def foreground_key(target, vk, expected_size, control=False, require_foreground=False):
    """One F1-F11 or Escape input; F12 remains reserved for emergency stop."""
    if type(vk) is not int or not (0x70 <= vk <= 0x7A or vk == 0x1B):
        raise ValueError("Key diagnostic supports F1 through F11 and Escape only")
    if type(control) is not bool:
        raise ValueError("control must be a boolean")
    require_idle()
    before = target.snapshot()
    if require_foreground and before["foreground"] != target.hwnd:
        raise CaptureUnavailable("Game lost focus; no key sent")
    if before["minimized"] or before["client_size"] != list(expected_size):
        raise CaptureUnavailable("Window state differs from the foreground calibration; no input sent")
    activate = bind(target.backend.user, "SetForegroundWindow", [w.HWND], w.BOOL)
    send = bind(target.backend.user, "SendInput", [w.UINT, c.POINTER(Input), c.c_int], w.UINT)
    send = guarded_send(send)
    map_key = bind(target.backend.user, "MapVirtualKeyW", [w.UINT, w.UINT], w.UINT)
    scan_code = map_key(vk, 0)
    scan_key_event(scan_code)  # Validate before activating or sending anything.
    scans = ([map_key(0x11, 0)] if control else []) + [scan_code]
    for code in scans:
        scan_key_event(code)
    key_state = bind(target.backend.user, "GetAsyncKeyState", [c.c_int], c.c_short)
    activate(target.hwnd)
    time.sleep(0.15)
    if target.backend.foreground() != target.hwnd:
        raise CaptureUnavailable("Game did not receive focus; no input sent")
    if any(key_state(code) & 0x8000 for code in (0x10, 0x11, 0x12, vk)):
        raise CaptureUnavailable("A physical modifier or target key is held; no input sent")

    def send_event(event):
        if send(1, c.byref(event), c.sizeof(event)) != 1:
            raise target.backend.error("SendInput(keyboard)")

    press_scan_sequence(send_event, scans)
    time.sleep(0.25)
    return {"mode": "foreground_key_diagnostic", "vk": vk, "control": control,
            "scan_code": scan_code, "encoding": "scan_code",
            "before": before, "after": target.snapshot(), "qualified_for_background": False}


def require_click_position(snapshot, hwnd, expected_size, expected_point):
    if snapshot["client_size"] != list(expected_size):
        raise CaptureUnavailable("Window changed before click; no button pressed")
    if snapshot["foreground"] != hwnd or snapshot["minimized"]:
        raise CaptureUnavailable("Game lost focus before click; no button pressed")
    if any(abs(actual - expected) > 2 for actual, expected in zip(snapshot["cursor"], expected_point)):
        raise CaptureUnavailable("Cursor moved away from the target; no button pressed")


@coordinated_input
def foreground_click(target, x, y, expected_size, button="left", control=False,
                     require_foreground=False, expected_origin=None,diagnostics=None,
                     before_press=None,layout_guard=None):
    if button not in ("left", "right"):
        raise ValueError("Unsupported mouse button")
    if type(control) is not bool:
        raise ValueError("control must be a boolean")
    require_idle()
    before = target.snapshot()
    foreground_hwnd = before.get('root_hwnd',target.hwnd)
    if require_foreground and before["foreground"] != foreground_hwnd:
        raise CaptureUnavailable("Game lost focus; no input sent")
    if before["minimized"] or before["client_size"] != list(expected_size):
        raise CaptureUnavailable("Window state differs from the foreground calibration; no input sent")
    if not 0 <= x < expected_size[0] or not 0 <= y < expected_size[1]:
        raise ValueError("Point is outside the game client")
    user = target.backend.user
    activate = bind(user, "SetForegroundWindow", [w.HWND], w.BOOL)
    to_screen = bind(user, "ClientToScreen", [w.HWND, c.POINTER(w.POINT)], w.BOOL)
    metrics = bind(user, "GetSystemMetrics", [c.c_int], c.c_int)
    send = bind(user, "SendInput", [w.UINT, c.POINTER(Input), c.c_int], w.UINT)
    send = guarded_send(send)
    if before['foreground']!=foreground_hwnd:
        activate(foreground_hwnd)
        time.sleep(0.15)
    if target.backend.foreground() != foreground_hwnd:
        raise CaptureUnavailable("Game did not receive focus; no input sent")
    point = w.POINT(x, y)
    if not to_screen(target.hwnd, c.byref(point)):
        raise target.backend.error("ClientToScreen")
    origin=w.POINT(0,0)
    if not to_screen(target.hwnd,c.byref(origin)):
        raise target.backend.error('ClientToScreen(origin)')
    if expected_origin is not None and any(abs(a-b)>1 for a,b in zip((origin.x,origin.y),expected_origin)):
        raise CaptureUnavailable(f"Game moved since observation; expected {expected_origin}, actual {[origin.x,origin.y]}; no input sent")
    left, top, width, height = (metrics(index) for index in (76, 77, 78, 79))
    if width <= 1 or height <= 1:
        raise ValueError("Invalid virtual desktop dimensions")

    def mouse(flags, dx=0, dy=0):
        event = Input(type=0, data=InputUnion(mi=MouseInput(dx, dy, 0, flags, 0, 0)))
        if send(1, c.byref(event), c.sizeof(event)) != 1:
            raise target.backend.error("SendInput")

    mouse(0x8000 | 0x4000 | 0x0001,
          round((point.x - left) * 65535 / (width - 1)),
          round((point.y - top) * 65535 / (height - 1)))
    time.sleep(0.03 if require_foreground else 0.15)
    require_click_position(target.snapshot(), foreground_hwnd, expected_size, (point.x, point.y))
    refreshed = w.POINT(x, y)
    if not to_screen(target.hwnd, c.byref(refreshed)) or (refreshed.x, refreshed.y) != (point.x, point.y):
        raise CaptureUnavailable("Game moved before click; no button pressed")
    down, up = (0x0002, 0x0004) if button == "left" else (0x0008, 0x0010)
    control_attempted = False
    def control_key(released):
        event = scan_key_event(0x1D, released)  # Left Control on the inspected Windows client.
        if send(1, c.byref(event), c.sizeof(event)) != 1:
            raise target.backend.error("SendInput(Control)")
    try:
        if control:
            key_state = bind(user, "GetAsyncKeyState", [c.c_int], c.c_short)
            if key_state(0x11) & 0x8000:
                raise CaptureUnavailable("Physical Control key is held; no click sent")
            control_attempted = True
            control_key(False)
            time.sleep(0.05)
            from conquest.window_host import HostApi
            _, keyboard = HostApi().thread_info(target.hwnd)
            modifier={'control':bool(key_state(0x11)&0x8000),
                'left_control':bool(key_state(0xA2)&0x8000),
                'game_focus_hwnd':int(keyboard.hwndFocus or 0),
                'game_active_hwnd':int(keyboard.hwndActive or 0)}
            if diagnostics is not None:
                diagnostics['modifier_before_click']=modifier
            if not modifier['control']:
                raise CaptureUnavailable('Windows did not register Control as held; no jump click sent')
        try:
            require_click_position(target.snapshot(), foreground_hwnd, expected_size, (point.x, point.y))
            key_state = bind(user, "GetAsyncKeyState", [c.c_int], c.c_short)
            if key_state(0x7B) & 0x8000:
                raise ValueError("Emergency stop before click")
            if layout_guard:
                layout_guard()
                require_click_position(target.snapshot(),foreground_hwnd,expected_size,(point.x,point.y))
            if before_press:
                before_press()
                require_click_position(target.snapshot(),foreground_hwnd,expected_size,(point.x,point.y))
            mouse(down)
            time.sleep(0.04 if require_foreground else 0.1)
        finally:
            mouse(up)
    finally:
        if control_attempted:
            control_key(True)
    time.sleep(0.02 if require_foreground else 0.2)
    return {"mode": "foreground_diagnostic", "before": before, "after": target.snapshot(),
            "point": [x, y], "button": button, "control": control, "qualified_for_background": False}


@coordinated_input
def foreground_drag(target, source, destination, expected_size,*,before_press=None,
                    layout_guard=None,before_release=None):
    """One bounded client-local drag for explicit shortcut calibration."""
    require_idle()
    state = target.snapshot()
    if state["foreground"] != target.hwnd or state["minimized"] or state["client_size"] != list(expected_size):
        raise ValueError("Game must have focus at calibrated size before dragging")
    for point in (source, destination):
        if len(point) != 2 or any(type(v) is not int for v in point) or not (0 <= point[0] < expected_size[0] and 0 <= point[1] < expected_size[1]):
            raise ValueError("Drag endpoint outside client")
    user = target.backend.user
    to_screen = bind(user, "ClientToScreen", [w.HWND, c.POINTER(w.POINT)], w.BOOL)
    metrics = bind(user, "GetSystemMetrics", [c.c_int], c.c_int)
    send = bind(user, "SendInput", [w.UINT, c.POINTER(Input), c.c_int], w.UINT)
    send = guarded_send(send)
    a, b = w.POINT(*source), w.POINT(*destination)
    if not to_screen(target.hwnd, c.byref(a)) or not to_screen(target.hwnd, c.byref(b)):
        raise target.backend.error("ClientToScreen")
    left,top,width,height=(metrics(i) for i in (76,77,78,79))
    if min(width,height) <= 1:
        raise ValueError("Invalid virtual desktop")
    def mouse(flags, x=0, y=0):
        event=Input(type=0,data=InputUnion(mi=MouseInput(x,y,0,flags,0,0)))
        if send(1,c.byref(event),c.sizeof(event)) != 1:
            raise target.backend.error("SendInput(drag)")
    def move(x,y):
        if target.backend.foreground() != target.hwnd:
            raise ValueError("Focus changed during drag")
        mouse(0xC001,round((x-left)*65535/(width-1)),round((y-top)*65535/(height-1)))
    move(a.x,a.y)
    time.sleep(.1)
    require_click_position(target.snapshot(),target.hwnd,expected_size,(a.x,a.y))
    try:
        if layout_guard:
            layout_guard()
            require_click_position(target.snapshot(),target.hwnd,expected_size,(a.x,a.y))
        if before_press:
            before_press()
            require_click_position(target.snapshot(),target.hwnd,expected_size,(a.x,a.y))
        mouse(0x2)
        time.sleep(.15)
        for step in range(1,9):
            if layout_guard:layout_guard()
            move(round(a.x+(b.x-a.x)*step/8),round(a.y+(b.y-a.y)*step/8))
            time.sleep(.04)
        require_click_position(target.snapshot(),target.hwnd,expected_size,(b.x,b.y))
        if layout_guard:layout_guard()
        if before_release:before_release()
        require_click_position(target.snapshot(),target.hwnd,expected_size,(b.x,b.y))
        time.sleep(.1)
    finally:
        mouse(0x4)
    return {"mode":"foreground_drag_calibration","source":source,"destination":destination,"after":target.snapshot()}


@coordinated_input
def foreground_scroll(target, point, ticks, expected_size=(1036,793), *, layout_guard=None):
    """Bounded wheel input over a memory-qualified shop grid."""
    require_idle()
    if type(ticks) is not int or not 1<=abs(ticks)<=3:raise ValueError('Invalid wheel step')
    before=target.snapshot();hwnd=before.get('root_hwnd',target.hwnd)
    if before['foreground']!=hwnd or before['minimized'] or before['client_size']!=list(expected_size):
        raise CaptureUnavailable('Shop scrolling needs focused client; no input sent')
    if not(0<=point[0]<expected_size[0] and 0<=point[1]<expected_size[1]):raise ValueError('Scroll point outside client')
    user=target.backend.user
    screen=w.POINT(*point)
    to_screen=bind(user,'ClientToScreen',[w.HWND,c.POINTER(w.POINT)],w.BOOL)
    if not to_screen(target.hwnd,c.byref(screen)):raise target.backend.error('ClientToScreen')
    metrics=bind(user,'GetSystemMetrics',[c.c_int],c.c_int)
    left,top,width,height=(metrics(i) for i in (76,77,78,79))
    if min(width,height)<=1:raise ValueError('Invalid desktop dimensions')
    send=guarded_send(bind(user,'SendInput',[w.UINT,c.POINTER(Input),c.c_int],w.UINT))
    if layout_guard:layout_guard()
    move=Input(type=0,data=InputUnion(mi=MouseInput(round((screen.x-left)*65535/(width-1)),
        round((screen.y-top)*65535/(height-1)),0,0xC001,0,0)))
    if send(1,c.byref(move),c.sizeof(move))!=1:raise target.backend.error('SendInput(scroll move)')
    time.sleep(.03)
    if layout_guard:layout_guard()
    require_click_position(target.snapshot(),hwnd,expected_size,(screen.x,screen.y))
    wheel=Input(type=0,data=InputUnion(mi=MouseInput(0,0,(ticks*120)&0xffffffff,0x800,0,0)))
    if send(1,c.byref(wheel),c.sizeof(wheel))!=1:raise target.backend.error('SendInput(wheel)')
    time.sleep(.12)
