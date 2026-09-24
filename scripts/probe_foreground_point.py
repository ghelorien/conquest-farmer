"""Local one-click baseline using a fresh candidate health and pointer check."""

from pathlib import Path
import json
import yaml
from conquest.addressing import resolve_player
from conquest.memory_health import HealthLayout, HealthWorkerSession, MemoryHealthReader
from conquest.worker import request

root = Path(__file__).resolve().parents[1]
info = root / ".runtime/input-probe-worker.json"
layout = HealthLayout.model_validate(
    yaml.safe_load((root / "profiles/classic-1074-health-candidate.yaml").read_text())
)
session = HealthWorkerSession(info, layout.player.expected_sha256)
hp = MemoryHealthReader(session, layout, "Parasite").read()
if hp.current_hp != hp.max_hp or hp.current_hp <= 0:
    raise ValueError("Baseline requires the full HP candidate")
addresses = resolve_player(session, layout.player)
result = request(
    info,
    "foreground-click",
    {
        "point": [826, 751],
        "expected_size": [1536, 793],
        "require_foreground": True,
        "guard": {
            "name_address": hex(addresses["name"]),
            "name": "Parasite",
            "hp_address": hex(addresses["max_hp"]),
            "max_hp": hp.max_hp,
        },
    },
)
(root / "reports/foreground-status-scaled.json").write_text(
    json.dumps(result, indent=2)
)
print(json.dumps(result, indent=2))
