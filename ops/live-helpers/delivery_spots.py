"""Read-only: where the farmer stood for each verified merchant delivery."""
import collections
import json
import time
from pathlib import Path

CHAR = Path(
    r"C:\Users\Floor\AppData\Local\Packages\OpenAI.Codex_2p2nqsd0c76g0\LocalCache"
    r"\Local\Conquest\characters\6c98e401-89a9-4d8a-9bfd-9a2ed7880d26"
)
last_position = None
spots = collections.Counter()
rows = []
stalls = collections.Counter()
with open(CHAR / "reports/overnight/events.jsonl", encoding="utf-8") as handle:
    for line in handle:
        if '"runback' not in line and "merchant_delivery_verified" not in line and "merchant_approach_deferred" not in line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        runback = event.get("runback")
        if isinstance(runback, dict) and runback.get("map_id") == 1036 and runback.get("position"):
            last_position = tuple(runback["position"])
        if event.get("event") == "merchant_delivery_verified":
            spots[(event.get("merchant"), last_position)] += 1
            rows.append((event["time"], event.get("merchant"), last_position))
        if event.get("event") == "merchant_approach_deferred":
            stalls[event.get("merchant")] += 1
print("verified deliveries:", len(rows), "| approach stalls:", dict(stalls))
for (merchant, spot), count in spots.most_common(20):
    print(f"  {count:3d}  {merchant}  farmer at {spot}")
print("last 8:")
for stamp, merchant, spot in rows[-8:]:
    print("  ", time.strftime("%m-%d %H:%M", time.localtime(stamp)), merchant, spot)
