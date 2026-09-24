"""Bounded candidate recording with durable session outcomes in SQLite."""

import json
import math
import sqlite3
import time
import uuid
from datetime import datetime, timezone


class Recorder:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        try:
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS observation_sessions (
                    id TEXT PRIMARY KEY, started_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL, duration_seconds REAL,
                    stop_reason TEXT NOT NULL DEFAULT 'running'
                );
                CREATE TABLE IF NOT EXISTS observation_samples (
                    session_id TEXT NOT NULL REFERENCES observation_sessions(id),
                    sequence INTEGER NOT NULL, sample_json TEXT NOT NULL,
                    PRIMARY KEY (session_id, sequence)
                );
            """)
        except BaseException:
            self.db.close()
            raise

    def begin(self, metadata):
        session_id = str(uuid.uuid4())
        with self.db:
            self.db.execute(
                "INSERT INTO observation_sessions(id, started_at, metadata_json) VALUES (?, ?, ?)",
                (
                    session_id,
                    datetime.now(timezone.utc).isoformat(),
                    json.dumps(metadata, allow_nan=False),
                ),
            )
        return session_id

    def append(self, session_id, sequence, sample):
        with self.db:
            self.db.execute(
                "INSERT INTO observation_samples VALUES (?, ?, ?)",
                (session_id, sequence, json.dumps(sample.to_dict(), allow_nan=False)),
            )

    def finish(self, session_id, duration, reason):
        with self.db:
            self.db.execute(
                "UPDATE observation_sessions SET duration_seconds=?, stop_reason=? WHERE id=?",
                (duration, reason, session_id),
            )

    def close(self):
        self.db.close()


def record(
    reader,
    recorder,
    metadata,
    *,
    seconds=60,
    interval=0.25,
    clock=time.monotonic,
    sleep=time.sleep,
    should_stop=lambda: False,
    on_sample=lambda _: None,
):
    if not math.isfinite(seconds) or not 0 < seconds <= 3600:
        raise ValueError(
            "Observation duration must be greater than 0 and at most 3600 seconds"
        )
    if not math.isfinite(interval) or not 0.05 <= interval <= 10:
        raise ValueError("Observation interval must be between 0.05 and 10 seconds")
    started = clock()
    session_id = recorder.begin(metadata)
    count, reason, failure = 0, "duration_elapsed", None
    previous, changes = {}, {}
    try:
        while clock() - started < seconds:
            if should_stop():
                reason = "emergency_stop"
                break
            sample = reader.read()
            recorder.append(session_id, count, sample)
            count += 1
            on_sample(sample)
            if not sample.read_ok:
                reason, failure = "invalid_observation", sample.error
                break
            for field in sample.fields:
                key = f"{field.name}@{field.address}"
                changes.setdefault(key, 0)
                if key in previous and previous[key] != field.value:
                    changes[key] += 1
                previous[key] = field.value
            # No catch-up burst after slow reads; stop polling remains responsive.
            next_sample = min(clock() + interval, started + seconds)
            while clock() < next_sample:
                if should_stop():
                    reason = "emergency_stop"
                    break
                sleep(min(0.05, next_sample - clock()))
            if reason == "emergency_stop":
                break
    except KeyboardInterrupt:
        reason = "interrupted"
    except BaseException:
        reason = "recording_error"
        raise
    finally:
        duration = clock() - started
        recorder.finish(session_id, duration, reason)
    return {
        "schema_version": 1,
        "stage": "observation_recording",
        "session_id": session_id,
        "samples": count,
        "duration_seconds": duration,
        "stop_reason": reason,
        "error": failure,
        "candidate_change_counts": changes,
        "qualified": False,
        "autonomous_actions_enabled": False,
        "note": "Changing values do not prove field semantics; constant values do not prove a frozen client.",
    }
