"""Stop the route as the farmer arrives in Market, for the supervised listing.

Polls read-only farmer health every 2 s. On the first observation in Market
(map 1036) with the route running and nothing in flight (no merchant input
owner or handoff, no pending merchant or farmer delivery transaction, no
active delivery route, no manual input), it records deploy-stop-listing.json
and sends an explicit Farming Off, then waits for the route process to exit.
Arrival is before any walk to a merchant, so no trade can be open yet.
Usage: python market_watch.py [max_minutes]
"""

import os
import sqlite3
import sys
import time
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

from conquest.discord_notify import read_json, write_json, process_alive  # noqa: E402
from conquest.merchants.bridge import request as merchant  # noqa: E402
from conquest.worker import request as worker  # noqa: E402

SETTLED = "('verified','aborted','operator_overridden')"


def pending_rows():
    rows = []
    for path in (
        CHAR / "reports/banking/merchant-deliveries.sqlite3",
        ROOT / "machine-state/reports/merchants/journal.sqlite3",
    ):
        if path.exists():
            with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=3) as db:
                rows += db.execute(
                    f"SELECT id, phase FROM transactions WHERE phase NOT IN {SETTLED}"
                ).fetchall()
    return rows


limit = time.monotonic() + 60 * float(sys.argv[1] if len(sys.argv) > 1 else 180)
while time.monotonic() < limit:
    time.sleep(2)
    try:
        app = read_json(CHAR / "reports/desktop-farming/app-state.json") or {}
        info = Path(app["worker_info_path"])
        embedded = worker(info, "health")["embedded_controls"]
        life = embedded.get("life") or {}
        if life.get("map_id") != 1036:
            continue
        route = read_json(CHAR / "reports/overnight/status.json") or {}
        status = merchant({"action": "status"})
        blockers = {
            "route_not_running": not process_alive(route.get("pid")),
            "dead": life.get("dead_candidate") is not False,
            "manual": bool(embedded.get("manual_mouse") or embedded.get("manual_input_fence")),
            "merchant_input": status.get("input_owner") is not None
            or bool(status.get("handoff_active") or status.get("handoff_granted")),
            "delivery_active": bool((read_json(CHAR / "reports/banking/merchant-route.json") or {}).get("active")),
            "pending_tx": pending_rows(),
        }
        if any(blockers.values()):
            print(time.strftime("%H:%M:%S"), "in Market but not yet safe:", blockers, flush=True)
            continue
        requested_at = time.time()
        write_json(HERE / "deploy-stop-listing.json", {"requested_at": requested_at, "farmer": life})
        current = worker(info, "controls", {"enabled": False, "explicit_stop": True})
        print(time.strftime("%H:%M:%S"), "stop sent at", life.get("position"), "revision", current.get("revision"), flush=True)
        until = time.monotonic() + 90
        while time.monotonic() < until and process_alive(route.get("pid")):
            time.sleep(1)
        route = read_json(CHAR / "reports/overnight/status.json") or {}
        print(time.strftime("%H:%M:%S"), "route", route.get("phase"), "process alive", process_alive(route.get("pid")), flush=True)
        sys.exit(0)
    except (OSError, ValueError, KeyError) as error:
        print(time.strftime("%H:%M:%S"), "poll error:", type(error).__name__, str(error)[:200], flush=True)
print("watch timed out")
sys.exit(1)
