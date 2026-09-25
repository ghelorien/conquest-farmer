"""End-to-end: a concurrent journal reader holds SQLite's shared lock while the
desktop farm runner records verified kills.

Live incidents 2026-09-24 21:03:09 and 2026-09-25 09:33:28: under heavy load a
reader of reports/desktop-farming/trial.sqlite3 (journal_mode=delete) held its
shared lock longer than the writer's default 5 s busy timeout. run_trial's
event() raised sqlite3.OperationalError("database is locked"), which escaped
run_trial; DesktopApp.run_embedded_farm finished the session as
"requested_stop" (or the previous trial's "duration_limit"), switching Farming
Off and leaving the farmer idle in the hunting field.

Real code under test: DesktopApp.run_embedded_farm -> run_trial (three
consecutive in-place trials) -> its event journal on a real trial.sqlite3 file,
FarmingControl.finish_session, SessionKills (incremental rowid cursor, the app's
kill counter) and discord_notify.recent_kills (time-window reader).

Fakes exist only at process boundaries: the game process (LocalSession memory
samples scripted per trial), window geometry, F11/F12 key state, the monotonic
clock/sleep (fake, deterministic) and NativeFarmSupervisor (no targets). The
concurrent reader is a real second SQLite connection in another thread holding
BEGIN + the same unindexed kill scan recent_kills runs.

Failure modes, written before the fix:
 E1 A reader holds the shared lock for more than 5 s while a kill is recorded:
    the trial raises "database is locked", the runner stops, or Farming is
    switched Off.
 E2 The kill recorded under the lock is dropped, written twice, written out of
    order with later events, or loses its original time once the lock clears.
 E3 The lock persists to trial end: the trial ends with an error, loses the
    queued events, or leaves no durable pending file.
 E4 The next trial start imports that pending file zero or two times, or the
    readers (SessionKills cursor, recent_kills window) count a kill twice.
 E5 A user Stop after recovery is not honoured exactly as before
    (requested_stop -> Farming Off), or the runner reports a failure.
 E6 The artifact is not repeatable.

The scenario writes <tmp>/<run>/journal-lock-e2e.json, re-reads it for the
assertions, and runs twice to prove the artifact is byte-identical.
"""

import json
import queue
import sqlite3
import threading
import time
from types import SimpleNamespace


from conquest import desktop_app, discord_notify, native_farm, trial
from conquest.control import FarmingControl
from conquest.farmer_profile import CombatSpeed
from conquest.memory_inventory import InventorySnapshot, Item
from conquest.session_kills import SessionKills
from trial_template import trial_template

# Longer than sqlite3's old default 5 s busy timeout, with margin for Windows
# sleep granularity stretching the busy handler's counted wait.
HOLD_SECONDS = 8.0
KEPT = {
    "trial_started",
    "kill_verified",
    "trial_stopped",
    "event_journal_recovered",
}


class LockHolder:
    """A real concurrent reader holding the shared lock (like a slow recent_kills)."""

    def __init__(self, database):
        self.database = database
        self.locked = threading.Event()
        self.release = threading.Event()
        self.released = threading.Event()
        self.held = []

    def start(self, hold=None):
        self.locked.clear()
        self.release.clear()
        self.released.clear()

        def run():
            db = sqlite3.connect(
                self.database.resolve().as_uri() + "?mode=ro", uri=True, timeout=5
            )
            try:
                db.execute("BEGIN")
                db.execute(
                    "SELECT count(*) FROM events WHERE event='kill_verified' AND time>?",
                    (0,),
                ).fetchone()
                began = time.perf_counter()
                self.locked.set()
                self.release.wait(hold if hold is not None else 120)
                self.held.append(time.perf_counter() - began)
                db.rollback()
            finally:
                db.close()
                self.released.set()

        threading.Thread(target=run, daemon=True).start()
        assert self.locked.wait(10), "reader could not take the shared lock"


def run_scenario(root, monkeypatch):
    import win32api
    import win32gui

    root.mkdir(parents=True)
    output = root / "desktop-farming"
    database = output / "trial.sqlite3"
    holder = LockHolder(database)
    now = [10.0]
    end_trial = [False]

    def fake_sleep(delay):
        now[0] += delay + (2000 if end_trial[0] else 0)
        end_trial[0] = False

    monkeypatch.setattr(trial.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(trial.time, "sleep", fake_sleep)
    monkeypatch.setattr(win32api, "GetAsyncKeyState", lambda _: 0)
    monkeypatch.setattr(win32gui, "GetClientRect", lambda hwnd: (0, 0, 1036, 793))
    monkeypatch.setattr(trial, "load_combat_speed", lambda _: CombatSpeed())

    kills = SessionKills(output)
    readings = {}
    observed = {}

    class Game:
        """Scripted memory samples; a new run_trial begins with a health read."""

        def __init__(self):
            self.trial, self.index, self.counter = 0, 0, 100

        def close(self):
            pass

        def request(self, operation, body=None):
            if operation == "health":
                self.trial += 1
                self.index = 0
                if self.trial == 2:
                    readings["after_trial_1"] = kills.refresh()["kills"]
                    # E3: this lock is held past the end of trial 2.
                    holder.start()
                if self.trial == 3:
                    readings["pending_before_trial_3"] = kills.refresh()["kills"]
                    files = sorted(output.glob("trial-pending-*.jsonl"))
                    observed["pending_files"] = len(files)
                    observed["pending_rows"] = sum(
                        len(path.read_text(encoding="utf-8").splitlines())
                        for path in files
                    )
                    holder.release.set()
                    assert holder.released.wait(10)
                return {"input_revision": 7, "window": {"hwnd": 1}}
            assert operation == "sample"
            if len(body["fields"]) == 2:  # kill confirmation read
                return {
                    "fields": [
                        {"name": "name", "value": "LockTest"},
                        {"name": "kill_counter", "value": [self.counter]},
                    ]
                }
            self.index += 1
            step = (self.trial, self.index)
            if step == (1, 2):
                # E1: reader takes the lock just before a kill; holds > 5 s.
                holder.start(hold=HOLD_SECONDS)
            if step in ((1, 3), (1, 20), (2, 3), (3, 3)):
                self.counter += 1
            if step == (1, 6):
                # Farming must still be running when the lock clears.
                assert holder.released.wait(15)
            if step in ((1, 24), (2, 6)):
                end_trial[0] = True
            if step == (3, 5):
                (output / "stop.request").write_text("Farming Off")
            values = {
                "name": "LockTest",
                "position": [423, 455],
                "max_hp": [100],
                "kill_counter": [self.counter],
                "level": [18],
                "map": [1002],
            }
            return {"fields": [{"name": k, "value": v} for k, v in values.items()]}

    class Inventory:
        def __init__(self, *args):
            pass

        def read(self):
            now[0] += 0.01
            return InventorySnapshot(
                now[0],
                now[0],
                (Item(1, 1000000, 1, 1, 0),),
                Item(2, 1050000, 200, 200, None),
                0,
                40,
            )

    monkeypatch.setattr(trial, "MemoryInventoryReader", Inventory)
    monkeypatch.setattr(
        trial,
        "resolve_player",
        lambda *a: dict.fromkeys(
            ("name", "position", "max_hp", "kill_counter", "level", "map"), 1
        ),
    )
    game = Game()
    monkeypatch.setattr(desktop_app, "LocalSession", lambda *a: game)
    monkeypatch.setattr(
        desktop_app,
        "WindowGeometry",
        lambda *a: SimpleNamespace(geometry=lambda: (0, 0), close=lambda: None),
    )

    class NoCoordinates:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(desktop_app, "physical_coordinates", NoCoordinates)
    monkeypatch.setattr(
        native_farm,
        "NativeFarmSupervisor",
        lambda *a: SimpleNamespace(
            last_target=None,
            recovery=SimpleNamespace(terrain=SimpleNamespace(width=1000, height=1000)),
            observe=lambda: {"health_ratio": 1.0, "waiting": False, "defending": False},
            memory_targets=lambda *a: [],
            loot_step=lambda *a: False,
            finish_target=lambda *a: None,
        ),
    )
    config = trial_template("pheasant-foreground-trial.yaml")
    config.update(
        character="LockTest",
        observation_mode="memory_only",
        kite_when_surrounded=False,
        adaptive_scatter=False,
        jump_scatter=False,
        attack_button="right",
        route=[],
        healing_enabled=False,
        loot_allowlist=[],
        interval=0.15,
        client_size=[1036, 793],
        player_anchor=[518, 396],
    )
    app = desktop_app.DesktopApp.__new__(desktop_app.DesktopApp)
    app.control = FarmingControl(root / "control.json")
    app.control.update({"enabled": True})
    app.runtime = SimpleNamespace(
        external_failure=None, recovery=None, external_execution=True
    )
    app.native_recovery = SimpleNamespace()
    app.observer = None
    app.client = (1, 2, "identity")
    app.profile = root / "unused-profile.yaml"
    app.output = output
    app.messages = queue.Queue()
    kills.begin()
    try:
        # The real desktop runner, synchronously in this thread.
        app.run_embedded_farm(trial.TrialConfig.model_validate(config))
    finally:
        holder.release.set()
        holder.released.wait(15)
    readings["final"] = kills.refresh()["kills"]
    messages = []
    while not app.messages.empty():
        messages.append(app.messages.get_nowait())

    with sqlite3.connect(database) as db:
        rows = db.execute("SELECT rowid,time,event,payload FROM events").fetchall()
    times = [row[1] for row in rows]
    finished = [fields for name, fields in messages if name == "finished"]
    state = app.control.snapshot()
    artifact = {
        "runner": {
            "failed": [fields for name, fields in messages if name == "failed"],
            "finished": [{"reason": f["reason"]} for f in finished],
            "farming_enabled": state["enabled"],
            "execution_state": state["execution_state"],
            "external_failure": app.runtime.external_failure,
        },
        "journal": [
            {
                "event": name,
                **{
                    key: value
                    for key, value in json.loads(payload).items()
                    if key in ("reason", "confirmed_kills", "count", "total", "rows")
                },
            }
            for _, _, name, payload in rows
            if name in KEPT
        ],
        "row_times_follow_rowids": times == sorted(times),
        "pending_at_trial_3_start": observed,
        "pending_files_after": len(list(output.glob("trial-pending-*.jsonl"))),
        "session_kills": readings,
        "recent_kills": discord_notify.recent_kills(
            database, time.time() + 1, seconds=3600
        ),
        "first_lock_held_over_5s": bool(holder.held) and holder.held[0] > 5,
        "locks_taken": len(holder.held),
    }
    path = root / "journal-lock-e2e.json"
    path.write_text(json.dumps(artifact, indent=2, sort_keys=True), encoding="utf-8")
    return path


def test_reader_lock_never_stops_farming_and_counts_each_kill_once(
    tmp_path, monkeypatch
):
    first = run_scenario(tmp_path / "first", monkeypatch)
    second = run_scenario(tmp_path / "second", monkeypatch)
    # E6: repeatable artifact.
    assert first.read_bytes() == second.read_bytes()
    artifact = json.loads(first.read_text(encoding="utf-8"))
    runner = artifact["runner"]

    # E1: the > 5 s lock did not end farming; no runner failure at all.
    assert artifact["first_lock_held_over_5s"] and artifact["locks_taken"] == 2
    assert runner["failed"] == []
    # E5: three in-place trials, the last ended only by the user's Stop.
    assert runner["finished"] == [{"reason": "requested_stop"}]
    assert runner["farming_enabled"] is False
    assert runner["external_failure"] is None

    journal = artifact["journal"]
    stops = [row for row in journal if row["event"] == "trial_stopped"]
    assert [row["reason"] for row in stops] == [
        "duration_limit",
        "duration_limit",
        "requested_stop",
    ]
    assert [row["confirmed_kills"] for row in stops] == [2, 1, 1]
    # E2/E4: each verified kill exactly once, in order, with its original time.
    verified = [row for row in journal if row["event"] == "kill_verified"]
    assert [(row["count"], row["total"]) for row in verified] == [
        (1, 1),
        (1, 2),
        (1, 1),
        (1, 1),
    ]
    assert artifact["row_times_follow_rowids"] is True
    assert [row["event"] for row in journal if row["event"] != "kill_verified"] == [
        "trial_started",
        "event_journal_recovered",  # trial 1: the kill queued under the lock
        "trial_stopped",
        "trial_started",  # trial 2: imported from the pending file by trial 3
        "trial_stopped",
        "trial_started",
        "event_journal_recovered",
        "trial_stopped",
    ]
    # E3: trial 2 ended under the lock with a durable pending file ...
    pending = artifact["pending_at_trial_3_start"]
    assert pending["pending_files"] == 1 and pending["pending_rows"] >= 3
    # ... which trial 3 imported exactly once and removed.
    assert artifact["pending_files_after"] == 0
    # E4: the app's incremental counter and the time-window reader agree.
    assert artifact["session_kills"] == {
        "after_trial_1": 2,
        "pending_before_trial_3": 2,
        "final": 4,
    }
    assert artifact["recent_kills"] == 4
