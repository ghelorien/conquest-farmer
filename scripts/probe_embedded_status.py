"""One expiring Status-button probe using the inspected embedded HUD layout."""

import argparse
import json
from pathlib import Path
import time
import yaml
from conquest.worker import request

parser = argparse.ArgumentParser()
parser.add_argument("--position-cursor", action="store_true")
args = parser.parse_args()
state = json.loads(Path("reports/desktop-farming/app-state.json").read_text())
info = state["worker_info_path"]
health = request(info, "health")
if args.position_cursor and health["input_revision"] < 8:
    raise ValueError("The running app has not loaded the positioned-input diagnostic")
window = health["window"]
width, height = window["client_size"]
if height != 793 or not 900 <= width <= 1200:
    raise ValueError("Embedded HUD geometry differs from the inspected layout")
body = {
    "health_profile": yaml.safe_load(
        Path("profiles/classic-1074-health-candidate.yaml").read_text()
    ),
    "character": "Parasite",
    "point": [width // 2 + 58, height - 42],
    "expected_size": [width, height],
    "move_settle_seconds": 0.2,
    "position_cursor": args.position_cursor,
    "expires_at": time.time() + 4,
}
try:
    result = request(info, "background-click", body)
except Exception as error:
    result = {"qualified": False, "error": str(error), "delivery_may_be_partial": True}
report = Path(
    "reports/embedded-positioned-status.json"
    if args.position_cursor
    else "reports/embedded-background-status.json"
)
report.write_text(json.dumps(result, indent=2), encoding="utf-8")
print(json.dumps(result, indent=2))
