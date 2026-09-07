"""One bounded window-message click for compatibility testing, not a farming backend."""

import ctypes as c
import time
from ctypes import wintypes as w
from datetime import datetime, timezone

from conquest.win32 import WindowsBackend, bind


class MessageTarget:
    def __init__(self, pid, hwnd):
        self.backend = WindowsBackend()
        self.pid, self.hwnd = pid, hwnd
        self.post_api = bind(self.backend.user, "PostMessageW", [w.HWND, w.UINT, w.WPARAM, w.LPARAM], w.BOOL)
        self.cursor_api = bind(self.backend.user, "GetCursorPos", [c.POINTER(w.POINT)], w.BOOL)

    def snapshot(self):
        owner = w.DWORD()
        if not self.backend.window_pid(self.hwnd, c.byref(owner)) or owner.value != self.pid:
            raise ValueError("Target window closed or changed ownership")
        rect = w.RECT()
        if not self.backend.client_rect(self.hwnd, c.byref(rect)):
            raise self.backend.error("GetClientRect")
        point = w.POINT()
        if not self.cursor_api(c.byref(point)):
            raise self.backend.error("GetCursorPos")
        return {"pid": owner.value, "hwnd": self.hwnd,
                "client_size": [rect.right - rect.left, rect.bottom - rect.top],
                "foreground": int(self.backend.foreground() or 0),
                "cursor": [point.x, point.y], "minimized": bool(self.backend.iconic(self.hwnd))}

    def post(self, message, wparam, lparam):
        owner = w.DWORD()
        if not self.backend.window_pid(self.hwnd, c.byref(owner)) or owner.value != self.pid:
            raise ValueError("Target window changed before input; no message sent")
        if not self.post_api(self.hwnd, message, wparam, lparam):
            raise self.backend.error("PostMessageW")


def click_probe(target, x, y, expected_size, *, require_unfocused=True, clock=time.monotonic, sleep=time.sleep):
    started = clock()
    before = target.snapshot()
    if before["client_size"] != list(expected_size):
        raise ValueError("Window size changed; recalibrate the point before probing")
    if before["minimized"]:
        raise ValueError("Minimized input is not qualified; restore the window first")
    if require_unfocused and before["foreground"] == target.hwnd:
        raise ValueError("Background probe requires another window to have focus")
    if not 0 <= x < min(expected_size[0], 32768) or not 0 <= y < min(expected_size[1], 32768):
        raise ValueError("Probe point lies outside the client area")
    packed = (y << 16) | x
    if clock() - started > 0.25:
        raise ValueError("Input observation expired before dispatch")
    # These target a single HWND and never invoke SetCursorPos or SetForegroundWindow.
    target.post(0x200, 0, packed)  # WM_MOUSEMOVE: client-local cursor position only.
    target.post(0x201, 1, packed)  # WM_LBUTTONDOWN
    try:
        sleep(0.12)  # Allow at least one tick at the client's configured 10 background FPS.
    finally:
        target.post(0x202, 0, packed)  # WM_LBUTTONUP, including interruption cleanup.
    sleep(0.25)
    after = target.snapshot()
    stable = before == after
    return {"schema_version": 1, "stage": "window_message_probe", "qualified": False,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "point": [x, y], "before": before, "after": after,
            "messages_queued": True, "sampled_desktop_state_unchanged": stable,
            "outcome": "awaiting_game_observation" if stable else "desktop_or_window_state_changed",
            "note": "Queue acceptance is not gameplay success. Verify the intended effect independently before retrying."}


def key_probe(target, vk, scan_code, expected_size, *, control_scan=None, clock=time.monotonic, sleep=time.sleep):
    """One F1-F11 window-directed key pair. No activation or desktop input."""
    if type(vk) is not int or not 0x70 <= vk <= 0x7A:
        raise ValueError("Key diagnostic supports F1 through F11 only")
    if type(scan_code) is not int or not 1 <= scan_code <= 0x7F:
        raise ValueError("Invalid keyboard scan code")
    if control_scan is not None and (type(control_scan) is not int or not 1 <= control_scan <= 0x7F):
        raise ValueError("Invalid Control scan code")
    started = clock()
    before = target.snapshot()
    if before["foreground"] == target.hwnd or before["minimized"]:
        raise ValueError("Key probe requires an unfocused, non-minimized game window")
    if before["client_size"] != list(expected_size):
        raise ValueError("Window size changed before key probe")
    if clock() - started > 0.25:
        raise ValueError("Key probe observation expired")
    keys = ([(0x11, control_scan)] if control_scan is not None else []) + [(vk, scan_code)]
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
    return {"schema_version": 1, "stage": "window_key_probe", "qualified": False,
            "vk": vk, "control": control_scan is not None,
            "before": before, "after": after, "messages_queued": True,
            "sampled_desktop_state_unchanged": before == after,
            "note": "Verify the configured panel effect independently; queued messages do not establish compatibility."}
