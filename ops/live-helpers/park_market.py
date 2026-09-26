"""Park the hunting Farmer in Market for a supervised listing session (user
request 09-26 13:1x: "Go to market now").

Same gates as park_city_cleanup.py (reviewed parking): Farming On with a fresh
idle observation, the hunting route running, no natural town trip or pending
banking/delivery, no merchant input or unsettled merchant transaction. It
pauses Farming, halts the route with its own tagged Stop marker, parks inside
Phoenix city with safe_reload.park(require_city=True) under the route
controller lock, then runs the bot's own verified Market leg
(meteor_banking.trip with the saved Conductress plan). Farming stays Off in
Market; only its own marker is removed. If anything fails before Market,
Farming is switched back On so the Farmer is never left idle in the field.
Run elevated. Usage: python park_market.py
"""

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import threading
import time
import ctypes

SOURCE = Path(r"C:\Users\Floor\Documents\ChatGPT\Conquest-releases\2026.09.26-smooth-r65")
HERE = Path(__file__).resolve().parent
ROOT = Path(
    r"C:\Users\Floor\AppData\Local\Packages\OpenAI.Codex_2p2nqsd0c76g0"
    r"\LocalCache\Local\Conquest"
)
PROFILE = "6c98e401-89a9-4d8a-9bfd-9a2ed7880d26"
CHAR = ROOT / "characters" / PROFILE
FARMER = (18676, 134348994817060548)
TAG = "Operator Market parking for supervised listing"
RESULT = HERE / f"park-market-{int(time.time())}.json"
STATE = {}
os.environ["CONQUEST_DATA_ROOT"] = str(ROOT)
os.environ["CONQUEST_PROFILE_ID"] = PROFILE
# Run the running release's exact code the way the app does (pinned manifest).
os.environ["CONQUEST_APP_ROOT"] = str(SOURCE)
os.environ["CONQUEST_RELEASE_MANIFEST_SHA256"] = hashlib.sha256(
    (SOURCE / "release-manifest.json").read_bytes()
).hexdigest()
sys.path.insert(0, str(SOURCE / "src"))
os.chdir(SOURCE)

from conquest.discord_notify import read_json, write_json, process_alive  # noqa: E402
from conquest.worker import request  # noqa: E402
from conquest.merchants.bridge import request as merchant  # noqa: E402
from conquest.merchants.delivery_operation import guard_reload  # noqa: E402
from conquest.merchants.delivery_journey import pending as journey_pending  # noqa: E402
from conquest.meteor_banking import pending as meteor_pending, trip  # noqa: E402
from conquest.manual_storage_recovery import pending as manual_storage_pending  # noqa: E402
from conquest.storage_overflow import pending as overflow_pending  # noqa: E402
from conquest.storage_halt import active as storage_halt_active  # noqa: E402
from conquest.win32 import WindowsBackend  # noqa: E402
from conquest.route_controller import controller_guard  # noqa: E402
from conquest.overnight import OvernightLoop  # noqa: E402
from conquest.safe_reload import park, validate_handoff  # noqa: E402


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def mark(phase, **fields):
    STATE.update(phase=phase, at=time.time(), **fields)
    write_json(RESULT, STATE)


def merchant_idle():
    row = merchant({"action": "status"})
    require(
        not any(
            row.get(k)
            for k in ("input_owner", "handoff_granted", "handoff_active", "manual_handoff")
        ),
        "Merchant or manual input owns the surface",
    )
    path = ROOT / "machine-state/reports/merchants/journal.sqlite3"
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=3) as db:
        rows = db.execute(
            "SELECT id, phase FROM transactions WHERE phase NOT IN "
            "('verified','aborted','operator_overridden')"
        ).fetchall()
    require(not rows, f"Merchant transaction requires reconciliation: {rows}")


def no_trip():
    guard_reload()
    require(
        not any(
            (
                journey_pending(),
                meteor_pending(),
                manual_storage_pending(),
                overflow_pending(),
                storage_halt_active(),
            )
        ),
        "Pending banking, storage, or delivery requires reconciliation",
    )
    visit = read_json(CHAR / "reports/banking/town-visit.json") or {}
    require(
        visit.get("phase") == "complete",
        "Natural town trip in progress; parking deferred",
    )


def main():
    backend = WindowsBackend()
    app = read_json(CHAR / "reports/desktop-farming/app-state.json") or {}
    info = Path(app.get("worker_info_path") or "")
    require(info.is_file(), "Worker info missing")
    health = request(info, "health")
    data = health["embedded_controls"]
    intent = data["control"]
    target = health["target"]
    require(
        (target.get("pid"), target.get("creation_time_100ns")) == FARMER
        and backend.identity(FARMER[0]) == target
        and health.get("profile_id") == PROFILE,
        "Farmer identity changed",
    )
    require(
        intent["enabled"] is True
        and not intent.get("paused")
        and not data.get("manual_mouse")
        and not data.get("manual_input_fence")
        and data.get("observations_available")
        and 0 <= time.time() - data.get("observed_at", 0) <= 1,
        "Farming On with fresh idle observation is required",
    )
    stop = CHAR / ".runtime/overnight.stop"
    require(not stop.exists(), "Existing Stop must be preserved")
    status = read_json(CHAR / "reports/overnight/status.json") or {}
    route = app.get("selected_route")
    require(
        isinstance(route, str)
        and route
        and status.get("route") == route
        and status.get("phase") in ("hunting", "starting")
        and process_alive(status.get("pid")) is not False,
        "Native hunting route is not running",
    )
    no_trip()
    merchant_idle()
    plan = dict((read_json(CHAR / "reports/banking/merchant-journey.json") or {})["route"]["outbound"])
    require(
        plan.get("verified") is True
        and plan.get("source_map") == 1011
        and plan.get("destination_map") == 1036
        and plan.get("npc") == "Conductress",
        "Saved Market leg is not the verified Phoenix Conductress route",
    )
    plan["activity"] = "Operator: heading to Market for a supervised listing session"
    revision = intent["revision"]
    mark("ready_to_pause", revision=revision, target=target, route=route)
    paused = request(info, "controls", {"enabled": False})
    require(
        paused["revision"] == revision + 1 and paused["enabled"] is False,
        "Farming control changed while pausing",
    )
    with stop.open("x", encoding="utf-8") as stream:
        stream.write(TAG)
    try:
        deadline = time.monotonic() + 25
        while True:
            current = request(info, "health")["embedded_controls"]
            state = read_json(CHAR / "reports/overnight/status.json") or {}
            require(
                current["control"]["revision"] == paused["revision"]
                and current["control"]["enabled"] is False
                and not current.get("manual_mouse")
                and not current.get("manual_input_fence")
                and stop.read_text(encoding="utf-8") == TAG,
                "Manual control or parking Stop changed",
            )
            if not current.get("external_execution") and (
                state.get("phase") == "stopped" or process_alive(state.get("pid")) is False
            ):
                break
            require(time.monotonic() < deadline, "Route did not release input")
            time.sleep(0.1)
        with controller_guard() as acquired:
            require(acquired, "Route controller lock remains held")
            no_trip()
            loop = OvernightLoop(route)
            loop.refresh()
            trace = HERE / "park-market-travel.jsonl"

            def record(event, **fields):
                with trace.open("a", encoding="utf-8") as stream:
                    stream.write(
                        json.dumps({"at": time.time(), "event": event, **fields}, default=str)
                        + "\n"
                    )

            loop.record = record
            # The walk in from the hunting area took 419 s for 215 tiles (13:08).
            limit = time.monotonic() + 1200

            def check_stop():
                require(
                    time.monotonic() < limit
                    and not storage_halt_active()
                    and stop.exists()
                    and stop.read_text(encoding="utf-8") == TAG
                    and not ctypes.windll.user32.GetAsyncKeyState(0x7B) & 0x8000,
                    "Parking canceled by Stop or deadline",
                )
                fresh = request(info, "health")
                d = fresh["embedded_controls"]
                c = d["control"]
                require(
                    fresh["target"] == target
                    and c["revision"] == paused["revision"]
                    and c["enabled"] is False
                    and not c.get("paused")
                    and not d.get("manual_mouse")
                    and not d.get("manual_input_fence"),
                    "Farmer identity, manual input, or intent changed during parking",
                )

            loop.check_stop = check_stop
            # park()'s deadline covers the walk in from the field (~9 min live).
            proof = park(
                loop,
                threading.Event(),
                lambda note: mark("parking", note=note),
                seconds=1100,
                require_city=True,
            )
            check_stop()
            validate_handoff(info, proof)
            mark("parked_in_city", proof=proof)
            trip(loop, plan)
            check_stop()
            life = request(info, "health")["embedded_controls"]["life"]
            require(
                life.get("map_id") == 1036 and life.get("dead_candidate") is False,
                "Market arrival was not verified",
            )
        merchant_idle()
        mark("in_market", position=life.get("position"), pause_revision=paused["revision"])
    finally:
        # Only our own marker is removed; a user Stop is never touched.
        if stop.exists() and stop.read_text(encoding="utf-8") == TAG:
            stop.unlink()


def restore_prior_intent():
    """Never leave a paused Farmer idle in the field after a failed park."""
    if STATE.get("phase") not in ("ready_to_pause", "parking", "parked_in_city"):
        return "not paused by this helper"
    if (CHAR / ".runtime/overnight.stop").exists():
        return "user Stop present; left Off"
    app = read_json(CHAR / "reports/desktop-farming/app-state.json") or {}
    info = Path(app.get("worker_info_path") or "")
    health = request(info, "health")
    d = health["embedded_controls"]
    c = d["control"]
    life = d.get("life") or {}
    if (
        (health["target"].get("pid"), health["target"].get("creation_time_100ns")) != FARMER
        or c["enabled"] is not False
        or c.get("paused")
        or d.get("manual_mouse")
        or d.get("manual_input_fence")
        or life.get("dead_candidate") is not False
    ):
        return "farmer changed or manual input; left as is"
    if life.get("map_id") == 1036:
        return "already in Market; left Off"
    current = request(info, "controls", {"enabled": True, "explicit_restart": True})
    return f"restored Farming On revision {current.get('revision')}"


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        try:
            restored = restore_prior_intent()
        except Exception as restore_error:  # report, never mask the original
            restored = f"restore failed: {restore_error}"
        mark("failed", error_type=type(error).__name__, error=str(error), restored=restored)
        raise
