"""Read-only: Dutch's merchant state and merchant journal events near a time."""
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

s = merchant({"action": "status"})
d = s["characters"]["Dutch"]
print("input_owner", s.get("input_owner"), "handoff", s.get("handoff_active"), s.get("handoff_requested"))
print("needs_attention", d.get("needs_attention"), "pending", d.get("pending"))
print("refill", {k: (d.get("refill") or {}).get(k) for k in ("enabled", "pending", "listing1078_request")})
print("work", json.dumps(d.get("foreground_refill_1078"), default=str)[:400])
print("manual", s.get("manual_handoff"), s.get("manual_sessions"), s.get("manual_farmer"))

start = float(sys.argv[1]) if len(sys.argv) > 1 else time.time() - 600
end = float(sys.argv[2]) if len(sys.argv) > 2 else time.time()
journal = ROOT / "machine-state/reports/merchants/journal.sqlite3"
with sqlite3.connect(journal.as_uri() + "?mode=ro", uri=True, timeout=3) as db:
    cols = [r[1] for r in db.execute("pragma table_info(events)")]
    print("event columns", cols)
    tcol = next((c for c in cols if c in ("time", "created", "at", "observed_at", "timestamp")), None)
    if tcol:
        for row in db.execute(f"select * from events where {tcol} between ? and ? order by {tcol}", (start, end)):
            print(" ", json.dumps(dict(zip(cols, row)), default=str)[:400])
