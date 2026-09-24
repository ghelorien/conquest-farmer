"""Follow a saved route's town or hunting anchor using observed arrival."""

import argparse
import json
from pathlib import Path
import time

from conquest.navigation import read_terrain, native_waypoint
from conquest.route_input import BridgeJumpStepper
from conquest.routes import RouteLibrary
from conquest.travel_care import TravelCare, TravelStateChanged

parser = argparse.ArgumentParser()
parser.add_argument("--route", required=True)
parser.add_argument("--phase", choices=("town", "hunt", "restock"), required=True)
parser.add_argument(
    "--destination", nargs=2, type=int, help="An inspected stop on the same map"
)
parser.add_argument("--avoid", nargs=2, type=int, action="append", default=[])
parser.add_argument("--seconds", type=int, default=45, choices=range(1, 61))
args = parser.parse_args()
route = RouteLibrary().load(args.route)
destination = route.town_anchor if args.phase == "town" else route.hunting_anchor
if args.phase == "restock":
    if route.restock_anchor is None:
        raise ValueError("Route has no saved restocking stop")
    destination = route.restock_anchor
if args.destination:
    destination = tuple(args.destination)
info = json.loads(Path("reports/desktop-farming/app-state.json").read_text())[
    "worker_info_path"
]
care = TravelCare(info, lambda event: print(json.dumps(event), flush=True))
stepper = BridgeJumpStepper(info, on_life=care.check)
terrain = read_terrain(r"C:\Program Files\Classic Conquer 2.0", route.map_id)
report = {
    "route": route.id,
    "phase": args.phase,
    "destination": destination,
    "steps": [],
    "arrived": False,
}
deadline = time.monotonic() + args.seconds
avoided = set(map(tuple, args.avoid))
try:
    while time.monotonic() < deadline:
        try:
            health = stepper.health()
        except TravelStateChanged:
            time.sleep(0.1)
            continue
        life = health["embedded_controls"].get("life")
        if life is None:
            time.sleep(0.05)
            continue
        if health["embedded_controls"]["control"]["enabled"]:
            raise ValueError(
                "The standalone route cannot share input with the farm executor"
            )
        if life["dead_candidate"] or life["current_hp"] < life["max_hp"] * 0.4:
            time.sleep(0.1)
            continue
        source = tuple(life["position"])
        if source == destination:
            report["arrived"] = True
            break
        path = terrain.path(source, destination, avoid=avoided)
        target = native_waypoint(path)
        try:
            result = stepper.step_to(target, expected_position=source)
        except TravelStateChanged:
            time.sleep(0.1)
            continue
        except ValueError as error:
            if str(error) in (
                "Player left the planned route segment",
                "Player moved from the planned starting tile",
                "Life state changed during observation",
                "Health fields or pointer topology changed during sampling",
            ):
                time.sleep(0.1)
                continue
            raise
        report["steps"].append(result)
        print(
            json.dumps(
                {
                    "source": source,
                    "destination": target,
                    "reached": result["reached"],
                    "movement": result.get("input", {}).get("movement"),
                }
            ),
            flush=True,
        )
        if not result["reached"]:
            if len(avoided) >= 6:
                break
            latest = stepper.observe().position
            dx, dy = target[0] - source[0], target[1] - source[1]
            blocked = (
                latest[0] + (1 if dx > 0 else -1 if dx < 0 else 0),
                latest[1] + (1 if dy > 0 else -1 if dy < 0 else 0),
            )
            avoided.add(blocked)
            report["avoided_tiles"] = sorted(avoided)
finally:
    Path("reports/hosted-travel.json").write_text(json.dumps(report, indent=2))
    print(
        json.dumps({"arrived": report["arrived"], "destination": destination}),
        flush=True,
    )
