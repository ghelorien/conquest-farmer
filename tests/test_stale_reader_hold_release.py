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
