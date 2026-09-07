from types import SimpleNamespace

import numpy as np

from conquest.capture import Frame, CaptureUnavailable
from conquest.health_monitor import HealthMonitor


def test_live_health_expires_and_obstruction_clears_previous_value():
    now = [10.]
    image = np.zeros((861, 1584, 3), np.uint8)
    image[772:775, 335:788] = (34, 22, 148)
    camera = SimpleNamespace(read=lambda: Frame(now[0], image, (0, 0)))
    monitor = HealthMonitor(lambda: camera, clock=lambda: now[0])
    monitor.sample(camera)
    assert monitor.snapshot()["health"] == 100
    now[0] += 1.01
    assert monitor.snapshot()["health"] is None
    monitor.sample(camera)
    assert monitor.snapshot()["health_valid"]
    def blocked():
        raise CaptureUnavailable("Game minimized")
    camera.read = blocked
    monitor.sample(camera)
    assert monitor.snapshot()["health"] is None
    assert monitor.snapshot()["health_note"] == "Game minimized"


def test_stale_frame_never_becomes_fresh_telemetry():
    image = np.zeros((861, 1584, 3), np.uint8)
    image[772:775, 335:788] = (34, 22, 148)
    camera = SimpleNamespace(read=lambda: Frame(5, image, (0, 0)))
    monitor = HealthMonitor(lambda: camera, clock=lambda: 10)
    monitor.sample(camera)
    assert not monitor.snapshot()["health_valid"]


def test_dashboard_keeps_live_kills_but_never_displays_stale_health(tmp_path, monkeypatch):
    import json
    import sqlite3
    from conquest import dashboard
    database = tmp_path / "test.sqlite3"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE events(time REAL, event TEXT, payload TEXT)")
        db.executemany("INSERT INTO events VALUES (?,?,?)", [
            (10, "trial_started", "{}"),
            (11, "health_observation", json.dumps({"health_ratio": .39})),
            (11, "kill_verified", json.dumps({"total": 2})),
            (12, "paused", "{}")])
    monkeypatch.setattr(dashboard.time, "time", lambda: 30)
    result = dashboard.status(database)
    assert result["state"] == "Paused"
    assert result["health"] is None
    assert result["kills"] == 2
    assert not any(e["event"] == "health observation" for e in result["events"])
