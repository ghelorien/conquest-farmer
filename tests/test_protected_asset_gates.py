from queue import Queue
from types import SimpleNamespace

import pytest

from conquest import protected_withdrawal as protected
from conquest.merchants import delivery_operation as delivery


@pytest.fixture
def locked_assets(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "reports/banking/protected-withdrawals.sqlite3"
    monkeypatch.setattr(protected, "JOURNAL", path)
    journal = protected.ProtectedWithdrawalJournal(path)
    journal.begin(
        {
            "operation_id": "withdrawal-1",
            "plan_id": "qualification-1",
            "uid": 101,
            "phase": "prepared",
            "intent": {"item": {"uid": 101}},
        }
    )
    return journal


@pytest.mark.parametrize(
    "phase", ["prepared", "input_maybe_sent", "reconciling", "blocked"]
)
def test_every_unresolved_phase_blocks_reload_before_other_journal_exists(
    locked_assets, monkeypatch, tmp_path, phase
):
    if phase != "prepared":
        locked_assets.transition("withdrawal-1", "prepared", "input_maybe_sent")
    if phase in ("reconciling", "blocked"):
        locked_assets.transition("withdrawal-1", "input_maybe_sent", phase)
    missing = tmp_path / "missing-source.sqlite3"
    monkeypatch.setattr(delivery, "JOURNAL", missing)
    with pytest.raises(ValueError, match="Protected warehouse withdrawal"):
        delivery.guard_reload()
    assert not missing.exists()


def test_terminal_no_transfer_releases_reload_gate(
    locked_assets, monkeypatch, tmp_path
):
    locked_assets.transition(
        "withdrawal-1", "prepared", "no_transfer", receipt={"input_attempted": False}
    )
    monkeypatch.setattr(delivery, "JOURNAL", tmp_path / "missing-source.sqlite3")
    delivery.guard_reload()


def test_malformed_protected_journal_cannot_be_ignored(monkeypatch, tmp_path):
    path = tmp_path / "broken.sqlite3"
    path.write_bytes(b"not a sqlite database")
    monkeypatch.setattr(protected, "JOURNAL", path)
    with pytest.raises(ValueError, match="inventory reconciliation"):
        delivery.guard_protected_assets()


def test_ui_on_and_bridge_on_reject_before_changing_intent_or_clearing_halt(
    locked_assets, monkeypatch
):
    from conquest.control import FarmingControl
    from conquest.desktop_app import DesktopApp
    from conquest import storage_halt

    app = DesktopApp.__new__(DesktopApp)
    app.control = FarmingControl()
    app.memory_text = SimpleNamespace(set=lambda text: notes.append(text))
    notes = []
    records = []
    app.record = lambda **fields: records.append(fields)
    monkeypatch.setattr(
        storage_halt,
        "clear_by_user",
        lambda: pytest.fail("On cleared halt before asset lock check"),
    )
    previous = app.control.snapshot()
    app.update_ids(True)
    assert app.control.snapshot() == previous and not records
    assert notes and "inventory reconciliation" in notes[0]
    with pytest.raises(ValueError, match="inventory reconciliation"):
        app.update_control({"enabled": True})
    assert app.control.snapshot() == previous


def test_manual_off_still_works_with_asset_lock(locked_assets):
    from conquest.control import FarmingControl
    from conquest.desktop_app import DesktopApp

    app = DesktopApp.__new__(DesktopApp)
    app.control = FarmingControl()
    app.control.update({"enabled": True})
    app.messages = Queue()
    app.thread = None
    result = app.update_control({"enabled": False})
    assert not result["enabled"]
    assert app.messages.get_nowait()[0] == "control_intent"


def test_runner_cannot_resume_old_intent_before_runtime_exists(locked_assets):
    from conquest.control import FarmingControl
    from conquest.desktop_app import DesktopApp

    app = DesktopApp.__new__(DesktopApp)
    app.control = FarmingControl()
    app.runtime = None
    app.control.update({"enabled": True})
    records = []
    app.record = lambda **fields: records.append(fields)
    assert app.start_embedded_farm() is False
    assert "inventory reconciliation" in app.control.snapshot()["note"]
    assert records[-1]["state"] == "Farming could not start"


def test_route_launch_and_resume_leave_locked_items_stationary(
    locked_assets, monkeypatch, tmp_path
):
    from conquest import route_controller
    from conquest.overnight import OvernightLoop

    for name in ("src/conquest", "profiles/routes", "scripts"):
        (tmp_path / name).mkdir(parents=True)
    (tmp_path / "pyproject.toml").touch()
    (tmp_path / "scripts/run_overnight.py").touch()
    marker = tmp_path / ".runtime/overnight.stop"
    marker.parent.mkdir()
    marker.write_text("user stop")
    monkeypatch.setattr(
        route_controller.subprocess,
        "Popen",
        lambda *a, **kw: pytest.fail("spawned a route"),
    )
    assert route_controller.ensure_running("bandit", root=tmp_path) is False
    assert marker.read_text() == "user stop"
    loop = OvernightLoop.__new__(OvernightLoop)
    loop.living = lambda: pytest.fail("began route movement/resume")
    with pytest.raises(ValueError, match="inventory reconciliation"):
        loop._run_route()


def test_new_deliveries_cannot_use_locked_assets_but_status_remains_read_only(
    locked_assets, monkeypatch, tmp_path
):
    monkeypatch.setattr(delivery, "JOURNAL", tmp_path / "delivery.sqlite3")
    ui = SimpleNamespace()
    for action in ("delivery-start", "delivery-test"):
        with pytest.raises(ValueError, match="inventory reconciliation"):
            delivery.dispatch(
                ui,
                {
                    "action": action,
                    "request_id": "other-operation",
                    "character": "Dutch",
                    "uids": [101],
                },
            )
    assert not delivery.JOURNAL.exists()
    result = delivery.dispatch(
        ui, {"action": "delivery-status", "request_id": "other-operation"}
    )
    assert result["receipt"] is None and result["running"] is False
