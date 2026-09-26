"""Pause or resume one merchant's refill engine. Usage: refill_toggle.py <name> <on|off>"""
import os
import sys
import time
from pathlib import Path

ROOT = Path(
    r"C:\Users\Floor\AppData\Local\Packages\OpenAI.Codex_2p2nqsd0c76g0"
    r"\LocalCache\Local\Conquest"
)
os.environ["CONQUEST_DATA_ROOT"] = str(ROOT)
sys.path.insert(0, r"C:\Users\Floor\Documents\ChatGPT\cf-smooth\src")
from conquest.merchants.bridge import request as merchant  # noqa: E402

name, state = sys.argv[1], sys.argv[2] == "on"
print(merchant({"action": "refill-enabled", "character": name, "enabled": state}))
time.sleep(3)
s = merchant({"action": "status"})
d = s["characters"][name]
print(name, "refill enabled:", (d.get("refill") or {}).get("enabled"), "| input owner:", s.get("input_owner"),
      "| work:", (d.get("foreground_refill_1078") or {}).get("state"), (d.get("foreground_refill_1078") or {}).get("blocker"))
