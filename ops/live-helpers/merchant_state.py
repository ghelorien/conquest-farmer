"""Read-only: booth fill, bag, refill state and sales for both merchants."""
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
print("input owner:", s.get("input_owner"), "| handoff:", s.get("handoff_active"), s.get("handoff_requested"))
for name in ("Dutch", "Spiritual"):
    d = s["characters"][name]
    snap = d.get("snapshot") or {}
    refill = d.get("refill") or {}
    q = d.get("qualification") or {}
    booth = snap.get("booth") or []
    inv = snap.get("inventory") or []
    print(
        f"{name}: connected={d.get('connected')} map={snap.get('map_id')} "
        f"booth_open={snap.get('booth_open')} listed={len(booth)}/32 bag={len(inv)}/40 "
        f"silver={snap.get('silver')} snapshot_age={round(time.time() - (snap.get('timestamp') or 0), 1)}s"
    )
    print(
        f"   refill enabled={refill.get('enabled')} listing_qualified="
        f"{q.get('foreground_open_booth_listing_1078')} trading_ready={d.get('ready')} "
        f"needs_attention={d.get('needs_attention')} blocker="
        f"{(d.get('foreground_refill_1078') or {}).get('blocker')}"
    )
    p = merchant({"action": "merchant-refill-preview-1078", "character": name})
    queue = p.get("queue") or []
    value = sum(i.get("total_listing_price") or 0 for i in queue)
    print(
        f"   preview blockers={p.get('blockers')} queued={len(queue)} "
        f"queued_value={value:,} top={[(i['name'], i['total_listing_price']) for i in queue[:3]]}"
    )

journal = ROOT / "machine-state/reports/merchants/journal.sqlite3"
with sqlite3.connect(journal.as_uri() + "?mode=ro", uri=True, timeout=3) as db:
    tables = [r[0] for r in db.execute("select name from sqlite_master where type='table'")]
    print("journal tables:", tables)
    for table in tables:
        if "sale" in table.lower():
            cols = [r[1] for r in db.execute(f"pragma table_info({table})")]
            print(f"  {table} columns: {cols}")
            time_col = next((c for c in cols if c in ("sold_at", "observed_at", "time", "created_at", "at")), None)
            if time_col:
                since = time.time() - 24 * 3600
                rows = db.execute(
                    f"select count(*) from {table} where {time_col} >= ?", (since,)
                ).fetchone()
                last = db.execute(f"select max({time_col}) from {table}").fetchone()[0]
                print(f"  {table}: last 24h={rows[0]} last_at="
                      f"{time.strftime('%m-%d %H:%M', time.localtime(last)) if last else None}")
