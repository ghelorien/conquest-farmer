"""Stop the route in Market once no farmer journal is open, for listing.

Same checks the one-shot listing applies (booth_probe_1078._farmer_journals_clear):
Meteor consolidation, delivery probe and qualification-prep journals
terminal, no active delivery route, no protected withdrawal or delivery
operation pending, no override recovery. Plus: route running, farmer alive in
Market, no manual input, no merchant input/handoff, no pending merchant or
farmer delivery transaction. Then explicit Farming Off and wait for exit.
Usage: python market_watch2.py [max_minutes]
"""

import json
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
from conquest.protected_withdrawal import pending as protected_pending  # noqa: E402
from conquest.merchants.delivery_operation import pending as delivery_pending  # noqa: E402
from conquest.merchants.delivery_probe import JOURNAL as probe_path, TERMINAL as probe_terminal  # noqa: E402
from conquest.merchants.trade_qualification_prep import JOURNAL as prep_path, TERMINAL as prep_terminal  # noqa: E402
from conquest.meteor_banking import JOURNAL as meteor_path, TERMINAL as meteor_terminal  # noqa: E402
from conquest.merchants.delivery_route import STATE as route_path  # noqa: E402

SETTLED = "('verified','aborted','operator_overridden')"


def journals_open():
    reasons = []
    if protected_pending():
        reasons.append("protected_withdrawal")
    if delivery_pending():
        reasons.append("delivery_operation")
    for label, path, terminal in (
        ("probe", probe_path, probe_terminal),
        ("prep", prep_path, prep_terminal),
        ("meteor", meteor_path, meteor_terminal),
        ("route", route_path, None),
    ):
        path = Path(path)
        if Path(str(path) + ".override-intent.json").exists():
            reasons.append(label + "_override")
        if not path.exists():
            continue
        state = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            reasons.append(label + "_invalid")
        elif state and (
            state.get("phase") not in terminal if terminal is not None else bool(state.get("active"))
        ):
            reasons.append(f"{label}:{state.get('phase') if terminal is not None else 'active'}")
    return reasons


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
last_note = None
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
            "journals": journals_open(),
            "pending_tx": pending_rows(),
        }
        if any(blockers.values()):
            note = json.dumps(blockers, default=str)
            if note != last_note:
                print(time.strftime("%H:%M:%S"), "in Market, waiting:", note, flush=True)
                last_note = note
            continue
        requested_at = time.time()
        write_json(HERE / "deploy-stop-listing2.json", {"requested_at": requested_at, "farmer": life})
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
