"""Disposable hidden-window mouse experiment; never accepts a game HWND.

Windows message order and TrackMouseEvent results are real observations. Frame
processing is a small diagnostic model of the captured ImGui input-trickle and
control-character-filter branches, not a game renderer or gameplay proof.
"""

from conquest.character_context import state_path

import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import time


INVALID_POSITION = (-3.4028234663852886e38,) * 2


def model_frame(queue, position=INVALID_POSITION):
    """Model only mouse-position/text ordering with no active text editor."""
    moved = False
    inserted = []
    used = 0
    for event in queue:
        if event["type"] == "mouse_position":
            next_position = tuple(event["position"])
            if position != next_position:
                position = next_position
                moved = True
        elif event["type"] == "text":
            # Captured 0x1f318..0x1f338 stops before text after mouse movement.
            if moved:
                break
            value = event["value"]
            # Captured 0x4c675..0x4c839 rejects control characters before
            # callbacks. This model has neither multiline nor AllowTabInput.
            if value >= 0x20 and value != 0x7F:
                inserted.append(value)
        else:
            raise ValueError("Unsupported diagnostic model event")
        used += 1
    return {
        "position": list(position),
        "processed": used,
        "remaining": queue[used:],
        "inserted_characters": inserted,
    }


def _pair_count(pairs):
    if type(pairs) is not int or not 1 <= pairs <= 12:
        raise ValueError("Dummy mouse probe permits 1 through 12 pairs")
    return pairs


def _child(pairs, alternate=False, capture=False):
    import win32api
    import win32con
    import win32gui
    from ctypes import wintypes as w

    _pair_count(pairs)
    user = ctypes.WinDLL("user32", use_last_error=True)

    class Track(ctypes.Structure):
        _fields_ = [
            ("cbSize", w.DWORD),
            ("dwFlags", w.DWORD),
            ("hwndTrack", w.HWND),
            ("dwHoverTime", w.DWORD),
        ]

    class GuiThreadInfo(ctypes.Structure):
        _fields_ = [
            ("cbSize", w.DWORD),
            ("flags", w.DWORD),
            ("hwndActive", w.HWND),
            ("hwndFocus", w.HWND),
            ("hwndCapture", w.HWND),
            ("hwndMenuOwner", w.HWND),
            ("hwndMoveSize", w.HWND),
            ("hwndCaret", w.HWND),
            ("rcCaret", w.RECT),
        ]

    user.TrackMouseEvent.argtypes = (ctypes.POINTER(Track),)
    user.TrackMouseEvent.restype = w.BOOL
    user.GetWindowThreadProcessId.argtypes = (w.HWND, ctypes.POINTER(w.DWORD))
    user.GetWindowThreadProcessId.restype = w.DWORD
    user.GetGUIThreadInfo.argtypes = (w.DWORD, ctypes.POINTER(GuiThreadInfo))
    user.GetGUIThreadInfo.restype = w.BOOL
    native = []
    queue = []
    callbacks_failed = []
    tracked = False
    held = False
    started = time.monotonic()
    class_name = f"ConquestMouseDummy-{os.getpid()}"
    hwnd = None
    registered = False
    baseline = {
        "foreground": win32gui.GetForegroundWindow(),
        "cursor": list(win32api.GetCursorPos()),
    }
    owner_pid = w.DWORD()
    foreground_thread = user.GetWindowThreadProcessId(
        baseline["foreground"], ctypes.byref(owner_pid)
    )
    if not baseline["foreground"] or owner_pid.value == os.getpid():
        raise RuntimeError("An independent foreground window is required")

    def callback(window, message, wp, lp):
        nonlocal tracked, held
        try:
            if capture and message == win32con.WM_LBUTTONDOWN:
                before = win32gui.GetCapture()
                if not held and not before:
                    win32gui.SetCapture(window)
                held = True
                native.append(
                    {
                        "message": "WM_LBUTTONDOWN",
                        "capture_before": before,
                        "capture_after": win32gui.GetCapture(),
                    }
                )
                return 0
            if capture and message == win32con.WM_LBUTTONUP:
                held = False
                if win32gui.GetCapture() == window:
                    win32gui.ReleaseCapture()
                native.append(
                    {"message": "WM_LBUTTONUP", "capture_after": win32gui.GetCapture()}
                )
                return 0
            if message == win32con.WM_MOUSEMOVE:
                point = [
                    ctypes.c_short(lp & 0xFFFF).value,
                    ctypes.c_short((lp >> 16) & 0xFFFF).value,
                ]
                row = {"message": "WM_MOUSEMOVE", "position": point}
                if not tracked:
                    request = Track(ctypes.sizeof(Track), 2, window, 0)
                    success = bool(user.TrackMouseEvent(ctypes.byref(request)))
                    row["track_mouse_event_accepted"] = success
                    if not success:
                        raise ctypes.WinError(ctypes.get_last_error())
                    tracked = True
                native.append(row)
                queue.append({"type": "mouse_position", "position": point})
                return 0
            if message == win32con.WM_MOUSELEAVE:
                tracked = False
                native.append({"message": "WM_MOUSELEAVE"})
                queue.append(
                    {"type": "mouse_position", "position": list(INVALID_POSITION)}
                )
                return 0
            if message == win32con.WM_CHAR:
                # Never permit this lab's message source to inject real text.
                if wp != 1:
                    raise ValueError("Unexpected dummy character")
                native.append({"message": "WM_CHAR", "value": 1})
                queue.append({"type": "text", "value": 1})
                return 0
            return win32gui.DefWindowProc(window, message, wp, lp)
        except BaseException as error:
            callbacks_failed.append(type(error).__name__)
            return 0

    frames, samples, inserted = [], [], []
    try:
        cls = win32gui.WNDCLASS()
        cls.hInstance = win32api.GetModuleHandle(None)
        cls.lpszClassName = class_name
        cls.lpfnWndProc = callback
        win32gui.RegisterClass(cls)
        registered = True
        hwnd = win32gui.CreateWindowEx(
            0,
            class_name,
            "Disposable mouse dummy",
            win32con.WS_POPUP,
            -32000,
            -32000,
            200,
            200,
            0,
            0,
            cls.hInstance,
            None,
        )
        if (
            win32gui.IsWindowVisible(hwnd)
            or win32gui.GetParent(hwnd)
            or win32gui.GetWindow(hwnd, win32con.GW_OWNER)
        ):
            raise RuntimeError("Dummy must be invisible, unowned and unparented")
        target_pid = w.DWORD()
        user.GetWindowThreadProcessId(hwnd, ctypes.byref(target_pid))
        if target_pid.value != os.getpid():
            raise RuntimeError("Only this child process may own the dummy")
        # Queue a bounded batch before pumping, explicitly testing the ordering
        # hypothesis. A live target may interleave generated leave earlier.
        for index in range(pairs):
            x = 60 + (index % 2 if alternate else 0)
            win32gui.PostMessage(hwnd, win32con.WM_MOUSEMOVE, 0, 80 << 16 | x)
            win32gui.PostMessage(hwnd, win32con.WM_CHAR, 1, 0)
        position = INVALID_POSITION
        deadline = started + 1.5
        while time.monotonic() < deadline:
            win32gui.PumpWaitingMessages()
            current = {
                "foreground": win32gui.GetForegroundWindow(),
                "cursor": list(win32api.GetCursorPos()),
            }
            samples.append(current)
            if current != baseline:
                raise RuntimeError("Desktop focus or cursor changed during dummy probe")
            if callbacks_failed:
                raise RuntimeError(
                    "Dummy callback failed: " + ", ".join(callbacks_failed)
                )
            if queue:
                frame = model_frame(queue, position)
                queue[:] = frame.pop("remaining")
                position = tuple(frame["position"])
                inserted.extend(frame["inserted_characters"])
                frame["elapsed"] = time.monotonic() - started
                frame["remaining_events"] = len(queue)
                frames.append(frame)
            if not queue and any(row["message"] == "WM_MOUSELEAVE" for row in native):
                break
            time.sleep(0.01)
        capture_result = None
        if capture:

            def capture_observation():
                info = GuiThreadInfo()
                info.cbSize = ctypes.sizeof(info)
                if not user.GetGUIThreadInfo(foreground_thread, ctypes.byref(info)):
                    raise ctypes.WinError(ctypes.get_last_error())
                desktop = {
                    "foreground": win32gui.GetForegroundWindow(),
                    "cursor": list(win32api.GetCursorPos()),
                }
                samples.append(desktop)
                if desktop != baseline:
                    raise RuntimeError("Desktop changed during dummy capture probe")
                return {
                    "foreground_capture": int(info.hwndCapture or 0),
                    "foreground_focus": int(info.hwndFocus or 0),
                    "dummy_capture": win32gui.GetCapture(),
                    "dummy_button_held": held,
                }

            before = capture_observation()
            try:
                win32gui.PostMessage(hwnd, win32con.WM_LBUTTONDOWN, 1, 80 << 16 | 60)
                win32gui.PumpWaitingMessages()
                down = capture_observation()
                if not held or callbacks_failed:
                    raise RuntimeError("Dummy mouse down was not acknowledged")
                time.sleep(0.025)
                during = capture_observation()
            finally:
                win32gui.PostMessage(hwnd, win32con.WM_LBUTTONUP, 0, 80 << 16 | 60)
                win32gui.PumpWaitingMessages()
            after = capture_observation()
            capture_result = {
                "before": before,
                "down": down,
                "during": during,
                "after": after,
                "foreground_capture_unchanged": all(
                    row["foreground_capture"] == before["foreground_capture"]
                    for row in (down, during, after)
                ),
                "foreground_focus_unchanged": all(
                    row["foreground_focus"] == before["foreground_focus"]
                    for row in (down, during, after)
                ),
                "dummy_capture_cleared": after["dummy_capture"] == 0
                and not after["dummy_button_held"],
                "limitation": "No physical input was injected. An initially uncaptured foreground window does not test an active user drag.",
            }
        return {
            "schema_version": 1,
            "dummy_only": True,
            "gameplay_qualified": False,
            "pairs": pairs,
            "alternate_adjacent_positions": alternate,
            "native_messages": native,
            "modeled_frames": frames,
            "generated_leave_observed": any(
                row["message"] == "WM_MOUSELEAVE" for row in native
            ),
            "modeled_requested_position_frames": sum(
                frame["position"] in ([60, 80], [61, 80]) for frame in frames
            ),
            "inserted_characters": inserted,
            "queue_drained": not queue,
            "desktop_unchanged": all(row == baseline for row in samples),
            "desktop_samples": len(samples),
            "callback_errors": callbacks_failed,
            "capture_test": capture_result,
            "method": "Actual Windows messages; modeled ImGui frames; batch queued before pumping",
        }
    finally:
        if hwnd and win32gui.IsWindow(hwnd):
            if win32gui.GetCapture() == hwnd:
                win32gui.ReleaseCapture()
            win32gui.DestroyWindow(hwnd)
        if registered:
            win32gui.UnregisterClass(class_name, win32api.GetModuleHandle(None))


def run_lab(*, pairs=12, alternate=False, capture=False):
    """An 8-second child-process deadline bounds all native operations."""
    _pair_count(pairs)
    if type(alternate) is not bool or type(capture) is not bool:
        raise ValueError("Alternate and capture must be booleans")
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "conquest.background_mouse_lab",
            "--child",
            str(pairs),
            str(int(alternate)),
            str(int(capture)),
        ],
        capture_output=True,
        text=True,
        timeout=8,
        check=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    result = json.loads(completed.stdout)
    result["child_exited"] = True
    return result


if __name__ == "__main__":
    if (
        len(sys.argv) == 5
        and sys.argv[1] == "--child"
        and all(value in ("0", "1") for value in sys.argv[3:])
    ):
        print(
            json.dumps(
                _child(int(sys.argv[2]), bool(int(sys.argv[3])), bool(int(sys.argv[4])))
            )
        )
    elif len(sys.argv) == 1:
        result = {
            "schema_version": 1,
            "dummy_only": True,
            "gameplay_qualified": False,
            "same_position": run_lab(),
            "alternating_positions": run_lab(alternate=True),
            "capture": run_lab(pairs=1, capture=True),
        }
        destination = Path(
            state_path("reports/merchants/background/mouse-dummy-verification.json")
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result))
    else:
        raise SystemExit("The dummy lab accepts no target HWND or process arguments")
