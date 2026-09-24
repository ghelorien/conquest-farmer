"""Execute a bounded batch of map-checked waypoints with arrival feedback."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from conquest.navigation import read_terrain, straight_waypoints
from conquest.scene_input import BridgeSceneStepper
from conquest.routes import RouteLibrary, plan_travel

parser = argparse.ArgumentParser()
target = parser.add_mutually_exclusive_group(required=True)
target.add_argument("--destination", nargs=2, type=int)
target.add_argument("--route", help="Saved route ID from profiles/routes")
parser.add_argument(
    "--phase", choices=("outbound", "return", "patrol"), default="outbound"
)
parser.add_argument("--maximum-segments", type=int, default=3, choices=range(1, 16))
args = parser.parse_args()
state = json.loads(Path("reports/desktop-farming/app-state.json").read_text())
stepper = BridgeSceneStepper(
    state["worker_info_path"],
    "profiles/classic-1074-player-candidate.yaml",
    "profiles/classic-1074-health-candidate.yaml",
    "Parasite",
    expected_map=1002,
    allowed_bounds=(400, 370, 710, 710),
    max_delta=4,
)
source = stepper.observe().position
terrain = read_terrain(r"C:\Program Files\Classic Conquer 2.0", 1002)
if args.route:
    route = RouteLibrary().load(args.route)
    report = plan_travel(route, terrain, source, phase=args.phase)
else:
    path = terrain.path(source, tuple(args.destination))
    report = {
        "source": source,
        "destination": args.destination,
        "tile_steps": len(path) - 1,
        "terrain_sha256": terrain.source_sha256,
        "waypoints": straight_waypoints(path, 4),
    }
waypoints = report["waypoints"]
report.update(steps=[], arrived=False, qualified_farming_loop=False)
stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
output = Path(f"reports/route-walk-{stamp}.json")
try:
    for destination in waypoints[1 : args.maximum_segments + 1]:
        result = stepper.step_to(destination, expected_position=source)
        report["steps"].append(result)
        print(
            json.dumps(
                {
                    "destination": destination,
                    "reached": result["reached"],
                    "error": result.get("error"),
                }
            ),
            flush=True,
        )
        output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        if not result["reached"]:
            break
        source = destination
    report["arrived"] = tuple(source) == tuple(report["destination"])
except Exception as error:
    report["error"] = str(error)
finally:
    report["last_verified_position"] = source
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "report": str(output),
                "arrived": report["arrived"],
                "last_verified_position": source,
                "error": report.get("error"),
            }
        ),
        flush=True,
    )
