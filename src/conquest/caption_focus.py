"""Focus our own visible desktop caption using guarded native mouse input."""

import ctypes as c
from ctypes import wintypes as w
import os
import re
import time
import win32process
from conquest.merchants.coordination import check_input
from conquest.mouse_priority import guarded_send
from conquest.win32 import bind


def activate_owner_caption(hwnd, identity, api):
    from conquest.foreground import Input, InputUnion, MouseInput
    from conquest.desktop_runtime import physical_coordinates

    gui = api.gui
    api.assert_owner(hwnd, identity)
    owner = gui.GetWindow(
        hwnd, 4
    )  # Only a game owned by THIS app may use this fallback.
    if not owner or win32process.GetWindowThreadProcessId(owner)[1] != os.getpid():
        return False
    with physical_coordinates():
        check_input()
        flags = 0x0001 | 0x0002 | 0x0010 | 0x0040  # no size/move/activation; show
        topmost = bool(gui.GetWindowLong(owner, -20) & 8)
        try:
            gui.SetWindowPos(owner, -1, 0, 0, 0, 0, flags)
        finally:
            if not topmost:
                gui.SetWindowPos(owner, -2, 0, 0, 0, 0, flags)
        left, top, right, bottom = gui.GetWindowRect(owner)
        _, client_y = gui.ClientToScreen(owner, (0, 0))
        point = ((left + right) // 2, (top + client_y) // 2)
        hit = bind(
            api.backend.user,
            "SendMessageTimeoutW",
            [w.HWND, w.UINT, w.WPARAM, w.LPARAM, w.UINT, w.UINT, c.POINTER(c.c_size_t)],
            w.LPARAM,
        )

        def caption():
            check_input()
            api.assert_owner(hwnd, identity)
            if (
                gui.GetWindowRect(owner) != (left, top, right, bottom)
                or gui.WindowFromPoint(point) != owner
            ):
                return False
            result = c.c_size_t()
            coords = c.c_int32(((point[1] & 65535) << 16) | (point[0] & 65535)).value
            return (
                bool(hit(owner, 0x84, 0, coords, 2, 200, c.byref(result)))
                and result.value == 2
            )

        if not caption():
            return False
        metrics = bind(api.backend.user, "GetSystemMetrics", [c.c_int], c.c_int)
        x, y, width, height = [metrics(n) for n in (76, 77, 78, 79)]
        send = guarded_send(
            bind(
                api.backend.user,
                "SendInput",
                [w.UINT, c.POINTER(Input), c.c_int],
                w.UINT,
            )
        )

        def mouse(flags, dx=0, dy=0):
            event = Input(
                type=0, data=InputUnion(mi=MouseInput(dx, dy, 0, flags, 0, 0))
            )
            if send(1, c.byref(event), c.sizeof(event)) != 1:
                raise OSError("Native caption focus input was not delivered")

        mouse(
            0xC001,
            round((point[0] - x) * 65535 / (width - 1)),
            round((point[1] - y) * 65535 / (height - 1)),
        )
        if not caption() or gui.GetCursorPos() != point:
            return False
        try:
            mouse(2)
        finally:
            mouse(4)
        time.sleep(0.05)
        return gui.GetForegroundWindow() in (owner, hwnd)


def activate_native_caption(hwnd, identity, api):
    """Focus only this process-pinned native Conquest title bar, never game controls."""
    from conquest.capture import CaptureUnavailable
    from conquest.foreground import Input, InputUnion, MouseInput
    from conquest.desktop_runtime import physical_coordinates

    gui = api.gui
    api.assert_owner(hwnd, identity)
    title = gui.GetWindowText(hwnd).strip()
    if (
        gui.GetAncestor(hwnd, 2) != hwnd
        or gui.GetWindow(hwnd, 4)
        or not gui.IsWindowVisible(hwnd)
        or gui.IsIconic(hwnd)
        or gui.GetClassName(hwnd) != "ImGuiShell"
        or not (
            title == "[ClassicConquer]"
            or re.fullmatch(r"\[[^\]\r\n]{1,63} - ClassicConquer\]", title)
        )
        or gui.GetWindowLong(hwnd, -16) & 0x00C00000 != 0x00C00000
    ):
        return False
    with physical_coordinates():
        check_input()
        api.assert_owner(hwnd, identity)
        flags = 0x0001 | 0x0002 | 0x0010 | 0x0040  # no size/move/activation; show
        original_topmost = bool(gui.GetWindowLong(hwnd, -20) & 8)
        try:
            # HWND_TOP cannot overtake a topmost foreground app. Temporarily
            # raise only the exact native game HWND, retaining its old band.
            gui.SetWindowPos(hwnd, -1, 0, 0, 0, 0, flags)
            rect = gui.GetWindowRect(hwnd)
            _, client_y = gui.ClientToScreen(hwnd, (0, 0))
            point = ((rect[0] + rect[2]) // 2, (rect[1] + client_y) // 2)
            hit = bind(
                api.backend.user,
                "SendMessageTimeoutW",
                [
                    w.HWND,
                    w.UINT,
                    w.WPARAM,
                    w.LPARAM,
                    w.UINT,
                    w.UINT,
                    c.POINTER(c.c_size_t),
                ],
                w.LPARAM,
            )
            key_state = bind(api.backend.user, "GetAsyncKeyState", [c.c_int], c.c_short)

            def caption():
                check_input()
                api.assert_owner(hwnd, identity)
                if (
                    key_state(0x7B) & 0x8000
                    or gui.GetAncestor(hwnd, 2) != hwnd
                    or gui.GetWindow(hwnd, 4)
                    or not gui.IsWindowVisible(hwnd)
                    or gui.IsIconic(hwnd)
                    or gui.GetClassName(hwnd) != "ImGuiShell"
                    or gui.GetWindowText(hwnd).strip() != title
                    or gui.GetWindowRect(hwnd) != rect
                ):
                    raise CaptureUnavailable(
                        "Native Conquest title bar changed or Stop was pressed; no button pressed"
                    )
                if gui.WindowFromPoint(point) != hwnd:
                    raise CaptureUnavailable(
                        "Native Conquest title bar is covered; no button pressed"
                    )
                result = c.c_size_t()
                coords = c.c_int32(
                    ((point[1] & 65535) << 16) | (point[0] & 65535)
                ).value
                if (
                    not hit(hwnd, 0x84, 0, coords, 2, 200, c.byref(result))
                    or result.value != 2
                ):
                    raise CaptureUnavailable(
                        "Native Conquest title bar hit test is not HTCAPTION; no button pressed"
                    )

            caption()
            metrics = bind(api.backend.user, "GetSystemMetrics", [c.c_int], c.c_int)
            x, y, width, height = [metrics(n) for n in (76, 77, 78, 79)]
            if min(width, height) <= 1:
                raise CaptureUnavailable(
                    "Native desktop geometry is invalid; no button pressed"
                )
            send = guarded_send(
                bind(
                    api.backend.user,
                    "SendInput",
                    [w.UINT, c.POINTER(Input), c.c_int],
                    w.UINT,
                )
            )

            def mouse(flags, dx=0, dy=0):
                event = Input(
                    type=0, data=InputUnion(mi=MouseInput(dx, dy, 0, flags, 0, 0))
                )
                if send(1, c.byref(event), c.sizeof(event)) != 1:
                    raise OSError("Native caption focus input was not delivered")

            mouse(
                0xC001,
                round((point[0] - x) * 65535 / (width - 1)),
                round((point[1] - y) * 65535 / (height - 1)),
            )
            caption()
            if gui.GetCursorPos() != point:
                raise CaptureUnavailable(
                    "Pointer did not reach native title bar; no button pressed"
                )
            try:
                mouse(2)
            finally:
                mouse(4)
        finally:
            if not original_topmost:
                gui.SetWindowPos(hwnd, -2, 0, 0, 0, 0, flags)
        time.sleep(0.05)
        check_input()
        api.assert_owner(hwnd, identity)
        if gui.GetForegroundWindow() != hwnd:
            raise CaptureUnavailable(
                "Native title-bar click did not transfer foreground ownership"
            )
        return True
