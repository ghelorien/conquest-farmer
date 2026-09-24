import sys
import threading
from types import SimpleNamespace
from unittest.mock import Mock
import pytest

from conquest.merchants.background_probe import (
    diagnostic_lease,
    start_probe,
    reserved_surface,
    start_surface_restore,
)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows input lock")
def test_diagnostic_lease_excludes_existing_input_without_focus_or_mouse_idle(tmp_path):
    coordinator = SimpleNamespace(
        lock=threading.RLock(),
        owner=None,
        thread=None,
        stopped=False,
        path=tmp_path / "input.lock",
        on_acquire=Mock(side_effect=AssertionError("No focus callback")),
        manual_active=Mock(
            side_effect=AssertionError("Unrelated mouse activity is permitted")
        ),
    )
    ui = SimpleNamespace(
        coordinator=coordinator,
        safe_to_yield=lambda: True,
        calibrating=set(),
        runtime=SimpleNamespace(
            refilling={},
            controllers={"Spiritual": object()},
            enabled=lambda c: False,
            journal=SimpleNamespace(pending=lambda c: []),
        ),
    )
    with pytest.raises(RuntimeError, match="probe failed"):
        with diagnostic_lease(ui, "Spiritual"):
            assert coordinator.owner == "Spiritual"
            with pytest.raises(ValueError, match="idle"):
                with diagnostic_lease(ui, "Dutch"):
                    pass
            raise RuntimeError("probe failed")
    assert coordinator.owner is coordinator.thread is None
    coordinator.on_acquire.assert_not_called()
    coordinator.manual_active.assert_not_called()
    with diagnostic_lease(ui, "Dutch"):
        assert coordinator.owner == "Dutch"
    coordinator.stopped = True
    with pytest.raises(ValueError, match="idle"):
        with diagnostic_lease(ui, "Spiritual"):
            pass


def test_probe_mode_is_bounded_before_any_runtime_access():
    with pytest.raises(ValueError, match="Unknown"):
        start_probe(object(), "Spiritual", "arbitrary-input")


@pytest.mark.parametrize(
    "mode",
    [
        "post-hover",
        "park-barrier-click",
        "park-ctrl",
        "park-drag-cancel",
        "park-price-cancel",
    ],
)
def test_failed_qualification_blocks_live_input_before_runtime_access(mode):
    from conquest.merchants.background_probe import run_probe

    with pytest.raises(ValueError, match="live input experiments are disabled"):
        start_probe(object(), "Dutch", mode)
    with pytest.raises(ValueError, match="live input experiments are disabled"):
        run_probe(object(), "Dutch", mode, threading.Event())


def test_running_probe_cannot_be_replaced():
    with pytest.raises(ValueError, match="already running"):
        start_probe(
            SimpleNamespace(background_probe={"state": "running"}), "Dutch", "observe"
        )


def test_failed_surface_restore_is_retained_and_can_be_retried(monkeypatch, tmp_path):
    class Surface:
        def __init__(self):
            self.saved = None
            self.fail = True

        def park(self, *args, **kwargs):
            self.saved = object()

        def restore(self):
            if self.fail:
                raise ValueError("Native restore refused")
            self.saved = None

    monkeypatch.setattr("conquest.background_surface.BackgroundSurface", Surface)
    ui = SimpleNamespace(background_surfaces={})
    target = SimpleNamespace(hwnd=20, snapshot=lambda: {"client_size": [1036, 793]})
    with pytest.raises(ValueError, match="restore refused"):
        with reserved_surface(target, {}, "park-observe", ui=ui, character="Dutch"):
            pass
    assert ui.background_surfaces["Dutch"].saved is not None
    surface = ui.background_surfaces["Dutch"]
    surface.fail = False
    ui.background_probe = {"state": "restoration_required"}
    ui.coordinator = SimpleNamespace(
        lock=threading.RLock(), owner=None, path=tmp_path / "input.lock"
    )
    ui.ui_requests = Mock()
    monkeypatch.setattr(
        "conquest.merchants.background_probe.threading.Thread",
        lambda *, target, **kwargs: SimpleNamespace(start=target),
    )
    callback = Mock()
    start_surface_restore(ui, on_complete=callback)
    assert not ui.background_surfaces and ui.background_probe["state"] == "complete"
    callback.assert_not_called()  # GUI callback must execute through the UI queue.
    ui.ui_requests.put.assert_called_once_with((callback, None, {}))


def test_restore_failure_keeps_recovery_record_and_never_calls_completion(
    monkeypatch, tmp_path
):
    ui = SimpleNamespace(
        background_surfaces={
            "Dutch": SimpleNamespace(
                restore=Mock(side_effect=ValueError("manual takeover"))
            )
        },
        background_probe={"state": "restoration_required"},
        coordinator=SimpleNamespace(
            lock=threading.RLock(), owner=None, path=tmp_path / "input.lock"
        ),
        ui_requests=Mock(),
    )
    monkeypatch.setattr(
        "conquest.merchants.background_probe.threading.Thread",
        lambda *, target, **kwargs: SimpleNamespace(start=target),
    )
    start_surface_restore(ui, on_complete=Mock())
    assert ui.background_probe["state"] == "restoration_required"
    assert ui.background_probe["restoration_error"] == "manual takeover"
    assert "Dutch" in ui.background_surfaces
    ui.ui_requests.put.assert_not_called()


def test_partial_park_error_still_restores_and_releases_reservation(monkeypatch):
    class Surface:
        def __init__(self):
            self.saved = None

        def park(self, *args, **kwargs):
            self.saved = object()
            raise ValueError("Native acknowledgement missing")

        def restore(self):
            self.saved = None

    monkeypatch.setattr("conquest.background_surface.BackgroundSurface", Surface)
    ui = SimpleNamespace(background_surfaces={})
    target = SimpleNamespace(hwnd=20, snapshot=lambda: {"client_size": [1036, 793]})
    with pytest.raises(ValueError, match="acknowledgement"):
        with reserved_surface(target, {}, "park-observe", ui=ui, character="Dutch"):
            pass
    assert not ui.background_surfaces


@pytest.mark.parametrize("state", ["running", "restoration_required"])
def test_ui_close_defers_until_window_can_be_restored(state):
    from conquest.merchants.ui import UnifiedUI

    ui = SimpleNamespace(
        background_probe={"state": state},
        background_surfaces={"Dutch": object()}
        if state == "restoration_required"
        else {},
        defer_background_action=Mock(),
        closed=False,
        app=SimpleNamespace(close=Mock(), restart=Mock(), state_text=Mock()),
    )
    assert UnifiedUI.close(ui) is False
    assert ui.closed is False
    ui.defer_background_action.assert_called_once_with(ui.app.close)
    ui.app.close.assert_not_called()


def test_explicit_embed_waits_while_periodic_layout_cannot_touch_parked_window():
    from conquest.merchants.ui import UnifiedUI

    ui = SimpleNamespace(
        background_probe={"state": "restoration_required"},
        background_surfaces={"Dutch": object()},
        defer_background_action=Mock(),
    )
    UnifiedUI.resize_merchant(ui, "Dutch")  # No access to hosts or native APIs.
    UnifiedUI.embed_client(ui, "Dutch")
    ui.defer_background_action.assert_called_once()
    with pytest.raises(ValueError, match="restoration"):
        UnifiedUI.embed_merchant(ui, "Dutch")
    with pytest.raises(ValueError, match="restoration"):
        UnifiedUI.show_merchant(ui, "Dutch")
