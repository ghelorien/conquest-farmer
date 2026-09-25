"""The farm runner's durable event journal (trial.sqlite3) under reader locks.

The journal uses SQLite's rollback journal. A concurrent reader (Discord kill
reports, the app's kill counter, town-visit checkpoints, route optimization)
holds a shared lock that blocks the writer's commit. Under heavy load that lock
has outlasted the busy timeout, and "database is locked" used to end the trial
and switch Farming Off.

A lock or busy error therefore never ends combat. The row keeps its original
time and joins an in-memory ordered backlog, which is also appended to a durable
trial-pending-<ns>.jsonl next to the database. Later record() calls flush the
whole backlog, oldest first, before the new row, in one transaction. That
transaction also names every pending file it covers in event_journal_sidecars,
so a file that survives the commit (crash, antivirus lock) is deleted, never
re-imported. The next journal (next trial start) loads any remaining pending
files ahead of its own rows. Every event is written exactly once, in order.

Any other storage error keeps the old strict behaviour: it is raised, after the
row is kept in the backlog and its pending file so it is not lost.
"""

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

# Bounded wait for an ordinary write, and for the final flush at trial end.
WRITE_TIMEOUT = 2.0
# While a backlog exists: short, rate-limited flush attempts keep combat moving.
RETRY_TIMEOUT = 0.1
RETRY_INTERVAL = 1.0
PENDING_GLOB = "trial-pending-*.jsonl"
SQLITE_BUSY, SQLITE_LOCKED = 5, 6


def transient_lock(error):
    """True only for SQLite lock/busy contention, never for other failures."""
    if not isinstance(error, sqlite3.OperationalError):
        return False
    code = getattr(error, "sqlite_errorcode", None)
    if code is not None:
        return code & 0xFF in (SQLITE_BUSY, SQLITE_LOCKED)
    message = str(error).lower()
    return (
        message.startswith("database")
        and message.endswith("is locked")
        or ("busy" in message)
    )


@dataclass
class Pending:
    time: float
    event: str
    payload: str
    source: Path
    durable: bool


class EventJournal:
    def __init__(self, directory, logger=None, *, connect=sqlite3.connect):
        self.directory = Path(directory)
        self.logger = logger
        self.path = self.directory / "trial.sqlite3"
        self.db = connect(self.path, timeout=WRITE_TIMEOUT)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS events (time REAL, event TEXT, payload TEXT)"
        )
        self.backlog = []
        self.current = None
        self.waiting_since = None
        self.next_retry = -float("inf")
        self._load_pending_files()

    @property
    def pending(self):
        return len(self.backlog)

    def _log(self, name, **fields):
        if self.logger:
            self.logger.warning(name, extra={"fields": fields})

    def _timeout(self, seconds):
        self.db.execute(f"PRAGMA busy_timeout={int(seconds * 1000)}")

    def _rollback(self):
        try:
            self.db.rollback()
        except sqlite3.Error:
            pass

    def _imported(self):
        if not self.db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='event_journal_sidecars'"
        ).fetchone():
            return set()
        return {
            name
            for (name,) in self.db.execute("SELECT name FROM event_journal_sidecars")
        }

    def _load_pending_files(self):
        files = sorted(self.directory.glob(PENDING_GLOB))
        if not files:
            return
        try:
            imported = self._imported()
        except sqlite3.OperationalError as error:
            if not transient_lock(error):
                raise
            # Cannot prove whether they were imported: keep them for next start.
            self._log("event_journal_pending_kept", files=len(files))
            return
        for path in files:
            if path.name in imported:
                self._remove(path)
                continue
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
                rows = []
                for number, line in enumerate(lines):
                    try:
                        row = json.loads(line)
                        rows.append((row["time"], row["event"], row["payload"]))
                    except (ValueError, KeyError, TypeError):
                        if number != len(lines) - 1:
                            raise
                        # A torn final line: the process died mid-append,
                        # before that row could have reached the database.
            except (OSError, ValueError, KeyError, TypeError) as error:
                self._log(
                    "event_journal_pending_invalid", file=path.name, detail=str(error)
                )
                continue
            if not self.backlog:
                self.waiting_since = time.monotonic()
            self.backlog += [Pending(*row, path, True) for row in rows]

    def _remove(self, path):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass  # recorded as imported; the next start deletes it

    def _append(self, entry):
        try:
            with entry.source.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {
                            "time": entry.time,
                            "event": entry.event,
                            "payload": entry.payload,
                        }
                    )
                    + "\n"
                )
            entry.durable = True
        except OSError:
            entry.durable = False

    def _queue(self, row):
        if self.current is None:
            self.current = self.directory / f"trial-pending-{time.time_ns()}.jsonl"
        if not self.backlog:
            self.waiting_since = time.monotonic()
        entry = Pending(*row, self.current, False)
        self._append(entry)
        self.backlog.append(entry)

    def _flush(self, timeout):
        self._timeout(timeout)
        backlog = list(self.backlog)
        files = list(dict.fromkeys(entry.source for entry in backlog))
        now = time.time()
        try:
            self.db.executemany(
                "INSERT INTO events VALUES (?,?,?)",
                [(entry.time, entry.event, entry.payload) for entry in backlog],
            )
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS event_journal_sidecars "
                "(name TEXT PRIMARY KEY, rows INTEGER, recorded_at REAL)"
            )
            self.db.executemany(
                "INSERT OR IGNORE INTO event_journal_sidecars VALUES (?,?,?)",
                [
                    (path.name, sum(entry.source == path for entry in backlog), now)
                    for path in files
                ],
            )
            self.db.execute(
                "INSERT INTO events VALUES (?,?,?)",
                (
                    now,
                    "event_journal_recovered",
                    json.dumps(
                        {
                            "rows": len(backlog),
                            "pending_files": len(files),
                            "waited_seconds": round(
                                time.monotonic() - self.waiting_since, 3
                            ),
                        }
                    ),
                ),
            )
            self.db.commit()
        except sqlite3.Error:
            self._rollback()
            raise
        finally:
            self._timeout(WRITE_TIMEOUT)
        self.backlog, self.current, self.waiting_since = [], None, None
        for path in files:
            self._remove(path)

    def _try_flush(self, timeout):
        try:
            self._flush(timeout)
        except sqlite3.OperationalError as error:
            if not transient_lock(error):
                raise
            self.next_retry = time.monotonic() + RETRY_INTERVAL

    def record(self, name, payload):
        """Write one event; a lock keeps it queued in order instead of raising."""
        row = (time.time(), name, payload)
        if self.backlog:
            self._queue(row)
            if time.monotonic() >= self.next_retry:
                self._try_flush(RETRY_TIMEOUT)
            return
        try:
            self.db.execute("INSERT INTO events VALUES (?,?,?)", row)
            self.db.commit()
        except sqlite3.Error as error:
            self._rollback()
            self._queue(row)  # never drop the event, whatever the error
            if not transient_lock(error):
                raise
            self.next_retry = time.monotonic() + RETRY_INTERVAL
            self._log("event_journal_waiting", event=name, detail=str(error))

    def close(self):
        """Bounded final flush; returns rows left in durable pending files."""
        try:
            if self.backlog:
                try:
                    self._flush(WRITE_TIMEOUT)
                except sqlite3.Error as error:
                    # Already durable in the pending files; never mask the
                    # trial's own outcome (or its original error) here.
                    for entry in self.backlog:
                        if not entry.durable:
                            self._append(entry)
                    self._log(
                        "event_journal_deferred",
                        rows=len(self.backlog),
                        pending_files=len(
                            {entry.source for entry in self.backlog if entry.durable}
                        ),
                        undurable_rows=sum(not e.durable for e in self.backlog),
                        detail=str(error),
                    )
        finally:
            self.db.close()
        return len(self.backlog)
