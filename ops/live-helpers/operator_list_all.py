"""List every listable item for both merchants while the farmer is parked
(user request 09-26: listing must work before farming resumes).

Each listing goes through the bot's own merchant-booth-list-once-1078 path,
which re-checks every safety rule itself (route exited, farmer Off and alive
in Market, closed trade/request windows, exact reliable price, exact price
buffer before OK). This loop picks the highest-value queued item from the
bot's read-only refill preview and waits for its receipt:
- verified: next item;
- aborted before input: retry that item once;
- uncertain with no OK marker (price key not taken): the bot's exact Cancel
  closes the dialog with stock unchanged, then the item is retried once;
- anything else (an OK may have been pressed): stop for reconciliation.
An item that fails three times is left for the day (the engine's own rule),
so it no longer blocks the items below it.
Usage: python operator_list_all.py [max_minutes] [merchant ...]
"""

import json
import os
import sqlite3
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(
    r"C:\Users\Floor\AppData\Local\Packages\OpenAI.Codex_2p2nqsd0c76g0"
    r"\LocalCache\Local\Conquest"
)
PROFILE = "6c98e401-89a9-4d8a-9bfd-9a2ed7880d26"
CHAR = ROOT / "characters" / PROFILE
HERE = Path(__file__).resolve().parent
os.environ["CONQUEST_DATA_ROOT"] = str(ROOT)
os.environ["CONQUEST_PROFILE_ID"] = PROFILE
sys.path.insert(0, r"C:\Users\Floor\Documents\ChatGPT\cf-smooth\src")

from conquest.discord_notify import read_json, process_alive  # noqa: E402
from conquest.merchants.bridge import request as merchant  # noqa: E402
from conquest.worker import request as worker  # noqa: E402

ITEM_FIELDS = ("uid", "type_id", "name", "plus", "gem1", "gem2", "bound", "quantity")
TERMINAL = ("verified", "aborted", "uncertain", "operator_overridden")
SKIP_TYPES = {1088000}  # DragonBall
LOG = HERE / "operator-list-all.jsonl"


def log(**row):
    row["at"] = time.time()
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, default=str) + "\n")


def say(*parts):
    print(time.strftime("%H:%M:%S"), *parts, flush=True)


def safe_to_list():
    route = read_json(CHAR / "reports/overnight/status.json") or {}
    app = read_json(CHAR / "reports/desktop-farming/app-state.json") or {}
    embedded = worker(Path(app["worker_info_path"]), "health")["embedded_controls"]
    life = embedded.get("life") or {}
    journal = ROOT / "machine-state/reports/merchants/journal.sqlite3"
    with sqlite3.connect(journal.as_uri() + "?mode=ro", uri=True, timeout=3) as db:
        pending = db.execute(
            "SELECT id, phase FROM transactions WHERE phase NOT IN "
            "('verified','aborted','operator_overridden')"
        ).fetchall()
    problems = {
        "route_running": bool(process_alive(route.get("pid"))),
        "farming_on": embedded["control"]["enabled"] is not False,
        "not_in_market": life.get("map_id") != 1036,
        "farmer_not_alive": life.get("dead_candidate") is not False,
        "manual_input": bool(embedded.get("manual_mouse") or embedded.get("manual_input_fence")),
        "pending_tx": pending,
    }
    return {k: v for k, v in problems.items() if v}


def status(name, request_id):
    return merchant(
        {"action": "merchant-booth-list-once-status-1078", "character": name, "request_id": request_id}
    )


def settle(name, request_id, deadline):
    result = {}
    while time.monotonic() < deadline:
        time.sleep(2)
        result = status(name, request_id)
        if result.get("phase") in TERMINAL:
            break
    return result


def cancel(name, request_id):
    merchant({"action": "merchant-booth-list-once-cancel-1078", "character": name, "request_id": request_id})
    result = settle(name, request_id, time.monotonic() + 40)
    ok = (
        result.get("phase") == "aborted"
        and (result.get("result") or {}).get("stock_unchanged") is True
        and (result.get("result") or {}).get("listing_submitted") is False
    )
    return ok, result


def attempt(name, choice, snapshot):
    item = next((i for i in snapshot["inventory"] if i["uid"] == choice["uid"]), None)
    if item is None:
        return "stale_preview", {}
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
    log(event="submit", character=name, item=item["name"], plus=item["plus"], price=body["price"],
        request_id=body["request_id"])
    started = time.monotonic()
    try:
        merchant(body)
    except ValueError as error:  # MerchantRejected: refused before any input
        log(event="refused", character=name, item=item["name"], reason=str(error)[:200])
        say(name, item["name"], "refused before input:", str(error)[:160])
        return "aborted", {"refused": str(error)}
    result = settle(name, body["request_id"], started + 150)
    phase = result.get("phase")
    reason = (result.get("result") or {}).get("reason")
    log(event="result", character=name, item=item["name"], price=body["price"], request_id=body["request_id"],
        phase=phase, reason=reason, confirmation=result.get("confirmation_marker"),
        refill_qualified=result.get("routine_refill_qualified"), seconds=round(time.monotonic() - started, 1))
    say(name, item["name"], f"+{item['plus']}", f"{body['price']:,}", "->", phase,
        f"({round(time.monotonic() - started)} s)", reason or "")
    if phase == "uncertain" and not result.get("confirmation_marker"):
        ok, closed = cancel(name, body["request_id"])
        log(event="cancel", character=name, request_id=body["request_id"], ok=ok, phase=closed.get("phase"))
        say("   exact Cancel:", "closed, stock unchanged" if ok else json.dumps(closed, default=str)[:300])
        return ("cancelled" if ok else "cancel_failed"), closed
    return phase, result


limit = time.monotonic() + 60 * float(sys.argv[1] if len(sys.argv) > 1 else 90)
names = sys.argv[2:] or ["Dutch", "Spiritual"]
listed, failed = {}, {}
tries = {}
for name in names:
    while time.monotonic() < limit:
        problems = safe_to_list()
        if problems:
            say("stopping: not safe to list:", problems)
            log(event="stopped_unsafe", problems=problems)
            sys.exit(2)
        preview = merchant({"action": "merchant-refill-preview-1078", "character": name})
        if not preview.get("booth_open") or preview.get("booth_free_slots", 0) < 1:
            say(name, "booth full or closed")
            break
        queue = [
            row for row in preview.get("queue") or []
            if row["uid"] not in failed.get(name, set())
        ]
        snapshot = merchant({"action": "status"})["characters"][name]["snapshot"]
        if not queue:
            say(name, "nothing left to list (skipped:", len(failed.get(name, set())), "failed/DragonBall excluded)")
            break
        choice = queue[0]
        outcome, detail = attempt(name, choice, snapshot)
        if outcome == "verified":
            listed[name] = listed.get(name, 0) + 1
            continue
        if outcome in ("aborted", "cancelled", "stale_preview"):
            tries[choice["uid"]] = tries.get(choice["uid"], 0) + 1
            if tries[choice["uid"]] >= 3:  # the engine's own stuck-item rule
                failed.setdefault(name, set()).add(choice["uid"])
            time.sleep(5)
            continue
        say("stopping: needs reconciliation:", outcome, json.dumps(detail, default=str)[:400])
        log(event="stopped_unverified", character=name, outcome=outcome)
        sys.exit(3)
say("listed:", listed, "| gave up on:", {k: len(v) for k, v in failed.items()})
log(event="finished", listed=listed, failed={k: sorted(v) for k, v in failed.items()})
