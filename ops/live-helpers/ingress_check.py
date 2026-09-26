"""Read-only: can the approach plan a path to Dutch through the live crowd?"""
import os
import sys
from pathlib import Path

ROOT = Path(
    r"C:\Users\Floor\AppData\Local\Packages\OpenAI.Codex_2p2nqsd0c76g0"
    r"\LocalCache\Local\Conquest"
)
os.environ["CONQUEST_DATA_ROOT"] = str(ROOT)
sys.path.insert(0, r"C:\Users\Floor\Documents\ChatGPT\cf-smooth\src")
from conquest.merchants.bridge import request as merchant  # noqa: E402
from conquest.city_travel import CLIENT_ROOT  # noqa: E402
from conquest.navigation import read_terrain  # noqa: E402
from conquest.merchants.approach import ingress_position  # noqa: E402

probe = merchant({"action": "delivery-target", "character": "Dutch"})
terrain = read_terrain(CLIENT_ROOT, 1036)
source, target = tuple(probe["farmer_position"]), tuple(probe["merchant_position"])
occupied = set(map(tuple, probe.get("occupied_tiles") or []))
print("farmer", source, "Dutch", target, "occupied", len(occupied), "reason", probe.get("reason"))
for label, avoid in (("all occupied tiles as walls", (occupied | {target}) - {source} - {target}),
                     ("only the merchant tile", set())):
    try:
        path = terrain.travel_path(source, target, avoid=avoid)
        print(f"  path with {label}: {len(path)} steps")
    except ValueError as error:
        print(f"  path with {label}: NONE ({error})")
if probe.get("reason") == "recipient_absent":
    print("  ingress_position:", ingress_position(terrain, probe))
