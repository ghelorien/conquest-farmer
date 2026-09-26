"""User choice 09-26 12:4x: farm first, batch listing.

Automatic merchant delivery Off for the farmer (the app's own setting, via
farmer_preferences.set_delivery_enabled) and refill Off for both merchants
(the merchant bridge's refill-enabled action). Merchant login recovery and
sales observation are untouched. Prints the resulting state.
"""
import os
import sys
from pathlib import Path

ROOT = Path(
    r"C:\Users\Floor\AppData\Local\Packages\OpenAI.Codex_2p2nqsd0c76g0"
    r"\LocalCache\Local\Conquest"
)
os.environ["CONQUEST_DATA_ROOT"] = str(ROOT)
os.environ["CONQUEST_PROFILE_ID"] = "6c98e401-89a9-4d8a-9bfd-9a2ed7880d26"
sys.path.insert(0, r"C:\Users\Floor\Documents\ChatGPT\cf-smooth\src")

from conquest.merchants.farmer_preferences import (  # noqa: E402
    set_delivery_enabled,
    enabled,
    rollout_enabled,
)
from conquest.merchants.bridge import request as merchant  # noqa: E402

set_delivery_enabled("Parasite", False)
print("farmer automatic delivery:", enabled("Parasite"), "rollout:", rollout_enabled("Parasite"))
for name in ("Dutch", "Spiritual"):
    print(merchant({"action": "refill-enabled", "character": name, "enabled": False}))
s = merchant({"action": "status"})
for name in ("Dutch", "Spiritual"):
    d = s["characters"][name]
    print(name, "connected", d.get("connected"), "refill", (d.get("refill") or {}).get("enabled"),
          "operations", d.get("enabled"), "recovery", (d.get("recovery_safety") or {}).get("active"))
