"""Read verified kill windows without changing the running farmer."""

import json
from pathlib import Path
import sqlite3
import time

now = time.time()
root = Path(__file__).resolve().parents[1]
path = root / "reports/desktop-farming/trial.sqlite3"
with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
    records = [
        (t, json.loads(payload)["count"])
        for t, payload in db.execute(
            "select time,payload from events where time>? and event=? order by time",
            (now - 900, "kill_verified"),
        )
    ]
windows = {
    str(seconds): {"kills": sum(n for t, n in records if t > now - seconds)}
    for seconds in (60, 300, 900)
}
for seconds, window in windows.items():
    window["kills_per_minute"] = window["kills"] * 60 / int(seconds)
goal_path = root / "reports/performance/goal.json"
goal = json.loads(goal_path.read_text())
start = goal.get("validation_started_at", goal["started_at"])
full = now - start >= 900
result = {
    "observed_at": now,
    "source": str(path),
    "validation_started_at": start,
    "full_validation_window": full,
    "windows": windows,
    "rate_target_met": full and windows["900"]["kills"] >= 600,
}
goal.update(
    last_measured_at=now,
    rolling_windows=windows,
    rate_target_met=result["rate_target_met"],
)
goal_path.write_text(json.dumps(goal, indent=2))
print(json.dumps(result, indent=2))
