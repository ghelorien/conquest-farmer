"""Level milestones from verified memory, with explicit review/qualification status."""

import threading
import time
from pathlib import Path

import yaml

from conquest.addressing import PlayerLayout, WorkerPointerSession, resolve_player


def review_plan(level, plan):
    if not isinstance(level, int) or isinstance(level, bool) or not 1 <= level <= 140:
        raise ValueError("Invalid character level")
    stages = sorted(plan["stages"], key=lambda s: s["review_character_level"])
    eligible = [s for s in stages if s["review_character_level"] <= level]
    stage = eligible[-1] if eligible else None
    reviews = [r for r in plan.get("reviews", []) if r.get("status") != "completed"]
    return {
        "monster_review": stage["monster"] if stage else None,
        "monster_route_qualified": bool(stage and stage["route_qualified"]),
        "due_reviews": [r for r in reviews if r["level"] <= level],
        "next_reviews": sorted(
            [r for r in reviews if r["level"] > level], key=lambda r: r["level"]
        )[:4],
        "completed_upgrades": [
            r["title"]
            for r in plan.get("reviews", [])
            if r.get("status") == "completed"
        ],
    }


def read_level(session, layout, character):
    started = time.monotonic()
    addresses = resolve_player(session, layout)
    sample = session.request(
        "sample",
        {
            "fields": [
                {"name": n, "address": hex(addresses[n]), "kind": k}
                for n, k in (("name", "utf8"), ("level", "u32"))
            ]
        },
    )
    fields = {f["name"]: f["value"] for f in sample["fields"]}
    if fields["name"] != character or resolve_player(session, layout) != addresses:
        raise ValueError("Character changed during level observation")
    if time.monotonic() - started > 2:
        raise ValueError("Level observation expired")
    level = fields["level"][0]
    if not 1 <= level <= 140:
        raise ValueError("Level is outside the validated range")
    return level


class LevelMonitor:
    def __init__(self, reader, plan, *, clock=time.monotonic):
        self.reader, self.plan, self.clock = reader, plan, clock
        self.lock, self.stop = threading.Lock(), threading.Event()
        self.level, self.timestamp, self.error, self.thread = (
            None,
            None,
            "Waiting for level memory",
            None,
        )

    def sample(self):
        try:
            level = self.reader()
            review_plan(level, self.plan)
            with self.lock:
                if self.level is not None and level < self.level:
                    raise ValueError(
                        "Level decreased; character integration needs review"
                    )
                self.level, self.timestamp, self.error = level, self.clock(), None
        except Exception as error:
            with self.lock:
                self.timestamp, self.error = None, str(error)

    def snapshot(self):
        with self.lock:
            valid = (
                self.timestamp is not None and 0 <= self.clock() - self.timestamp <= 5
            )
            result = {
                "character_level": self.level if valid else None,
                "level_valid": valid,
                "level_note": "Live read-only memory"
                if valid
                else (self.error or "Level reading is stale"),
            }
            if valid:
                result.update(review_plan(self.level, self.plan))
            return result

    def _run(self):
        while not self.stop.is_set():
            self.sample()
            self.stop.wait(2)

    def start(self):
        self.thread = threading.Thread(
            target=self._run, name="level-monitor", daemon=True
        )
        self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=2)


class CombinedMonitor:
    def __init__(self, *monitors):
        self.monitors = monitors

    def start(self):
        for monitor in self.monitors:
            monitor.start()

    def snapshot(self):
        result = {}
        for monitor in self.monitors:
            result.update(monitor.snapshot())
        return result

    def close(self):
        for monitor in self.monitors:
            monitor.close()


def from_profile(config, info):
    layout = PlayerLayout.model_validate(
        yaml.safe_load(Path(config.player_profile).read_text())
    )
    plan = yaml.safe_load(Path(config.leveling_plan).read_text())
    session = None

    def reader():
        nonlocal session
        try:
            if session is None:
                session = WorkerPointerSession(info, layout.expected_sha256)
            return read_level(session, layout, config.character)
        except Exception:
            session = None  # Reconnect only through fresh worker identity checks.
            raise

    return LevelMonitor(reader, plan)
