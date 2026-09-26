"""Read-only: recent refill listing results and each merchant's booth count."""
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(
    r"C:\Users\Floor\AppData\Local\Packages\OpenAI.Codex_2p2nqsd0c76g0"
    r"\LocalCache\Local\Conquest"
)
os.environ["CONQUEST_DATA_ROOT"] = str(ROOT)
sys.path.insert(0, r"C:\Users\Floor\Documents\ChatGPT\cf-smooth\src")
from conquest.merchants.bridge import request as merchant  # noqa: E402

since = time.time() - float(sys.argv[1] if len(sys.argv) > 1 else 900)
journal = ROOT / "machine-state/reports/merchants/journal.sqlite3"
with sqlite3.connect(journal.as_uri() + "?mode=ro", uri=True, timeout=3) as db:
    db.row_factory = sqlite3.Row
    rows = db.execute(
        "SELECT id, phase, created, updated, before_json, result_json FROM transactions "
        "WHERE kind='booth_listing_1078_once' AND updated > ? ORDER BY created",
        (since,),
    ).fetchall()
    for row in rows:
        before = json.loads(row["before_json"] or "{}")
        request = before.get("request") or {}
        result = json.loads(row["result_json"] or "{}")
        item = (request.get("item_fingerprint") or {}).get("name")
        steps = [s[0] for s in db.execute(
            "SELECT stage FROM transaction_steps WHERE transaction_id=? ORDER BY timestamp", (row["id"],)
        )]
        print(time.strftime("%H:%M:%S", time.localtime(row["created"])), request.get("character"), item,
              request.get("price"), row["phase"], "| steps:", ",".join(steps[-4:]),
              "| reason:", str(result.get("reason") or result.get("note") or "")[:160])
s = merchant({"action": "status"})
for name in ("Dutch", "Spiritual"):
    d = s["characters"][name]
    snap = d.get("snapshot") or {}
    work = d.get("foreground_refill_1078") or {}
    print(name, "listed", len(snap.get("booth") or []), "/32 bag", len(snap.get("inventory") or []),
          "qualified", (d.get("qualification") or {}).get("foreground_open_booth_listing_1078"),
          "work", work.get("state"), work.get("blocker"))
