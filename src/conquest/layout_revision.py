"""Memory/native geometry revision used to invalidate stale input plans."""

from dataclasses import dataclass
import ctypes as c
from ctypes import wintypes as w
import time

from conquest.capture import CaptureUnavailable
from conquest.mouse_priority import active as manual_mouse_active
from conquest.win32 import bind


TRACKED_PANELS = frozenset(
    {
        "Inventory",
        "Shop",
        "Warehouse",
        "Booth",
        "Dialog",
        "Add Item to Booth",
        "Trade",
        "Trade###Confirm",
        "Trade##TradeWindow",
        "###Confirm",
    }
)


class LayoutChanged(CaptureUnavailable):
    """A structural UI/native-window revision changed before input."""

    code = "layout_changed"


@dataclass(frozen=True)
class LayoutSnapshot:
    hwnd: int
    root_hwnd: int
    client_size: tuple
    client_origin: tuple
    dpi: int
    monitor: int
    minimized: bool
    foreground: int
    desktop: tuple
    panels: tuple
    gui_size: tuple | None
    manual_input: bool

    @property
    def structural_key(self):
        # Cursor, camera and character position intentionally do not belong to
        # the layout revision. They are checked independently at dispatch.
        return (
            self.hwnd,
            self.root_hwnd,
            self.client_size,
            self.client_origin,
            self.dpi,
            self.monitor,
            self.minimized,
            self.desktop,
            self.panels,
            self.gui_size,
        )


def _panel_signature(windows):
    result = []
    for window in windows or ():
        name = window.get("name")
        if name not in TRACKED_PANELS and not str(name).startswith("Trade##"):
            continue
        geometry = window.get("geometry")
        if not isinstance(geometry, (list, tuple)) or len(geometry) != 4:
            raise LayoutChanged("Invalid tracked panel geometry")
        result.append(
            (
                name,
                int(window.get("address", 0)),
                tuple(geometry),
                tuple(window.get("scroll", (0, 0))),
            )
        )
    return tuple(sorted(result))


def native_layout(target):
    """Read physical client placement/DPI without inspecting game pixels."""
    state = target.snapshot()
    user = target.backend.user
    to_screen = bind(user, "ClientToScreen", [w.HWND, c.POINTER(w.POINT)], w.BOOL)
    origin = w.POINT()
    if not to_screen(target.hwnd, c.byref(origin)):
        raise target.backend.error("ClientToScreen(layout)")
    metrics = bind(user, "GetSystemMetrics", [c.c_int], c.c_int)
    desktop = tuple(metrics(i) for i in (76, 77, 78, 79))
    try:
        dpi = int(bind(user, "GetDpiForWindow", [w.HWND], w.UINT)(target.hwnd))
    except (AttributeError, TypeError):
        dpi = int(state.get("dpi") or 96)
    try:
        monitor = int(
            bind(user, "MonitorFromWindow", [w.HWND, w.DWORD], w.HANDLE)(target.hwnd, 2)
            or 0
        )
    except (AttributeError, TypeError):
        monitor = int(state.get("monitor") or 0)
    return state, (origin.x, origin.y), dpi, monitor, desktop


class SharedLayoutRevision:
    """Require structural layout stability, then fence a queued input action."""

    def __init__(
        self,
        target,
        *,
        windows=lambda: (),
        gui_size=lambda: None,
        manual_active=manual_mouse_active,
        native_reader=native_layout,
        clock=time.monotonic,
        sleep=time.sleep,
    ):
        self.target = target
        self.windows = windows
        self.gui_size = gui_size
        self.manual_active = manual_active
        self.native_reader = native_reader
        self.clock = clock
        self.sleep = sleep
        self._qualified = None

    def read(self):
        state, origin, dpi, monitor, desktop = self.native_reader(self.target)
        gui = self.gui_size()
        snapshot = LayoutSnapshot(
            int(state.get("hwnd", self.target.hwnd)),
            int(state.get("root_hwnd", self.target.hwnd)),
            tuple(state["client_size"]),
            tuple(origin),
            int(dpi),
            int(monitor),
            bool(state["minimized"]),
            int(state["foreground"]),
            tuple(desktop),
            _panel_signature(self.windows()),
            tuple(gui) if gui is not None else None,
            bool(self.manual_active()),
        )
        self.require_actionable(snapshot)
        return snapshot

    @staticmethod
    def require_actionable(snapshot):
        if snapshot.manual_input:
            raise LayoutChanged("Manual input has priority; no input sent")
        if snapshot.minimized or snapshot.foreground != snapshot.root_hwnd:
            raise LayoutChanged(
                "Game layout is not foreground/actionable; no input sent"
            )
        left, top, width, height = snapshot.desktop
        x, y = snapshot.client_origin
        cw, ch = snapshot.client_size
        if (
            min(width, height, cw, ch) <= 1
            or x + cw <= left
            or y + ch <= top
            or x >= left + width
            or y >= top + height
        ):
            raise LayoutChanged("Game client is offscreen; no input sent")
        if snapshot.gui_size is not None and min(snapshot.gui_size) <= 1:
            raise LayoutChanged("Game GUI viewport is invalid; no input sent")

    def stable(self, minimum=0.25, timeout=0.75):
        if minimum < 0 or timeout < minimum:
            raise ValueError("Invalid layout stability interval")
        deadline = self.clock() + timeout
        current = self.read()
        since = self.clock()
        while self.clock() - since < minimum:
            if self.clock() >= deadline:
                raise LayoutChanged("Game layout did not stabilize; no input sent")
            self.sleep(min(0.02, max(0, deadline - self.clock())))
            fresh = self.read()
            if fresh.structural_key != current.structural_key:
                current = fresh
                since = self.clock()
        return current

    def qualified(self, minimum=0.25, timeout=0.75):
        """Return immediately for an already-qualified structural revision.

        Camera and actor movement are deliberately absent from structural_key,
        so ordinary route movement does not pay another stability interval.
        A native-window, DPI, viewport, or tracked-panel change must stabilize
        again before it can become the new qualified revision.
        """
        try:
            current = self.read()
        except Exception:
            self._qualified = None
            raise
        if (
            self._qualified is not None
            and current.structural_key == self._qualified.structural_key
        ):
            self._qualified = current
            return current
        try:
            current = self.stable(minimum, timeout)
        except Exception:
            self._qualified = None
            raise
        self._qualified = current
        return current

    def assert_current(self, expected):
        try:
            fresh = self.read()
        except Exception:
            self._qualified = None
            raise
        if fresh.structural_key != expected.structural_key:
            self._qualified = None
            raise LayoutChanged(
                "Game or panel layout changed; queued input was cancelled"
            )
        return fresh
