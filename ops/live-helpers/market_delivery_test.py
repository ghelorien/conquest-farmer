"""Supervised Market delivery test with the farmer parked in Market (user
request 09-26: make refill work smoothly; "Be creative, just make it work").

Runs the bot's own delivery code on the running release under the route
controller lock, exactly as a Market visit would: delivery_route.market_storage
plans deliveries for carried eligible valuables, approaches the merchant
(remembered trade spot, crowd-aware travel), trades and verifies both
inventories. With nothing eligible to deliver it only runs approach_merchant
toward Dutch, which is the step that kept stalling. Farming stays Off.
Run elevated. Usage: python market_delivery_test.py <release-id>
"""

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time

RELEASE = sys.argv[1]
SOURCE = Path(r"C:\Users\Floor\Documents\ChatGPT\Conquest-releases") / RELEASE
HERE = Path(__file__).resolve().parent
ROOT = Path(
    r"C:\Users\Floor\AppData\Local\Packages\OpenAI.Codex_2p2nqsd0c76g0"
    r"\LocalCache\Local\Conquest"
)
PROFILE = "6c98e401-89a9-4d8a-9bfd-9a2ed7880d26"
CHAR = ROOT / "characters" / PROFILE
RESULT = HERE / f"market-delivery-test-{int(time.time())}.json"
TRACE = HERE / "market-delivery-test.jsonl"
os.environ["CONQUEST_DATA_ROOT"] = str(ROOT)
os.environ["CONQUEST_PROFILE_ID"] = PROFILE
os.environ["CONQUEST_APP_ROOT"] = str(SOURCE)
os.environ["CONQUEST_RELEASE_MANIFEST_SHA256"] = hashlib.sha256(
    (SOURCE / "release-manifest.json").read_bytes()
).hexdigest()
sys.path.insert(0, str(SOURCE / "src"))
os.chdir(SOURCE)

from conquest.discord_notify import read_json, write_json, process_alive  # noqa: E402
from conquest.worker import request  # noqa: E402
from conquest.merchants.bridge import request as merchant  # noqa: E402
from conquest.route_controller import controller_guard  # noqa: E402
from conquest.overnight import OvernightLoop  # noqa: E402
from conquest.merchants import delivery_route  # noqa: E402

STATE = {}


def mark(phase, **fields):
    STATE.update(phase=phase, at=time.time(), **fields)
    write_json(RESULT, STATE)


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def main():
    app = read_json(CHAR / "reports/desktop-farming/app-state.json") or {}
    info = Path(app.get("worker_info_path") or "")
    health = request(info, "health")
    data = health["embedded_controls"]
    life = data.get("life") or {}
    target = health["target"]
    control = data["control"]
    route = read_json(CHAR / "reports/overnight/status.json") or {}
    require(not process_alive(route.get("pid")), "Route is running; park first")
    require(
        control["enabled"] is False
        and not control.get("paused")
        and not data.get("manual_mouse")
        and not data.get("manual_input_fence")
        and life.get("map_id") == 1036
        and life.get("dead_candidate") is False,
        "Farmer must be Off, alive and parked in Market",
    )
    status = merchant({"action": "status"})
    require(
        status.get("input_owner") is None and not status.get("handoff_active"),
        "A merchant holds input",
    )
    journal = ROOT / "machine-state/reports/merchants/journal.sqlite3"
    with sqlite3.connect(journal.as_uri() + "?mode=ro", uri=True, timeout=3) as db:
        rows = db.execute(
            "SELECT id, phase FROM transactions WHERE phase NOT IN "
            "('verified','aborted','operator_overridden')"
        ).fetchall()
    require(not rows, f"Merchant transaction requires reconciliation: {rows}")
    revision = control["revision"]
    mark("ready", farmer=life.get("position"))
    with controller_guard() as acquired:
        require(acquired, "Route controller lock remains held")
        loop = OvernightLoop(app.get("selected_route") or "bandit")
        loop.refresh()

        def record(event, **fields):
            with TRACE.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"at": time.time(), "event": event, **fields}, default=str) + "\n")

        loop.record = record
        limit = time.monotonic() + 600

        def check_stop():
            require(time.monotonic() < limit, "Test deadline reached")
            fresh = request(info, "health")
            d = fresh["embedded_controls"]
            c = d["control"]
            require(
                fresh["target"] == target
                and c["revision"] == revision
                and c["enabled"] is False
                and not d.get("manual_mouse")
                and not d.get("manual_input_fence"),
                "Farmer identity, manual input, or intent changed during the test",
            )

        loop.check_stop = check_stop
        # The route switches terrain on arrival in Market; do the same.
        from conquest.city_travel import CLIENT_ROOT
        from conquest.navigation import read_terrain

        loop.terrain = read_terrain(CLIENT_ROOT, 1036)
        carried = loop.town("supplies")
        mark("carried", items=[i.get("name") for i in carried["items"]])
        started = time.monotonic()
        delivered = delivery_route.market_storage(loop)
        mark("market_storage_done", seconds=round(time.monotonic() - started, 1),
             delivered=[{"merchant": r.get("merchant"), "items": [i.get("name") for i in r.get("items", [])]} for r in delivered or []])
        if not delivered:
            dutch = merchant({"action": "status"})["characters"]["Dutch"]["snapshot"]
            plan = {"merchant": "Dutch", "position": dutch["position"]}
            started = time.monotonic()
            reached = delivery_route.approach_merchant(loop, plan, merchant)
            mark("approach_only", reached=reached, seconds=round(time.monotonic() - started, 1),
                 spot=delivery_route.remembered_spot("Dutch", dutch["position"]))
        life = request(info, "health")["embedded_controls"]["life"]
        mark("finished", farmer=life.get("position"), map_id=life.get("map_id"))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        mark("failed", error_type=type(error).__name__, error=str(error)[:500])
        raise
