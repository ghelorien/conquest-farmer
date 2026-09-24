import json
from datetime import datetime, timezone
from pathlib import Path
from conquest.worker import request

root = Path(__file__).resolve().parents[1]
info = root / ".runtime/memory-worker.json"
health = request(info, "health")
observations = {
    "schema_version": 1,
    "source": "User reported displayed HP 213; current/max distinction not yet confirmed. Name from current window title.",
    "observed_at": datetime.now(timezone.utc).isoformat(),
    "expected_sha256": health["expected_sha256"],
    "observations": [{"name": "player_name", "kind": "utf8", "value": "Parasite"}]
    + [
        {"name": "hp_" + kind, "kind": kind, "value": 213}
        for kind in ["u16", "u32", "f32", "f64"]
    ],
}
report = request(info, "scan", {"observations": observations})
(root / "reports/hp-213-scan.json").write_text(
    json.dumps(report, indent=2), encoding="utf-8"
)
print(
    json.dumps(
        {
            "coverage": report["coverage"],
            "candidates": {
                k: {
                    "count": len(v["addresses"]),
                    "truncated": v["truncated"],
                    "addresses": v["addresses"] if k == "player_name" else [],
                }
                for k, v in report["candidates"].items()
            },
        },
        indent=2,
    )
)
