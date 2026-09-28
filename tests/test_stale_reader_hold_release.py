"""A reader hold from a dead game process is settled once a newer process
shows no trade and no request.

Live 2026-09-27 (Toxic): the client died in a GPU reset at 22:10:34 ("Memory
session is closed"), the reconnect logged a new one in at 22:11:41 and the
stale hold fenced every town action until an operator override. At 23:13
Defender killed the client ("Process 570848 exited during diagnostics") and
the same happened.
"""

from types import SimpleNamespace as NS

import pytest

from conquest.merchants.manual_farmer import (
    FILETIME_EPOCH,
    release_hold_from_dead_process,
    release_hold_on_user_farming_on,
)

HOLD_AT = 1790561434.69  # 22:10:34
NEW_PROCESS = int((1790561501.4 + FILETIME_EPOCH) * 1e7)  # 22:11:41
OLD_PROCESS = int((1790520000.0 + FILETIME_EPOCH) * 1e7)  # the day before


def runtime_with(reason):
    calls = []
    hold = {
        "id": "unbound:toxic",
        "reason": reason,
        "created_at": HOLD_AT,
        "holds_automation": True,
    }
    runtime = NS(
        _manual_get=lambda character, name: hold if name == "manual_reader_hold" else None,
        override_manual=lambda session_id, **kw: calls.append((session_id, kw)),
        manual_farmer_observation={},
    )
    return runtime, calls


def snapshot(created=NEW_PROCESS, trade=None, request=None, at=1790561718.0, pid=591900):
    return {
        "identity": {"pid": pid, "creation_time_100ns": created},
        "trade": trade,
        "request": request,
        "timestamp": at,
    }


@pytest.mark.parametrize(
    "reason",
    [
        "Farmer manual memory unavailable: Memory session is closed",
        "Farmer manual memory unavailable: Process 570848 exited during diagnostics",
    ],
)
def test_a_new_process_without_a_trade_settles_the_dead_processes_hold(reason):
    runtime, calls = runtime_with(reason)
    assert release_hold_from_dead_process(runtime, snapshot(), now=1790561718.5)
    assert calls[0][0] == "unbound:toxic"
    assert calls[0][1]["operator"] == "automatic (new game process)"
    assert runtime.manual_farmer_observation["stale_hold_released"] == "unbound:toxic"


@pytest.mark.parametrize(
    "reason, snap, now",
    [
        # A trade reader failure in a live process still needs an operator.
        ("Farmer manual memory unavailable: 1078 trade silver is not a proved numeric value", snapshot(), 1790561718.5),
        # The same process that held the session: it may still hold a trade.
        ("Farmer manual memory unavailable: Memory session is closed", snapshot(created=OLD_PROCESS), 1790561718.5),
        # Any trade or request open in the new process.
        ("Farmer manual memory unavailable: Memory session is closed", snapshot(trade={"id": 1}), 1790561718.5),
        ("Farmer manual memory unavailable: Memory session is closed", snapshot(request={"participant": "Luna"}), 1790561718.5),
        # Stale evidence.
        ("Farmer manual memory unavailable: Memory session is closed", snapshot(), 1790561730.0),
        # The process named as exited is the one now observed.
        ("Farmer manual memory unavailable: Process 591900 exited during diagnostics", snapshot(), 1790561718.5),
        # An exited process whose replacement predates the hold.
        ("Farmer manual memory unavailable: Process 570848 exited during diagnostics", snapshot(created=OLD_PROCESS), 1790561718.5),
        # Any other OS error keeps it.
        ("Farmer manual memory unavailable: OpenProcess failed: Access is denied", snapshot(), 1790561718.5),
    ],
)
def test_everything_else_keeps_the_hold(reason, snap, now):
    runtime, calls = runtime_with(reason)
    assert not release_hold_from_dead_process(runtime, snap, now=now)
    assert calls == []


# Live 2026-09-28 (Suicide, Back2Classic): a trade read failed at 17:12:53
# while Alex played by hand; his Farming On at 17:28:24 was fenced until an
# operator override at 17:30.
TRADE_HOLD_AT = 1790629973.31
FARMING_ON_AT = 1790630904.0
TRADE_READ_FAILURE = (
    "Farmer manual memory unavailable: 1078 trade silver is not a proved numeric value"
)


def farmer_runtime(pressed):
    calls = []
    hold = {
        "id": "unbound:suicide",
        "reason": TRADE_READ_FAILURE,
        "created_at": TRADE_HOLD_AT,
        "holds_automation": True,
    }
    runtime = NS(
        _manual_get=lambda character, name: hold
        if (character, name) == ("Farmer", "manual_reader_hold")
        else None,
        override_manual=lambda session_id, **kw: calls.append((session_id, kw)),
        manual_farmer_observation={},
    )
    if pressed is not None:
        runtime.user_farming_on_at = pressed
    return runtime, calls


def live_snapshot(server="Back2Classic", trade=None, request=None, at=FARMING_ON_AT + 0.4):
    # The same process that held the session: no restart.
    return {
        "server": server,
        "identity": {"pid": 14172, "creation_time_100ns": OLD_PROCESS},
        "trade": trade,
        "request": request,
        "timestamp": at,
    }


def test_the_users_farming_on_settles_a_live_processes_reader_hold():
    runtime, calls = farmer_runtime(FARMING_ON_AT)
    assert release_hold_on_user_farming_on(runtime, live_snapshot(), now=FARMING_ON_AT + 0.5)
    assert calls[0][0] == "unbound:suicide"
    assert calls[0][1]["operator"] == "user (Farming On)"
    assert calls[0][1]["confirmation_reference"] == f"farming-on:{FARMING_ON_AT}"
    assert runtime.manual_farmer_observation["stale_hold_released"] == "unbound:suicide"


@pytest.mark.parametrize(
    "pressed, snap, now",
    [
        # No Farming On from the user (a bridge restart never records one).
        (None, live_snapshot(), FARMING_ON_AT + 0.5),
        # Farming On before the hold: the hold came from a later trade.
        (TRADE_HOLD_AT - 60.0, live_snapshot(), FARMING_ON_AT + 0.5),
        # America: a visitor session or delivery may depend on the trade.
        (FARMING_ON_AT, live_snapshot(server="America"), FARMING_ON_AT + 0.5),
        # A trade or request still open.
        (FARMING_ON_AT, live_snapshot(trade={"id": 1}), FARMING_ON_AT + 0.5),
        (FARMING_ON_AT, live_snapshot(request={"participant": "Luna"}), FARMING_ON_AT + 0.5),
        # Stale evidence.
        (FARMING_ON_AT, live_snapshot(), FARMING_ON_AT + 10.0),
    ],
)
def test_the_farming_on_release_keeps_the_hold_otherwise(pressed, snap, now):
    runtime, calls = farmer_runtime(pressed)
    assert not release_hold_on_user_farming_on(runtime, snap, now=now)
    assert calls == []


def on_app(monkeypatch, tmp_path):
    from queue import Queue

    from conquest import storage_halt
    from conquest.control import FarmingControl
    from conquest.desktop_app import DesktopApp

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(storage_halt, "clear_by_user", lambda: None)
    monkeypatch.setattr(storage_halt, "active", lambda: False)
    app = DesktopApp.__new__(DesktopApp)
    app.control = FarmingControl()
    app.memory_text = NS(set=lambda text: None)
    app.record = lambda **fields: None
    app.update_kill_metrics = lambda action="refresh": None
    app.host = NS(saved=False)
    app.runtime = None
    app.thread = None
    app.messages = Queue()
    app.unified = NS(grant=None, coordinator=NS(resume=lambda: None), runtime=NS())
    return app


def test_the_apps_farming_on_records_the_users_press(monkeypatch, tmp_path):
    app = on_app(monkeypatch, tmp_path)
    app.update_ids(True)
    assert app.control.snapshot()["enabled"]
    assert type(app.unified.runtime.user_farming_on_at) is float


def test_a_bridge_restart_is_not_the_users_press(monkeypatch, tmp_path):
    app = on_app(monkeypatch, tmp_path)
    app.update_control({"enabled": True, "explicit_restart": True})
    assert app.control.snapshot()["enabled"]
    assert not hasattr(app.unified.runtime, "user_farming_on_at")
