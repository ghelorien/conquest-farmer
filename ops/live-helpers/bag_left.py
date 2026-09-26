"""Read-only: what each merchant still holds unlisted, and the plan's reason."""
import os
import sys
from pathlib import Path

ROOT = Path(
    r"C:\Users\Floor\AppData\Local\Packages\OpenAI.Codex_2p2nqsd0c76g0"
    r"\LocalCache\Local\Conquest"
)
os.environ["CONQUEST_DATA_ROOT"] = str(ROOT)
sys.path.insert(0, r"C:\Users\Floor\Documents\ChatGPT\cf-smooth\src")
from conquest.merchants.bridge import request as merchant  # noqa: E402

s = merchant({"action": "status"})
for name in ("Dutch", "Spiritual"):
    snap = s["characters"][name].get("snapshot") or {}
    booth = snap.get("booth") or []
    value = sum(i.get("price") or 0 for i in booth)
    print(f"{name}: listed {len(booth)}/32 worth {value:,}")
    preview = merchant({"action": "merchant-refill-preview-1078", "character": name})
    queued = {row["uid"]: row for row in preview.get("queue") or []}
    for item in snap.get("inventory") or []:
        row = queued.get(item["uid"])
        print("   unlisted:", item["name"], f"+{item['plus']}", "uid", item["uid"],
              "| queued price:", (row or {}).get("total_listing_price"),
              "| preview deferred:", preview.get("deferred"))
