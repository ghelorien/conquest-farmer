"""Read-only dry run of route_crowd against the live farmer scene (no input)."""
import sys
import time
from pathlib import Path

RUNTIME = Path(
    r"C:\Users\Floor\AppData\Local\Packages\OpenAI.Codex_2p2nqsd0c76g0\LocalCache"
    r"\Local\Conquest\characters\6c98e401-89a9-4d8a-9bfd-9a2ed7880d26\.runtime"
)
info_path = max(RUNTIME.glob("embedded-worker-*.json"), key=lambda p: p.stat().st_mtime)

from conquest.city_travel import CLIENT_ROOT
from conquest.memory_health import HealthWorkerSession
from conquest.navigation import read_terrain
from conquest.route_crowd import Crowd
from conquest.viewport import scene_bounds
from conquest.worker import request

goal = tuple(int(v) for v in sys.argv[1:3]) if len(sys.argv) > 2 else (204, 193)
health = request(info_path, "health")
life = health["embedded_controls"]["life"]
source = tuple(life["position"])
viewport = tuple(health["window"]["client_size"])
session = HealthWorkerSession(info_path, health["expected_sha256"])
anchor = (700, 418)
started = time.monotonic()
crowd = Crowd.observe(session, anchor)
print("bodies", len(crowd.bodies), "read", round(time.monotonic() - started, 2), "s")
terrain = read_terrain(CLIENT_ROOT, life["map_id"])
bounds = scene_bounds(viewport)
for target in [(193, 186), (193, 184), (201, 183), (189, 191), (189, 175), (190, 185), (188, 183), (200, 190)]:
    dx, dy = target[0] - source[0], target[1] - source[1]
    click = (anchor[0] + (dx - dy) * 32, anchor[1] + (dx + dy) * 16)
    print("target", target, "click", click, "covered", crowd.covers(click))
started = time.monotonic()
landing = crowd.open_landing(terrain, source, goal, anchor, bounds=bounds)
elapsed = round(time.monotonic() - started, 3)
if landing:
    dx, dy = landing[0] - source[0], landing[1] - source[1]
    print("landing", landing, "click", (anchor[0] + (dx - dy) * 32, anchor[1] + (dx + dy) * 16), "in", elapsed, "s")
else:
    print("no landing", elapsed)
