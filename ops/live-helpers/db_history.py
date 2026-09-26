"""Read-only: outcome history of one-shot listings by item name."""
import collections
import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(
    r"C:\Users\Floor\AppData\Local\Packages\OpenAI.Codex_2p2nqsd0c76g0"
    r"\LocalCache\Local\Conquest"
)
journal = ROOT / "machine-state/reports/merchants/journal.sqlite3"
names = set(sys.argv[1:]) or {"DragonBall"}
outcomes = collections.Counter()
last = {}
with sqlite3.connect(journal.as_uri() + "?mode=ro", uri=True, timeout=3) as db:
    for tid, phase, created, before_json, result_json in db.execute(
        "SELECT id, phase, created, before_json, result_json FROM transactions "
        "WHERE kind='booth_listing_1078_once' ORDER BY created"
    ):
        before = json.loads(before_json or "{}")
        request = before.get("request") or {}
        name = (request.get("item_fingerprint") or {}).get("name")
        if name in names:
            reason = (json.loads(result_json or "{}").get("reason") or "")[:90]
            outcomes[(name, phase, reason)] += 1
            last[(name, phase, reason)] = created
for key, count in sorted(outcomes.items(), key=lambda kv: last[kv[0]]):
    print(count, key, "last", time.strftime("%m-%d %H:%M", time.localtime(last[key])))
