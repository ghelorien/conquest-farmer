"""One bounded window-message click for compatibility testing, not a farming backend."""

import ctypes as c
import time
from ctypes import wintypes as w
from datetime import datetime, timezone

from conquest.win32 import WindowsBackend, bind
from conquest.mouse_priority import desktop_access_denied


class MessageTarget:
    def __init__(self, pid, hwnd):
        self.backend = WindowsBackend()
        self.pid, self.hwnd = pid, hwnd
        self.post_api = bind(
            self.backend.user,
            "PostMessageW",
            [w.HWND, w.UINT, w.WPARAM, w.LPARAM],
            w.BOOL,
        )
        self.cursor_api = bind(
            self.backend.user, "GetCursorPos", [c.POINTER(w.POINT)], w.BOOL
        )
        self.ancestor_api = bind(
            self.backend.user, "GetAncestor", [w.HWND, w.UINT], w.HWND
        )
        self.to_screen_api = bind(
            self.backend.user, "ClientToScreen", [w.HWND, c.POINTER(w.POINT)], w.BOOL
        )
        self.set_cursor_api = bind(
            self.backend.user, "SetCursorPos", [c.c_int, c.c_int], w.BOOL
        )
        self.key_state_api = bind(
            self.backend.user, "GetAsyncKeyState", [c.c_int], c.c_short
        )

    def screen_point(self, x, y):
        point = w.POINT(x, y)
        if not self.to_screen_api(self.hwnd, c.byref(point)):
            raise self.backend.error("ClientToScreen")
        return [point.x, point.y]

    def move_cursor(self, point):
        if not self.set_cursor_api(*point):
            raise self.backend.error("SetCursorPos")

    def mouse_busy(self):
        return any(self.key_state_api(key) & 0x8000 for key in (1, 2, 4, 5, 6, 0x7B))

    def snapshot(self, *, allow_cursor_unavailable=False):
        owner = w.DWORD()
        if (
            not self.backend.window_pid(self.hwnd, c.byref(owner))
            or owner.value != self.pid
        ):
            raise ValueError("Target window closed or changed ownership")
        rect = w.RECT()
        if not self.backend.client_rect(self.hwnd, c.byref(rect)):
            raise self.backend.error("GetClientRect")
        point = w.POINT()
        cursor_available = True
        if not self.cursor_api(c.byref(point)):
            error = self.backend.error("GetCursorPos")
            if not allow_cursor_unavailable or not desktop_access_denied(error):
                raise error
            cursor_available = False
        root = int(self.ancestor_api(self.hwnd, 2) or self.hwnd)
        return {
            "pid": owner.value,
            "hwnd": self.hwnd,
            "root_hwnd": root,
            "client_size": [rect.right - rect.left, rect.bottom - rect.top],
            "foreground": int(self.backend.foreground() or 0),
            "cursor": [point.x, point.y] if cursor_available else None,
            "cursor_available": cursor_available,
            "minimized": bool(self.backend.iconic(root)),
        }

    def post(self, message, wparam, lparam):
        owner = w.DWORD()
        if (
            not self.backend.window_pid(self.hwnd, c.byref(owner))
            or owner.value != self.pid
        ):
            raise ValueError("Target window changed before input; no message sent")
        if not self.post_api(self.hwnd, message, wparam, lparam):
            raise self.backend.error("PostMessageW")


def click_probe(
    target,
    x,
    y,
    expected_size,
    *,
    require_unfocused=True,
    move_settle_seconds=0,
    control=False,
    control_scan=0x1D,
    clock=time.monotonic,
    sleep=time.sleep,
):
    if (
        not isinstance(move_settle_seconds, (int, float))
        or not 0 <= move_settle_seconds <= 0.25
    ):
        raise ValueError("Mouse movement settling delay must be 0 to 250 ms")
    if type(control) is not bool:
        raise ValueError("control must be a boolean")
    if type(control_scan) is not int or not 1 <= control_scan <= 0x7F:
        raise ValueError("Invalid Control scan code")
    started = clock()
    before = target.snapshot()
    if before["client_size"] != list(expected_size):
        raise ValueError("Window size changed; recalibrate the point before probing")
    if before["minimized"]:
        raise ValueError("Minimized input is not qualified; restore the window first")
    if require_unfocused and before["foreground"] == before.get(
        "root_hwnd", target.hwnd
    ):
        raise ValueError("Background probe requires another window to have focus")
    if not 0 <= x < min(expected_size[0], 32768) or not 0 <= y < min(
        expected_size[1], 32768
    ):
        raise ValueError("Probe point lies outside the client area")
    packed = (y << 16) | x
    if clock() - started > 0.25:
        raise ValueError("Input observation expired before dispatch")
    # Target one HWND; modifier messages do not change global keyboard state.
    modifier_attempted = False
    try:
        if control:
            modifier_attempted = True
            target.post(0x100, 0x11, 1 | control_scan << 16)  # WM_KEYDOWN / VK_CONTROL
            sleep(0.05)
            if target.snapshot() != before:
                raise ValueError(
                    "Desktop changed during modifier preparation; no mouse message sent"
                )
        modifier = (
            0x8 if control else 0
        )  # MK_CONTROL also belongs in the mouse message.
        target.post(0x200, modifier, packed)
        if move_settle_seconds:
            sleep(move_settle_seconds)
            if target.snapshot() != before:
                raise ValueError(
                    "Window or desktop changed before button down; only mouse movement was queued"
                )
        try:
            target.post(0x201, modifier | 1, packed)
            sleep(0.12)  # At least one tick at the client's 10 background FPS.
        finally:
            target.post(0x202, modifier, packed)
    finally:
        if modifier_attempted:
            target.post(0x101, 0x11, 1 | control_scan << 16 | (3 << 30))
    sleep(0.25)
    after = target.snapshot()
    stable = before == after
    return {
        "schema_version": 1,
        "stage": "window_message_probe",
        "qualified": False,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "point": [x, y],
        "before": before,
        "after": after,
        "control": control,
        "move_settle_seconds": move_settle_seconds,
        "messages_queued": True,
        "sampled_desktop_state_unchanged": stable,
        "outcome": "awaiting_game_observation"
        if stable
        else "desktop_or_window_state_changed",
        "note": "Queue acceptance is not gameplay success. Verify the intended effect independently before retrying.",
    }


def key_probe(
    target,
    vk,
    scan_code,
    expected_size,
    *,
    control_scan=None,
    clock=time.monotonic,
    sleep=time.sleep,
):
    """One F1-F11 window-directed key pair. No activation or desktop input."""
    if type(vk) is not int or not 0x70 <= vk <= 0x7A:
        raise ValueError("Key diagnostic supports F1 through F11 only")
    if type(scan_code) is not int or not 1 <= scan_code <= 0x7F:
        raise ValueError("Invalid keyboard scan code")
    if control_scan is not None and (
        type(control_scan) is not int or not 1 <= control_scan <= 0x7F
    ):
        raise ValueError("Invalid Control scan code")
    started = clock()
    before = target.snapshot()
    if (
        before["foreground"] == before.get("root_hwnd", target.hwnd)
        or before["minimized"]
    ):
        raise ValueError("Key probe requires an unfocused, non-minimized game window")
    if before["client_size"] != list(expected_size):
        raise ValueError("Window size changed before key probe")
    if clock() - started > 0.25:
        raise ValueError("Key probe observation expired")
    keys = ([(0x11, control_scan)] if control_scan is not None else []) + [
        (vk, scan_code)
    ]
    attempted = []
    try:
        for code, scan in keys:
            attempted.append((code, scan))
            target.post(0x100, code, 1 | scan << 16)
            sleep(0.05)
        sleep(0.15)
    finally:
        first_error = None
        for code, scan in reversed(attempted):
            try:
                target.post(0x101, code, 1 | scan << 16 | (3 << 30))
            except OSError as error:
                first_error = first_error or error
        if first_error is not None:
            raise first_error
    sleep(0.25)
    after = target.snapshot()
    return {
        "schema_version": 1,
        "stage": "window_key_probe",
        "qualified": False,
        "vk": vk,
        "control": control_scan is not None,
        "before": before,
        "after": after,
        "messages_queued": True,
        "sampled_desktop_state_unchanged": before == after,
        "note": "Verify the configured panel effect independently; queued messages do not establish compatibility.",
    }


def positioned_click_probe(target, x, y, expected_size, *, sleep=time.sleep):
    """One unfocused click using a briefly borrowed desktop cursor.

    This diagnostic is deliberately separate from cursor-independent input.
    It never activates the game or injects a desktop mouse-button event.
    """
    before = target.snapshot()
    if (
        before["foreground"] == before.get("root_hwnd", target.hwnd)
        or before["minimized"]
    ):
        raise ValueError("Positioned probe requires an unfocused, restored game")
    if (
        before["client_size"] != list(expected_size)
        or not 0 <= x < expected_size[0]
        or not 0 <= y < expected_size[1]
    ):
        raise ValueError("Positioned probe geometry changed")
    if target.mouse_busy():
        raise ValueError("A mouse button or emergency stop key is held")
    sleep(0.2)
    if target.snapshot() != before or target.mouse_busy():
        raise ValueError(
            "Desktop input is active; no cursor movement or click was sent"
        )
    point = target.screen_point(x, y)
    result = None
    target.move_cursor(point)
    try:
        positioned = target.snapshot()
        expected = {**before, "cursor": point}
        if positioned != expected or target.mouse_busy():
            raise ValueError(
                "Desktop changed during cursor positioning; no button message sent"
            )

        def guarded_wait(delay):
            sleep(delay)
            if target.snapshot() != expected or target.mouse_busy():
                raise ValueError(
                    "User input or window state changed during the positioned probe"
                )

        result = click_probe(
            target, x, y, expected_size, move_settle_seconds=0.2, sleep=guarded_wait
        )
    finally:
        current = target.snapshot()
        # A user movement takes priority over restoring our saved cursor.
        if current["cursor"] == point and not target.mouse_busy():
            target.move_cursor(before["cursor"])
    after = target.snapshot()
    return {
        "schema_version": 1,
        "stage": "positioned_background_probe",
        "qualified": False,
        "before": before,
        "after": after,
        "point": [x, y],
        "cursor_borrowed": True,
        "focus_unchanged": before["foreground"] == after["foreground"],
        "cursor_restored": before["cursor"] == after["cursor"],
        "click": result,
    }
