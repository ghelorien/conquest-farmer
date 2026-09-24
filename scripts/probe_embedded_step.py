"""One bounded world click with immediate read-only position evidence.

This compares scene input separately from ImGui panel input. It never enables
the farming loop. Coordinates are limited to the currently inspected grass area.
"""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import struct
import time

import yaml

from conquest.addressing import resolve_player
from conquest.memory_health import HealthLayout, HealthWorkerSession
from conquest.worker import request


parser = argparse.ArgumentParser()
parser.add_argument("--position-cursor", action="store_true")
parser.add_argument("--dx", type=int, choices=(-1, 0, 1), default=1)
parser.add_argument("--dy", type=int, choices=(-1, 0, 1), default=0)
args = parser.parse_args()
if abs(args.dx) + abs(args.dy) != 1:
    raise ValueError("The diagnostic permits exactly one adjacent tile")
state = json.loads(Path("reports/desktop-farming/app-state.json").read_text())
info = state["worker_info_path"]
health = request(info, "health")
layout = HealthLayout.model_validate(
    yaml.safe_load(Path("profiles/classic-1074-health-candidate.yaml").read_text())
)
session = HealthWorkerSession(info, layout.player.expected_sha256)
addresses = resolve_player(session, layout.player)


def position():
    session.assert_identity()
    return list(struct.unpack("<II", session.read_block(addresses["position"], 8)))


before = position()
if not (448 <= before[0] <= 456 and 445 <= before[1] <= 453):
    raise ValueError("Character left the inspected grass area; no input sent")
window = health["window"]
width, height = window["client_size"]
if [width, height] != [1036, 793]:
    raise ValueError("The inspected embedded layout changed")
point = [width // 2 + (args.dx - args.dy) * 32, height // 2 + (args.dx + args.dy) * 16]
baseline = []
for _ in range(3):
    time.sleep(0.2)
    baseline.append(position())
if any(sample != before for sample in baseline):
    raise ValueError("Character is moving; no input sent")
generated_at = datetime.now(timezone.utc)
result = {
    "qualified": False,
    "generated_at": generated_at.isoformat(),
    "process_identity": health["target"],
    "before_position": before,
    "baseline_positions": baseline,
    "expected_position": [before[0] + args.dx, before[1] + args.dy],
    "point": point,
    "position_cursor": args.position_cursor,
}
try:
    result["input"] = request(
        info,
        "background-click",
        {
            "health_profile": layout.model_dump(mode="json"),
            "character": "Parasite",
            "point": point,
            "expected_size": [width, height],
            "position_cursor": args.position_cursor,
            "move_settle_seconds": 0.2,
            "expires_at": time.time() + 4,
        },
    )
    samples = []
    windows = []
    for _ in range(8):
        samples.append(position())
        windows.append(request(info, "health")["window"])
        time.sleep(0.2)
    result["positions_after"] = samples
    result["windows_after"] = windows
    result["expected_position_observed"] = result["expected_position"] in samples
    result["expected_position_stable"] = all(
        p == result["expected_position"] for p in samples[-3:]
    )
    original = result["input"]["before"]
    result["sampled_foreground_unchanged"] = all(
        w["foreground"] == original["foreground"] for w in windows
    )
    result["sampled_cursor_unchanged"] = all(
        w["cursor"] == original["cursor"] for w in windows
    )
except Exception as error:
    result["error"] = str(error)
    result["delivery_may_be_partial"] = True
name = "embedded-positioned-step" if args.position_cursor else "embedded-message-step"
rendered = json.dumps(result, indent=2)
Path(f"reports/{name}.json").write_text(rendered, encoding="utf-8")
Path(f"reports/{name}-{generated_at.strftime('%Y%m%dT%H%M%S%fZ')}.json").write_text(
    rendered, encoding="utf-8"
)
print(json.dumps(result, indent=2))
