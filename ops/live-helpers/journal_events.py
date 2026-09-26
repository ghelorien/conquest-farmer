"""Read-only: merchant journal events and listing trace in a time window."""
import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(
    r"C:\Users\Floor\AppData\Local\Packages\OpenAI.Codex_2p2nqsd0c76g0"
    r"\LocalCache\Local\Conquest"
)
start, end = float(sys.argv[1]), float(sys.argv[2])
request_id = sys.argv[3] if len(sys.argv) > 3 else None
journal = ROOT / "machine-state/reports/merchants/journal.sqlite3"
with sqlite3.connect(journal.as_uri() + "?mode=ro", uri=True, timeout=3) as db:
    db.row_factory = sqlite3.Row
    rows = db.execute(
        "select character, event, payload, timestamp from events where timestamp between ? and ? order by timestamp",
        (start, end),
    ).fetchall()
    print("events:", len(rows))
    for row in rows:
        print(time.strftime("%H:%M:%S", time.localtime(row["timestamp"])), row["character"], row["event"],
              (row["payload"] or "")[:260])
    if request_id:
        steps = db.execute(
            "select stage, status, payload, timestamp from transaction_steps where transaction_id=? order by timestamp",
            (request_id,),
        ).fetchall()
        print("steps:", len(steps))
        for step in steps:
            print(time.strftime("%H:%M:%S", time.localtime(step["timestamp"])), step["stage"], step["status"],
                  (step["payload"] or "")[:300])
