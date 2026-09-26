"""Set the farmer's automatic merchant delivery (the app's own setting).
Usage: python delivery_toggle.py <on|off>"""
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
from conquest.merchants.farmer_preferences import set_delivery_enabled, enabled, rollout_enabled  # noqa: E402

set_delivery_enabled("Parasite", sys.argv[1] == "on")
print("farmer automatic delivery:", enabled("Parasite"), "rollout:", rollout_enabled("Parasite"))
