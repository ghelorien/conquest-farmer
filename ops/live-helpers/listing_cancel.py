"""Run the bot's exact one-shot listing cancellation and wait for its result.

merchant-booth-list-once-cancel-1078 presses only the price dialog's Cancel,
after verifying the exact request, unchanged ownership and that the typed
partial price is a prefix of this request's price; its once-only marker
prevents any replay. Usage: python listing_cancel.py <character> <request_id>
"""
import json
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

name, request_id = sys.argv[1], sys.argv[2]
body = {"action": "merchant-booth-list-once-cancel-1078", "character": name, "request_id": request_id}
print("cancel:", json.dumps(merchant(body), default=str)[:500])
status = {}
for _ in range(15):
    time.sleep(2)
    status = merchant(
        {"action": "merchant-booth-list-once-status-1078", "character": name, "request_id": request_id}
    )
    if status.get("phase") in ("aborted", "verified", "operator_overridden") or status.get("cancel_failure"):
        break
print("final:", json.dumps(status, default=str)[:1500])
