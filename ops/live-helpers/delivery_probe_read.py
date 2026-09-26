"""Read-only: the delivery-target probe for Dutch and the delivery plan inputs."""
import json
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

for name in ("Dutch",):
    try:
        probe = merchant({"action": "delivery-target", "character": name})
        print(name, "probe:", json.dumps({k: v for k, v in probe.items() if k != "occupied_tiles"}, default=str)[:900])
        print("   occupied tiles:", len(probe.get("occupied_tiles") or []))
    except Exception as error:  # noqa: BLE001 - diagnostic output only
        print(name, "probe error:", type(error).__name__, str(error)[:400])
status = merchant({"action": "status"})
farmer = status.get("farmer") or status.get("manual_farmer") or {}
print("farmer keys:", list(farmer)[:20])
for key in ("delivery", "farmer_delivery", "delivery_readiness"):
    if key in status:
        print(key, json.dumps(status[key], default=str)[:500])
try:
    print("readiness:", json.dumps(merchant({"action": "delivery-readiness"}), default=str)[:600])
except Exception as error:  # noqa: BLE001
    print("readiness error:", type(error).__name__, str(error)[:300])
