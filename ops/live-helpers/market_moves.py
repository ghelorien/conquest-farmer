"""Read-only: how Market (map 1036) route clicks fare, from the input log.

recovery-input.jsonl records every route click with source, destination,
action (run/jump) and screen point; the next record's source tells whether
the farmer moved. Groups outcomes by action and click distance.
"""
import collections
import json
import sys
import time
from pathlib import Path

CHAR = Path(
    r"C:\Users\Floor\AppData\Local\Packages\OpenAI.Codex_2p2nqsd0c76g0\LocalCache"
    r"\Local\Conquest\characters\6c98e401-89a9-4d8a-9bfd-9a2ed7880d26"
)
since = float(sys.argv[1]) if len(sys.argv) > 1 else time.time() - 12 * 3600
rows = []
with open(CHAR / "reports/recovery-input.jsonl", encoding="utf-8") as handle:
    for line in handle:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("issued_at", 0) >= since and row.get("source") and row.get("destination"):
            rows.append(row)
# Market tiles: the route clicks are map-agnostic here, so keep the Market box.
def in_market(p):
    return 150 <= p[0] <= 280 and 150 <= p[1] <= 240
stats = collections.defaultdict(lambda: [0, 0])
examples = collections.defaultdict(list)
for current, following in zip(rows, rows[1:]):
    source, destination = tuple(current["source"]), tuple(current["destination"])
    if not in_market(source) or following["issued_at"] - current["issued_at"] > 15:
        continue
    moved = tuple(following["source"]) != source
    distance = max(abs(a - b) for a, b in zip(source, destination))
    band = "1-2" if distance <= 2 else "3-5" if distance <= 5 else "6-9" if distance <= 9 else "10+"
    key = (current.get("action"), band)
    stats[key][0 if moved else 1] += 1
    if not moved and len(examples[key]) < 3:
        examples[key].append((time.strftime("%H:%M:%S", time.localtime(current["issued_at"])), source, destination))
print(f"Market route clicks since {time.strftime('%H:%M', time.localtime(since))}: {sum(sum(v) for v in stats.values())}")
for key in sorted(stats, key=lambda k: (str(k[0]), k[1])):
    moved, stuck = stats[key]
    print(f"  {key[0]:5} {key[1]:4} tiles: moved {moved:3d}  no-move {stuck:3d}  ({100 * moved / max(1, moved + stuck):.0f}% moved)")
    for example in examples[key]:
        print("        no-move e.g.", example)
