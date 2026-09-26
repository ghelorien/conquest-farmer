"""Read-only: nearby NPC/scene sample from the farmer worker."""
import json
from pathlib import Path

RUNTIME = Path(
    r"C:\Users\Floor\AppData\Local\Packages\OpenAI.Codex_2p2nqsd0c76g0\LocalCache"
    r"\Local\Conquest\characters\6c98e401-89a9-4d8a-9bfd-9a2ed7880d26\.runtime"
)
info_path = max(RUNTIME.glob("embedded-worker-*.json"), key=lambda p: p.stat().st_mtime)

from conquest.worker import request

health = request(info_path, "health")
life = health["embedded_controls"]["life"]
x0, y0 = life["position"]
print("farmer", life["position"], hex(life["status"]))
result = request(info_path, "sample-npcs", {})
Path(RUNTIME.parent / "reports" / "diag-sample-npcs.json").write_text(
    json.dumps(result, indent=1, default=str), encoding="utf-8"
)
rows = result.get("npcs") or result.get("entities") or result.get("objects") or []
print("keys", list(result)[:20], "rows", len(rows))
for row in rows:
    pos = row.get("position") or [None, None]
    if None in pos:
        continue
    d = max(abs(pos[0] - x0), abs(pos[1] - y0))
    if d <= 12:
        print(d, json.dumps(row, default=str)[:260])
