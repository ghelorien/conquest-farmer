"""Supervised one-shot booth listing for one merchant (user-approved option 1).

Builds the exact request from fresh read-only state: the bot's own refill
preview picks the highest-value queued item and its planned price; the live
merchant snapshot supplies the item fingerprint and process binding. Submits
through the bot's merchant-booth-list-once-1078 path, which re-checks every
safety rule itself (route exited, farmer Off and alive in Market, closed
trade/request windows, exact price), then polls status until it settles.
Usage: python operator_listing.py <Dutch|Spiritual>
"""

import json
import os
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(
    r"C:\Users\Floor\AppData\Local\Packages\OpenAI.Codex_2p2nqsd0c76g0"
    r"\LocalCache\Local\Conquest"
)
os.environ["CONQUEST_DATA_ROOT"] = str(ROOT)
sys.path.insert(0, r"C:\Users\Floor\Documents\ChatGPT\cf-smooth\src")

from conquest.merchants.bridge import request as merchant  # noqa: E402

ITEM_FIELDS = ("uid", "type_id", "name", "plus", "gem1", "gem2", "bound", "quantity")
HERE = Path(__file__).resolve().parent

name = sys.argv[1]
preview = merchant({"action": "merchant-refill-preview-1078", "character": name})
queue = preview.get("queue") or []
if not queue or not preview.get("booth_open") or preview.get("booth_free_slots", 0) < 1:
    raise SystemExit(f"{name}: nothing to list or booth not open: {preview.get('blockers')}")
choice = queue[0]
state = merchant({"action": "status"})["characters"][name]
snapshot = state["snapshot"]
item = next(i for i in snapshot["inventory"] if i["uid"] == choice["uid"])
body = {
    "action": "merchant-booth-list-once-1078",
    "character": name,
    "request_id": "booth-list1078-op-" + uuid.uuid4().hex[:20],
    "item_uid": item["uid"],
    "item_fingerprint": {key: item[key] for key in ITEM_FIELDS},
    "price": choice["total_listing_price"],
    "expected_identity": snapshot["identity"],
    "expected_character_uid": snapshot["character_uid"],
    "expected_own_booth_uid": snapshot["own_booth_uid"],
}
record = HERE / f"operator-listing-{name}.json"
record.write_text(json.dumps({"submitted_at": time.time(), "body": body}, indent=1), encoding="utf-8")
print(name, "listing", item["name"], "+%d" % item["plus"], "at", body["price"], body["request_id"])
result = merchant(body)
print("submit:", json.dumps(result, default=str)[:600])
status_body = {
    "action": "merchant-booth-list-once-status-1078",
    "character": name,
    "request_id": body["request_id"],
}
deadline = time.monotonic() + 120
status = result
while time.monotonic() < deadline:
    status = merchant(status_body)
    phase = status.get("phase") or status.get("status")
    if phase not in (None, "prepared", "submitted", "running", "in_progress"):
        break
    time.sleep(2)
print("final:", json.dumps(status, default=str)[:1200])
record.write_text(
    json.dumps({"submitted_at": time.time(), "body": body, "final": status}, indent=1, default=str),
    encoding="utf-8",
)
