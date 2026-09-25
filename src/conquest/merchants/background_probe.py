"""Opt-in, bounded background diagnostics. No production input routing."""

from conquest.character_context import state_path
from contextlib import contextmanager
import json
from pathlib import Path
import threading
import uuid


MODES = (
    "observe",
    "post-hover",
    "send-hover",
    "cancel-hover",
    "park-observe",
    "park-post-hover",
    "park-send-hover",
    "park-cancel-hover",
    "barrier-hover",
    "barrier-burst-hover",
    "park-barrier-hover",
    "park-barrier-burst-hover",
    "park-barrier-click",
    "park-ctrl",
    "park-drag-cancel",
    "park-price-cancel",
)

# Qualification stopped after an interrupted drag left its source item missing.
# Keep diagnostic implementations reviewable, but expose no live input bypass.
READ_ONLY_MODES = ("observe", "park-observe")


def validate_live_mode(mode):
    if mode not in MODES:
        raise ValueError("Unknown background diagnostic mode")
    if mode not in READ_ONLY_MODES:
        raise ValueError(
            "Background input qualification failed interrupted-drag reconciliation; live input experiments are disabled"
        )


def probe_busy(ui):
    return getattr(ui, "background_probe", {}).get("state") in (
        "running",
        "restoring",
    ) or bool(getattr(ui, "background_surfaces", {}))


def restore_surface(surface):
    callback = getattr(surface, "before_restore", None)
    if callback:
        callback()
    surface.restore()


@contextmanager
def restoration_lease(ui):
    """Window recovery honors external input owners, including while stopped."""
    coordinator = ui.coordinator
    if not coordinator.lock.acquire(blocking=False):
        raise ValueError("Another input action is running; restoration deferred")
    file, owned = None, False
    try:
        if coordinator.owner:
            raise ValueError(
                "Another input action owns the client; restoration deferred"
            )
        import msvcrt

        coordinator.path.parent.mkdir(parents=True, exist_ok=True)
        file = coordinator.path.open("a+b")
        file.seek(0, 2)
        if file.tell() == 0:
            file.write(b"0")
            file.flush()
        file.seek(0)
        msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
        coordinator.owner, coordinator.thread = (
            "Background restoration",
            threading.get_ident(),
        )
        owned = True
        yield
    finally:
        if owned:
            coordinator.owner = coordinator.thread = None
        if file:
            file.close()
        coordinator.lock.release()


@contextmanager
def reserved_surface(target, identity, mode, *, ui=None, character=None):
    surface = None
    try:
        if mode.startswith("park-"):
            from conquest.background_surface import BackgroundSurface

            surface = BackgroundSurface()
            if ui is not None:
                ui.background_surfaces[character] = surface
            surface.park(
                target.hwnd, identity, expected_size=target.snapshot()["client_size"]
            )
        yield surface
    finally:
        if surface is not None:
            try:
                restore_surface(surface)
            finally:
                # A failed restoration must retain its exact original state.
                # Do not allow ordinary embedding to replace the saved owner.
                if ui is not None and surface.saved is None:
                    ui.background_surfaces.pop(character, None)


def start_surface_restore(ui, *, on_complete=None):
    """Retry retained window recovery off Tk, with no gameplay input."""
    if getattr(ui, "background_probe", {}).get("state") in ("running", "restoring"):
        ui.background_cancel.set()
        raise ValueError(
            "Background diagnostic is stopping; wait for client restoration"
        )
    if not getattr(ui, "background_surfaces", {}):
        if on_complete:
            ui.ui_requests.put((on_complete, None, {}))
        return {"restored": True}
    ui.background_probe = {**ui.background_probe, "state": "restoring"}

    def work():
        error = None
        try:
            with restoration_lease(ui):
                for character, surface in list(ui.background_surfaces.items()):
                    restore_surface(surface)
                    ui.background_surfaces.pop(character, None)
        except Exception as failure:
            error = (
                str(failure)
                if isinstance(failure, (ValueError, OSError))
                else type(failure).__name__
            )
        ui.background_probe = {
            **ui.background_probe,
            "state": "restoration_required" if ui.background_surfaces else "complete",
            "restoration_error": error,
        }
        if not ui.background_surfaces and on_complete:
            ui.ui_requests.put((on_complete, None, {}))

    thread = threading.Thread(
        target=work, name="background-surface-restore", daemon=True
    )
    ui.background_thread = thread
    thread.start()
    return {"restoration_requested": True}


@contextmanager
def diagnostic_lease(ui, character):
    """Exclude existing input without invoking foreground handoff callbacks."""
    coordinator = ui.coordinator
    if not coordinator.lock.acquire(blocking=False):
        raise ValueError("Another input action is running")
    file = None
    owned = False
    try:
        if (
            coordinator.owner
            or coordinator.stopped
            or not ui.safe_to_yield()
            or ui.calibrating
            or ui.runtime.refilling
            or any(
                ui.runtime.enabled(c) or ui.runtime.journal.pending(c)
                for c in ui.runtime.controllers
            )
        ):
            raise ValueError(
                "Background diagnostics require idle, paused operations and reconciled stock"
            )
        import msvcrt

        coordinator.path.parent.mkdir(parents=True, exist_ok=True)
        file = coordinator.path.open("a+b")
        if file.tell() == 0:
            file.write(b"0")
            file.flush()
        file.seek(0)
        msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
        coordinator.owner, coordinator.thread = character, threading.get_ident()
        owned = True
        yield
    finally:
        if owned:
            coordinator.owner = coordinator.thread = None
        if file:
            file.close()
        coordinator.lock.release()


def run_probe(ui, character, mode, cancel):
    validate_live_mode(mode)
    import win32gui as gui
    import win32process
    import os
    from conquest.window_host import HostApi

    HostApi()
    app_windows = []

    def collect_app_window(hwnd, _):
        if (
            win32process.GetWindowThreadProcessId(hwnd)[1] == os.getpid()
            and gui.GetClassName(hwnd) == "TkTopLevel"
            and gui.GetWindowText(hwnd).startswith("Conquest")
        ):
            app_windows.append(hwnd)

    gui.EnumWindows(collect_app_window, None)
    if len(app_windows) != 1:
        raise ValueError("Conquest native window identity is ambiguous")
    observer = ui.runtime.observers[character]
    driver = ui.runtime.controllers[character].driver
    target = driver.target
    with (
        diagnostic_lease(ui, character),
        observer.lock,
        reserved_surface(
            target, observer.adapter.identity, mode, ui=ui, character=character
        ),
    ):
        before = driver.read()
        if before.get("request") or before.get("trade"):
            raise ValueError("Trade must finish before background diagnostics")
        target.snapshot()
        gui.GetWindow(target.hwnd, 4)
        # The background input evidence reader was pinned to the retired 1074
        # renderer. No build has a qualified layout, so the diagnostic stops
        # here -- after the same lease and surface handling -- as it always
        # did on any other build.
        raise ValueError("Unqualified merchant client fingerprint")


def start_probe(ui, character, mode):
    validate_live_mode(mode)
    if probe_busy(ui):
        raise ValueError("A background diagnostic is already running")
    if character not in ui.runtime.observers:
        raise ValueError("Merchant is not connected")
    cancel = threading.Event()
    if not hasattr(ui, "background_surfaces"):
        ui.background_surfaces = {}
    ui.background_cancel = cancel
    probe_id = uuid.uuid4().hex
    ui.background_probe = {
        "id": probe_id,
        "state": "running",
        "character": character,
        "mode": mode,
    }

    def work():
        try:
            report = run_probe(ui, character, mode, cancel)
        except Exception as error:
            report = {
                **getattr(error, "probe_report", {}),
                "character": character,
                "mode": mode,
                "outcome": "stopped",
                "error": str(error)
                if isinstance(error, (ValueError, OSError))
                else type(error).__name__,
            }
            from conquest.merchants.ui import calibration_failure

            report["diagnostic"] = calibration_failure(error)["diagnostic"]
        path = Path(state_path("reports/merchants/background")) / (probe_id + ".json")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        except OSError:
            report["report_error"] = "Could not save diagnostic report"
        finally:
            ui.background_probe = {
                "id": probe_id,
                "state": "restoration_required"
                if ui.background_surfaces
                else "complete",
                "report": str(path),
                **{k: v for k, v in report.items() if k not in ("samples", "state")},
            }

    ui.background_thread = threading.Thread(
        target=work, name="background-input-probe", daemon=True
    )
    ui.background_thread.start()
    return {"requested": probe_id}
