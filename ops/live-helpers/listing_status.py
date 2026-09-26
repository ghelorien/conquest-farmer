"""Read-only: full status of one one-shot listing request."""
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

name, request_id = sys.argv[1], sys.argv[2]
status = merchant(
    {"action": "merchant-booth-list-once-status-1078", "character": name, "request_id": request_id}
)
print(json.dumps(status, indent=1, default=str)[:4000])
state = merchant({"action": "status"})["characters"][name]
snap = state.get("snapshot") or {}
print("booth:", [(i.get("name"), i.get("price")) for i in snap.get("booth") or []])
print("bag has item:", any(i["uid"] == 295190266 for i in snap.get("inventory") or []))
