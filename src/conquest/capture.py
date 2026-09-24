"""Desktop-only DXCam capture with foreground, occlusion and geometry guards."""

import time
from dataclasses import dataclass


class CaptureUnavailable(ValueError):
    """A recoverable lack of visible game observations; pause input."""


@dataclass(frozen=True)
class Frame:
    timestamp: float
    image: object
    origin: tuple[int, int]


class DesktopFrames:
    def __init__(
        self,
        hwnd,
        size=(1584, 861),
        output_idx=0,
        output_origin=(0, 0),
        *,
        require_focus=True,
    ):
        import dxcam
        import win32gui

        self.gui, self.hwnd, self.size = win32gui, hwnd, tuple(size)
        self.output_origin = tuple(output_origin)
        self.require_focus = require_focus
        self.camera = dxcam.create(output_idx=output_idx, output_color="BGR")

    def geometry(self):
        g, h = self.gui, self.hwnd
        if g.IsIconic(h) or (self.require_focus and g.GetForegroundWindow() != h):
            raise CaptureUnavailable("Game is minimized or does not have focus")
        rect = g.GetClientRect(h)
        if rect[2:] != self.size:
            raise ValueError("Game client geometry changed")
        origin = g.ClientToScreen(h, (0, 0))
        width, height = self.size
        ox, oy = self.output_origin
        if not (
            ox <= origin[0] <= ox + self.camera.width - width
            and oy <= origin[1] <= oy + self.camera.height - height
        ):
            raise ValueError("Game is outside calibrated capture monitor")
        # Foreground alone does not exclude topmost overlays: inspect every
        # visible window above the client in z order, including game dialogs.
        window = g.GetTopWindow(0)
        while window and window != h:
            if g.IsWindowVisible(window):
                # The installed computer-use helper has full-desktop transparent
                # cursor surfaces. They do not obscure the game; pixel checks
                # still reject a cursor over a calibrated observation.
                if (
                    g.GetClassName(window) == "CodexComputerUseCursorOverlay"
                    and g.GetWindowLong(window, -20) & 0x20
                ):
                    window = g.GetWindow(window, 2)
                    continue
                l, t, r, b = g.GetWindowRect(window)
                if (
                    l < origin[0] + width
                    and r > origin[0]
                    and t < origin[1] + height
                    and b > origin[1]
                ):
                    raise CaptureUnavailable("Another window covers the client")
            window = g.GetWindow(window, 2)  # GW_HWNDNEXT
        if window != h:
            raise ValueError("Game window disappeared")
        return origin

    def read(self):
        origin = self.geometry()
        x, y = origin[0] - self.output_origin[0], origin[1] - self.output_origin[1]
        frame = self.camera.grab(region=(x, y, x + self.size[0], y + self.size[1]))
        # A single DXGI poll may have no new presentation. Briefly wait for a
        # genuinely new frame; never reuse the prior image for another action.
        for _ in range(5):
            if frame is not None:
                break
            time.sleep(0.02)
            if self.geometry() != origin:
                raise ValueError("Game moved while waiting for a frame")
            frame = self.camera.grab(region=(x, y, x + self.size[0], y + self.size[1]))
        stamp = time.monotonic()
        if frame is None or self.geometry() != origin:
            raise ValueError("No fresh stable game frame")
        return Frame(stamp, frame, origin)

    def close(self):
        self.camera.release()
