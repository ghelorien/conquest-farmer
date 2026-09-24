"""Durable verified kills across combat runners, town trips and app reloads."""

import json
from contextlib import closing
from pathlib import Path
import sqlite3
import time


class SessionKills:
    def __init__(self, output, *, clock=time.time):
        self.clock = clock
        self.path = Path(output) / "kill-session.json"
        self.database = Path(output) / "trial.sqlite3"
        self.state = None
        self.error = None
        try:
            if self.path.exists():
                value = json.loads(self.path.read_text(encoding="utf-8"))
                if (
                    type(value.get("kills")) is not int
                    or value["kills"] < 0
                    or type(value.get("cursor")) is not int
                    or value["cursor"] < 0
                    or not isinstance(value.get("started_at"), (int, float))
                    or value.get("stopped_at") is not None
                    and not isinstance(value["stopped_at"], (int, float))
                ):
                    raise ValueError("Invalid session state")
                self.state = value
        except (OSError, ValueError, TypeError, AttributeError):
            self.error = "Kill session could not be restored; totals unavailable"

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.state), encoding="utf-8")
        temporary.replace(self.path)

    def _connect(self):
        return sqlite3.connect(
            self.database.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.05
        )

    def begin(self):
        """Idempotent for automatic combat restarts; only a stopped session resets."""
        if self.error and self.state is None:
            raise ValueError(self.error)
        if self.state and self.state["stopped_at"] is None:
            return
        cursor = 0
        if self.database.exists():
            with closing(self._connect()) as db:
                cursor = db.execute(
                    "SELECT COALESCE(MAX(rowid),0) FROM events"
                ).fetchone()[0]
        self.state = dict(
            started_at=self.clock(), stopped_at=None, cursor=cursor, kills=0
        )
        self._save()

    def stop(self):
        if self.state and self.state["stopped_at"] is None:
            self.state["stopped_at"] = self.clock()
            self._save()

    def refresh(self):
        """Read only new journal rows. Never block gameplay on telemetry failure."""
        if self.state is None:
            return self.snapshot()
        try:
            changed = False
            if self.database.exists():
                with closing(self._connect()) as db:
                    db.execute("BEGIN")
                    end = db.execute(
                        "SELECT COALESCE(MAX(rowid),0) FROM events"
                    ).fetchone()[0]
                    if end < self.state["cursor"]:
                        raise ValueError("Kill log was truncated")
                    rows = db.execute(
                        "SELECT time,payload FROM events WHERE rowid>? AND rowid<=? "
                        "AND event='kill_verified'",
                        (self.state["cursor"], end),
                    )
                    increment = 0
                    for observed_at, payload in rows:
                        if observed_at < self.state["started_at"]:
                            continue
                        if (
                            self.state["stopped_at"] is not None
                            and observed_at > self.state["stopped_at"]
                        ):
                            continue
                        count = json.loads(payload).get("count")
                        if type(count) is not int or not 1 <= count <= 32:
                            raise ValueError("Unqualified kill increment")
                        increment += count
                changed = end != self.state["cursor"]
                self.state.update(cursor=end, kills=self.state["kills"] + increment)
            elif self.state["cursor"]:
                raise ValueError("Kill log is missing")
            if changed or self.error:
                self._save()
            self.error = None
        except (OSError, ValueError, TypeError, AttributeError, sqlite3.Error):
            self.error = "Kill metrics unavailable; retaining the last verified total"
        return self.snapshot()

    def snapshot(self):
        state = self.state
        active = bool(state and state["stopped_at"] is None)
        elapsed = max(0, self.clock() - state["started_at"]) if state else 0
        count = state["kills"] if state else 0
        return dict(
            kills=count,
            kills_per_hour=(
                None
                if self.error
                else count * 3600 / elapsed
                if active and elapsed > 0
                else 0
            ),
            kill_session_active=active,
            kill_session_started_at=state["started_at"] if state else None,
            kill_metrics_note=self.error,
        )
