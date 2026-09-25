"""Isolated failure modes for the farm event journal and the desktop runner.

The end-to-end path (real runner + run_trial + real SQLite + concurrent reader)
is tests/test_farm_journal_lock_e2e.py. These isolate the journal's storage
edges and the runner's outcome mapping. Failure modes, written before the fix:

Journal (conquest.event_journal.EventJournal, used by run_trial's event()):
 J1 A reader holds the shared lock: record() raises, blocks combat for the full
    busy timeout on every event, drops/duplicates/reorders queued events, or
    rewrites their original times once the lock clears.
 J2 The lock lasts past close(): queued events are lost instead of kept in a
    durable trial-pending-*.jsonl next to the DB; the next start imports that
    file zero or two times.
 J3 A crash (or AV lock) between the import commit and deleting the pending
    file re-imports it at the following start (double-counted kills).
 J4 The DB is still locked at the next start: opening the journal fails, or the
    old pending file is lost or imported out of order with newer pending rows.
 J5 A non-lock storage error (disk I/O) is swallowed as if transient (strict
    behaviour lost), or the event that hit it is dropped.
 J6 Lock/busy errors are not distinguished from other OperationalErrors.

Runner (DesktopApp.run_embedded_farm):
 R1 An unexpected exception is finished as "requested_stop" (Farming Off) or
    keeps an earlier trial's "duration_limit" reason (both live incidents)
    instead of a runner failure with its own reason and the error detail,
    leaving Farming On so the route records needs_attention (Discord STOPPED).
 R2 A transient lock error escaping run_trial stops farming instead of
    restarting the trial loop in place, or restarts without bound.
 R3 User Stop / F12 / control change outcomes change.
"""

import queue
import sqlite3
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from conquest import desktop_app, native_farm
from conquest.control import FarmingControl


def rows(directory):
    with sqlite3.connect(directory / "trial.sqlite3") as db:
        return db.execute("SELECT rowid,time,event,payload FROM events").fetchall()


def sidecars(directory):
    return sorted(directory.glob("trial-pending-*.jsonl"))


class Reader:
    """A real second connection holding SHARED (like a slow kill scan)."""

    def __init__(self, directory):
        self.database = directory / "trial.sqlite3"
        self.locked, self.release, self.done = (threading.Event() for _ in range(3))

    def __enter__(self):
        def run():
            db = sqlite3.connect(
                self.database.resolve().as_uri() + "?mode=ro", uri=True
            )
            try:
                db.execute("BEGIN")
                db.execute("SELECT count(*) FROM events").fetchone()
                self.locked.set()
                self.release.wait(60)
                db.rollback()
            finally:
                db.close()
                self.done.set()

        threading.Thread(target=run, daemon=True).start()
        assert self.locked.wait(10)
        return self

    def __exit__(self, *exc):
        self.release.set()
        assert self.done.wait(10)


def journal(directory, **kwargs):
    from conquest.event_journal import EventJournal

    return EventJournal(directory, **kwargs)


def test_j1_lock_queues_without_blocking_combat_and_flushes_in_order(tmp_path):
    events = journal(tmp_path)
    events.record("before", "{}")
    with Reader(tmp_path):
        began = time.perf_counter()
        for number in range(20):
            events.record("kill_verified", f'{{"n": {number}}}')
        elapsed = time.perf_counter() - began
        # One bounded busy wait, then queued: not 20 full timeouts.
        assert elapsed < 4.0
        assert events.pending == 20
        assert [r[2] for r in rows(tmp_path)] == ["before"]
        assert len(sidecars(tmp_path)) == 1
    # Windows time.time() can tick only every ~15.6 ms; let it advance so a
    # re-stamped flush would be distinguishable from the kills' own times.
    time.sleep(0.05)
    events.record("after", "{}")
    assert events.close() == 0
    written = rows(tmp_path)
    names = [r[2] for r in written]
    assert names == ["before"] + ["kill_verified"] * 20 + [
        "after",
        "event_journal_recovered",
    ]
    assert [r[3] for r in written[1:21]] == [f'{{"n": {n}}}' for n in range(20)]
    times = [r[1] for r in written]
    assert times == sorted(times)
    # Original record times are kept (the kills happened under the lock).
    assert written[20][1] < written[-1][1]
    assert sidecars(tmp_path) == []


def test_j2_lock_past_close_is_durable_and_imported_exactly_once(tmp_path):
    first = journal(tmp_path)
    first.record("trial_started", "{}")
    with Reader(tmp_path):
        for number in range(3):
            first.record("kill_verified", f'{{"n": {number}}}')
        assert first.close() == 3
    assert len(sidecars(tmp_path)) == 1
    assert [r[2] for r in rows(tmp_path)] == ["trial_started"]
    second = journal(tmp_path)
    second.record("trial_started", "{}")
    second.close()
    third = journal(tmp_path)
    third.record("trial_started", "{}")
    third.close()
    payloads = [r[3] for r in rows(tmp_path) if r[2] == "kill_verified"]
    assert payloads == ['{"n": 0}', '{"n": 1}', '{"n": 2}']
    assert sidecars(tmp_path) == []


def test_j3_failed_pending_file_removal_never_reimports(tmp_path, monkeypatch):
    first = journal(tmp_path)
    first.record("trial_started", "{}")
    with Reader(tmp_path):
        first.record("kill_verified", '{"n": 0}')
        first.record("kill_verified", '{"n": 1}')
        first.close()
    original = Path.unlink

    def locked_unlink(self, *args, **kwargs):
        if self.name.startswith("trial-pending-"):
            raise PermissionError("pending file is open elsewhere")
        return original(self, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", locked_unlink)
        second = journal(tmp_path)
        second.record("trial_started", "{}")
        second.close()
    # Committed, but the imported file and the second journal's own pending
    # file (its first row joined the backlog) are both still on disk.
    assert len(sidecars(tmp_path)) == 2
    third = journal(tmp_path)
    third.record("trial_started", "{}")
    third.close()
    assert sidecars(tmp_path) == []
    payloads = [r[3] for r in rows(tmp_path) if r[2] == "kill_verified"]
    assert payloads == ['{"n": 0}', '{"n": 1}']
    assert [r[2] for r in rows(tmp_path)].count("trial_started") == 3


def test_j4_still_locked_at_next_start_keeps_every_pending_row_in_order(tmp_path):
    first = journal(tmp_path)
    first.record("trial_started", "{}")
    with Reader(tmp_path):
        first.record("kill_verified", '{"n": 0}')
        first.close()
        second = journal(tmp_path)  # must open under the reader's lock
        second.record("kill_verified", '{"n": 1}')
        assert second.close() == 2
        assert len(sidecars(tmp_path)) == 2
    third = journal(tmp_path)
    third.record("kill_verified", '{"n": 2}')
    third.close()
    payloads = [r[3] for r in rows(tmp_path) if r[2] == "kill_verified"]
    assert payloads == ['{"n": 0}', '{"n": 1}', '{"n": 2}']
    assert sidecars(tmp_path) == []


def test_j5_disk_error_stays_strict_but_keeps_the_event(tmp_path):
    class Failing:
        def __init__(self, db):
            self.db = db

        def execute(self, sql, *args):
            if sql.startswith("INSERT"):
                raise sqlite3.OperationalError("disk I/O error")
            return self.db.execute(sql, *args)

        def executemany(self, sql, *args):
            if sql.startswith("INSERT"):
                raise sqlite3.OperationalError("disk I/O error")
            return self.db.executemany(sql, *args)

        def __getattr__(self, name):
            return getattr(self.db, name)

    failing = journal(
        tmp_path, connect=lambda *a, **k: Failing(sqlite3.connect(*a, **k))
    )
    with pytest.raises(sqlite3.OperationalError, match="disk I/O error"):
        failing.record("kill_verified", '{"n": 0}')
    assert failing.pending == 1
    failing.close()  # the event is already durable; close never masks the error
    assert len(sidecars(tmp_path)) == 1
    later = journal(tmp_path)
    later.record("trial_started", "{}")
    later.close()
    assert [r[3] for r in rows(tmp_path) if r[2] == "kill_verified"] == ['{"n": 0}']


def test_j6_only_lock_and_busy_errors_are_transient(tmp_path):
    from conquest.event_journal import transient_lock

    assert transient_lock(sqlite3.OperationalError("database is locked"))
    assert transient_lock(sqlite3.OperationalError("database table is locked"))
    assert not transient_lock(sqlite3.OperationalError("disk I/O error"))
    assert not transient_lock(
        sqlite3.OperationalError("database disk image is malformed")
    )
    assert not transient_lock(ValueError("database is locked"))
    database = tmp_path / "busy.sqlite3"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE t (x)")
    holder = sqlite3.connect(database, isolation_level=None)
    holder.execute("BEGIN EXCLUSIVE")
    try:
        with pytest.raises(sqlite3.OperationalError) as caught:
            sqlite3.connect(database, timeout=0).execute("SELECT * FROM t")
        assert transient_lock(caught.value)
    finally:
        holder.execute("ROLLBACK")
        holder.close()


# Runner -------------------------------------------------------------------


def runner(tmp_path, monkeypatch, outcomes):
    """The real run_embedded_farm with scripted run_trial outcomes."""
    import win32gui

    calls = []

    def scripted(*args, **kwargs):
        outcome = outcomes[len(calls)]
        calls.append(outcome)
        if isinstance(outcome, BaseException):
            raise outcome
        return {"reason": outcome}

    class NoCoordinates:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(desktop_app, "run_trial", scripted)
    monkeypatch.setattr(desktop_app, "physical_coordinates", NoCoordinates)
    monkeypatch.setattr(
        desktop_app, "LocalSession", lambda *a: SimpleNamespace(close=lambda: None)
    )
    monkeypatch.setattr(win32gui, "GetClientRect", lambda hwnd: (0, 0, 1036, 793))
    monkeypatch.setattr(native_farm, "NativeFarmSupervisor", lambda *a: object())
    app = desktop_app.DesktopApp.__new__(desktop_app.DesktopApp)
    app.control = FarmingControl(tmp_path / "control.json")
    app.control.update({"enabled": True})
    app.runtime = SimpleNamespace(
        external_failure=None, recovery=None, external_execution=True
    )
    app.native_recovery = SimpleNamespace()
    app.observer = None
    app.client = (1, 2, "identity")
    app.profile = tmp_path / "profile.yaml"
    app.output = tmp_path
    app.messages = queue.Queue()
    config = SimpleNamespace(
        player_profile="profiles/classic-1078-player-candidate.yaml",
        client_size=(1036, 793),
    )
    app.run_embedded_farm(config)
    messages = []
    while not app.messages.empty():
        messages.append(app.messages.get_nowait())
    return app, calls, messages


@pytest.mark.parametrize(
    "outcomes",
    [
        [sqlite3.OperationalError("disk I/O error")],
        # Live incident 2: the earlier trial's duration_limit masked the error.
        ["duration_limit", sqlite3.OperationalError("disk I/O error")],
        [KeyError("health_ratio")],
    ],
)
def test_r1_unexpected_exception_is_a_runner_failure(tmp_path, monkeypatch, outcomes):
    app, calls, messages = runner(tmp_path, monkeypatch, outcomes)
    error = outcomes[-1]
    assert calls == outcomes  # a non-lock error is never retried
    state = app.control.snapshot()
    # Not a user stop: intent stays On so the route treats it as a failure.
    assert state["enabled"] is True
    assert state["execution_state"] == "runner_stopped"
    assert state["note"].startswith("Farm runner stopped: runner_failure: ")
    assert type(error).__name__ in state["note"] and str(error) in state["note"]
    assert app.runtime.external_failure == (
        state["revision"],
        state["note"].removeprefix("Farm runner stopped: "),
    )
    assert [name for name, _ in messages] == ["failed"]
    assert app.runtime.external_execution is False


def test_r2_transient_lock_restarts_in_place_with_a_bound(tmp_path, monkeypatch):
    locked = sqlite3.OperationalError("database is locked")
    outcomes = [locked, locked, "duration_limit", locked, locked, locked]
    outcomes += ["requested_stop"]
    app, calls, messages = runner(tmp_path, monkeypatch, outcomes)
    assert calls == outcomes
    assert app.control.snapshot()["enabled"] is False  # only by the user Stop
    assert [name for name, _ in messages].count("runner_restarted") == 5
    assert messages[-1] == ("finished", {"reason": "requested_stop"})
    assert app.runtime.external_failure is None

    exhausted = [locked] * 4
    (tmp_path / "exhausted").mkdir()
    app, calls, messages = runner(tmp_path / "exhausted", monkeypatch, exhausted)
    assert len(calls) == 4  # the first attempt plus three in-place restarts
    state = app.control.snapshot()
    assert state["enabled"] is True and state["execution_state"] == "runner_stopped"
    assert state["note"] == (
        "Farm runner stopped: runner_failure: OperationalError: database is locked"
    )


@pytest.mark.parametrize(
    "reason, enabled, restarted",
    [
        ("requested_stop", False, False),
        ("emergency_stop", False, False),
        ("control_changed", True, True),
    ],
)
def test_r3_user_stop_f12_and_control_change_are_unchanged(
    tmp_path, monkeypatch, reason, enabled, restarted
):
    app, _, messages = runner(tmp_path, monkeypatch, [reason])
    assert app.control.snapshot()["enabled"] is enabled
    assert app.runtime.external_failure is None
    assert (("farm_requested", {}) in messages) is restarted
    assert messages[0] == ("finished", {"reason": reason})
