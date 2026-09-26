"""Read-only: is each merchant's refill running and what did it last decide?"""
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


def when(value):
    return time.strftime("%H:%M:%S", time.localtime(value)) if isinstance(value, (int, float)) else value


since = float(sys.argv[1]) if len(sys.argv) > 1 else time.time() - 3 * 3600
s = merchant({"action": "status"})
print("handoff_requested:", s.get("handoff_requested"), "| input_owner:", s.get("input_owner"))
for name in ("Dutch", "Spiritual"):
    d = s["characters"][name]
    refill = d.get("refill") or {}
    work = d.get("foreground_refill_1078") or {}
    snap = d.get("snapshot") or {}
    print(f"== {name}: connected={d.get('connected')} booth_open={snap.get('booth_open')} "
          f"listed={len(snap.get('booth') or [])}/32 bag={len(snap.get('inventory') or [])}")
    print("   refill:", {k: (when(v) if k in ('next_check', 'last_check', 'attempt_started_at', 'completed_at', 'checked_at') else v)
                          for k, v in refill.items() if k not in ('cursor', 'last_verified_listing', 'listing1078_request')})
    proof = refill.get("last_verified_listing") or {}
    print("   last verified listing:", when(proof.get("verified_at")), proof.get("request_id"))
    print("   engine:", {k: (when(v) if 'observed_at' in k else v) for k, v in work.items()})

journal = ROOT / "machine-state/reports/merchants/journal.sqlite3"
with sqlite3.connect(journal.as_uri() + "?mode=ro", uri=True, timeout=3) as db:
    rows = db.execute(
        "SELECT character, event, payload, timestamp FROM events WHERE timestamp > ? "
        "AND (event LIKE '%refill%' OR event LIKE '%capacity%' OR event LIKE '%listing%') ORDER BY timestamp",
        (since,),
    ).fetchall()
    print(f"--- refill/listing journal events since {when(since)}: {len(rows)}")
    for character, event, payload, stamp in rows[-25:]:
        print("  ", when(stamp), event, (payload or "")[:160])
    tx = db.execute(
        "SELECT created, phase, before_json FROM transactions WHERE kind='booth_listing_1078_once' "
        "AND created > ? ORDER BY created", (since,),
    ).fetchall()
    print(f"--- listing transactions since {when(since)}: {len(tx)}")
    for created, phase, before_json in tx[-10:]:
        request = (json.loads(before_json or "{}").get("request") or {})
        print("  ", when(created), request.get("character"), (request.get("item_fingerprint") or {}).get("name"), phase)
