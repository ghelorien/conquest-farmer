import json
from datetime import datetime, timezone
from pathlib import Path
from conquest.worker import request

root = Path(__file__).resolve().parents[1]
info = root / ".runtime/memory-worker.json"
health = request(info, "health")
player = json.loads((root / "reports/player-root-trace.json").read_text())
if player["process_identity"] != health["target"]:
    raise ValueError("Process changed")
observations = {
    "schema_version": 1,
    "source": "Pointer reference search for the freshly identified player candidate; not HP validation.",
    "observed_at": datetime.now(timezone.utc).isoformat(),
    "expected_sha256": health["expected_sha256"],
    "observations": [
        {"name": "player_pointer", "kind": "u64", "value": int(player["object"], 16)}
    ],
}
r = request(info, "scan", {"observations": observations})
(root / "reports/player-reference-scan.json").write_text(json.dumps(r, indent=2))
print(json.dumps({"coverage": r["coverage"], "candidates": r["candidates"]}, indent=2))
