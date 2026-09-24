from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock
import subprocess
import sys
import threading

import pytest

from conquest.merchants.background_probe import (
    BackgroundCleanupRequired,
    cleanup_probe,
    settle_input,
    restore_surface,
    restoration_lease,
    reconciliation_read,
)


def state(frame, *, queued=0, down=False):
    return {
        "frame": frame,
        "context": 0x10000,
        "queue": {"size": queued},
        "backend": {"buttons_down": int(down)},
        "mouse_down": [down] * 5,
        "active": {"id": 0},
        "modifiers": {"ctrl": False},
        "key_mods": 0,
    }


def reader_states(states):
    values = iter(states)
    latest = [states[0]]

    def snapshot():
        latest[0] = next(values, latest[0])
        return deepcopy(latest[0])

    return SimpleNamespace(
        snapshot=snapshot,
        session=SimpleNamespace(read_block=lambda *a: bytes(0x285 * 16)),
    )


def settle(reader, **kwargs):
    now = [0]

    def sleep(seconds):
        now[0] += seconds

    return settle_input(
        reader, timeout=0.1, clock=lambda: now[0], sleep=sleep, **kwargs
    )


def test_neutral_old_snapshot_does_not_pass_before_queued_down_and_release():
    reader = reader_states(
        [
            state(10),
            state(10),
            state(11, queued=3),
            state(12, down=True),
            state(13),
            state(14),
        ]
    )
    assert settle(reader)["frames"] == [13, 14]


@pytest.mark.parametrize(
    "states",
    [
        [state(10)] * 20,
        [state(x, queued=1) for x in range(20)],
        [state(x, down=True) for x in range(20)],
    ],
)
def test_frozen_or_pending_input_does_not_qualify_release(states):
    with pytest.raises(ValueError, match="did not settle"):
        settle(reader_states(states))


def test_ctrl_cleanup_requires_all_target_key_down_flags_clear():
    reader = reader_states([state(x) for x in range(20)])
    reader.session.read_block = lambda *a: b"\x01" + bytes(0x285 * 16 - 1)
    with pytest.raises(ValueError, match="did not settle"):
        settle(reader, include_keys=True)


def test_frame_reset_is_uncertain():
    with pytest.raises(ValueError, match="reset"):
        settle(reader_states([state(10), state(9)]))


def snapshots():
    return {
        "identity": {"pid": 7},
        "inventory": [],
        "booth": [],
        "silver": 100,
        "position": [232, 213],
    }


def test_failed_input_still_reads_final_stock_and_keeps_surface_parked(monkeypatch):
    monkeypatch.setattr(
        "conquest.merchants.background_probe.settle_input",
        Mock(side_effect=ValueError("queued input remains")),
    )
    journal = Mock()
    ui = SimpleNamespace(
        coordinator=SimpleNamespace(stop=Mock()),
        runtime=SimpleNamespace(journal=journal),
    )
    report = {
        "button_messages_sent": True,
        "outcome": "stopped",
        "error": "manual cancellation",
    }
    driver = SimpleNamespace(read=Mock(return_value=snapshots()))
    surface = SimpleNamespace(
        restore=Mock(),
        before_restore=lambda: cleanup_probe(
            ui, "Dutch", report, object(), driver, snapshots()
        ),
    )
    with pytest.raises(BackgroundCleanupRequired) as error:
        restore_surface(surface)
    assert error.value.probe_report is report
    driver.read.assert_called_once()
    surface.restore.assert_not_called()
    ui.coordinator.stop.assert_called_once()
    assert report["final_reconciliation"]["available"] is True
    assert report["input_cleanup"]["settled"] is False
    assert report["release_verified"] is False
    journal.set.assert_called_once()


def test_changed_stock_is_not_reported_as_success_or_emergency_input_stop(monkeypatch):
    monkeypatch.setattr(
        "conquest.merchants.background_probe.settle_input",
        lambda *a, **k: {"settled": True},
    )
    ui = SimpleNamespace(
        coordinator=SimpleNamespace(stop=Mock()),
        runtime=SimpleNamespace(journal=Mock()),
    )
    before = snapshots()
    after = {**before, "silver": 200}
    report = {"input_messages_sent": True, "outcome": "observed"}
    cleanup_probe(
        ui, "Dutch", report, object(), SimpleNamespace(read=lambda: after), before
    )
    assert report["outcome"] == "stopped" and report["stock_unchanged"] is False
    ui.coordinator.stop.assert_not_called()


def test_failed_read_is_unknown_even_if_old_success_evidence_exists():
    ui = SimpleNamespace(
        coordinator=SimpleNamespace(stop=Mock()),
        runtime=SimpleNamespace(journal=Mock()),
    )
    report = {
        "stock_unchanged": True,
        "position_unchanged": True,
        "outcome": "observed",
    }
    cleanup_probe(
        ui,
        "Dutch",
        report,
        object(),
        SimpleNamespace(read=Mock(side_effect=ValueError("disconnected"))),
        snapshots(),
    )
    assert report["stock_unchanged"] is report["position_unchanged"] is None
    assert report["final_reconciliation"]["available"] is False
    assert report["outcome"] == "stopped"


def test_final_reconciliation_retries_gui_races_without_repeating_input():
    from conquest.merchants.memory import GuiObservationChanged

    now = [0.0]

    def sleep(seconds):
        now[0] += seconds

    driver = SimpleNamespace(
        read=Mock(
            side_effect=[
                ValueError("Invalid GUI geometry"),
                GuiObservationChanged("GUI registry changed"),
                snapshots(),
            ]
        )
    )
    result, attempts = reconciliation_read(driver, clock=lambda: now[0], sleep=sleep)
    assert result == snapshots() and attempts == 3
    assert now[0] == pytest.approx(0.05)


def test_persistent_gui_geometry_failure_remains_unknown_after_half_second():
    now = [0.0]

    def sleep(seconds):
        now[0] += seconds

    driver = SimpleNamespace(read=Mock(side_effect=ValueError("Invalid GUI geometry")))
    with pytest.raises(ValueError, match="Invalid GUI geometry"):
        reconciliation_read(driver, clock=lambda: now[0], sleep=sleep)
    assert now[0] == pytest.approx(0.5)
    assert driver.read.call_count <= 22


@pytest.mark.parametrize(
    "error",
    [
        ValueError("Process identity changed"),
        ValueError("GUI frame counter reset during observation"),
        OSError("Memory read failed"),
    ],
)
def test_non_race_reconciliation_errors_are_not_retried(error):
    driver = SimpleNamespace(read=Mock(side_effect=error))
    sleep = Mock()
    with pytest.raises(type(error), match=str(error)):
        reconciliation_read(driver, sleep=sleep)
    driver.read.assert_called_once()
    sleep.assert_not_called()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows process byte lock")
def test_restoration_respects_separate_process_lock_and_still_works_when_stopped(
    tmp_path,
):
    path = tmp_path / "input.lock"
    code = """import msvcrt,sys
with open(sys.argv[1],'w+b') as f:
 f.write(b'0');f.flush();f.seek(0)
 msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
 print('ready',flush=True)
 sys.stdin.readline()
"""
    child = subprocess.Popen(
        [sys.executable, "-c", code, str(path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    ui = SimpleNamespace(
        coordinator=SimpleNamespace(
            lock=threading.RLock(), owner=None, thread=None, path=path, stopped=True
        )
    )
    try:
        assert child.stdout.readline().strip() == "ready"
        with pytest.raises(OSError):
            with restoration_lease(ui):
                pytest.fail("Foreign lease was ignored")
        assert ui.coordinator.owner is None
    finally:
        child.communicate("\n", timeout=5)
    with restoration_lease(ui):
        assert ui.coordinator.owner == "Background restoration"
        assert ui.coordinator.stopped is True
    assert ui.coordinator.owner is None
