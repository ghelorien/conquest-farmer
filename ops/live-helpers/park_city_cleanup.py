"""Park a hunting Farmer inside Phoenix city for a controller deployment.

Ported from Codex's reviewed deploy_takeover_r33.py (parking phase only).
Pauses Farming, halts the route with a tagged Stop marker, then runs the
qualified safe_reload.park(require_city=True) under the route-controller
lock. Leaves Farming Off in the city, removes only its own marker, and
never closes or launches anything; deploy_cleanup.py does that next.
A natural town trip, pending work, manual input or changed intent defers.
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

SOURCE = Path(__file__).resolve().parents[1]
ROOT = Path(
    r"C:\Users\Floor\AppData\Local\Packages\OpenAI.Codex_2p2nqsd0c76g0"
    r"\LocalCache\Local\Conquest"
)
PROFILE = "6c98e401-89a9-4d8a-9bfd-9a2ed7880d26"
CHAR = ROOT / "characters" / PROFILE
FARMER = (18532, 134345064188672222)
TAG = "Deployment city parking (cleanup)"
RESULT = SOURCE / ".runtime" / f"park-city-{int(time.time())}.json"
STATE = {}
os.environ["CONQUEST_DATA_ROOT"] = str(ROOT)
os.environ["CONQUEST_PROFILE_ID"] = PROFILE
sys.path.insert(0, str(SOURCE / "src"))
# Saved routes and policies load from profiles/ relative to the working
# directory, exactly as the app runs from its release root.
os.chdir(SOURCE)

from conquest.discord_notify import read_json, write_json, process_alive  # noqa: E402
from conquest.worker import request  # noqa: E402
from conquest.merchants.bridge import request as merchant  # noqa: E402
from conquest.merchants.handoff import qualified_listing_request  # noqa: E402
from conquest.merchants.delivery_operation import guard_reload  # noqa: E402
from conquest.merchants.delivery_journey import pending as journey_pending  # noqa: E402
from conquest.meteor_banking import pending as meteor_pending  # noqa: E402
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


def journal_hashes():
    banking = CHAR / "reports/banking"
    paths = sorted([*banking.glob("*.json"), *banking.glob("*.jsonl")])
    require(any(p.name == "town-visit.json" for p in paths), "Town visit journal missing")
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


# An explicitly named uncertain merchant row may stay held across a parking
# for a controller-only reload; the new controller reconciles it afterwards.
ALLOW_PENDING = {
    sys.argv[i + 1] for i, arg in enumerate(sys.argv[:-1]) if arg == "--allow-pending"
}


def merchant_journal_settled():
    path = ROOT / "machine-state/reports/merchants/journal.sqlite3"
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=3) as db:
        rows = db.execute(
            "SELECT id, phase FROM transactions WHERE phase NOT IN "
            "('verified','aborted','operator_overridden')"
        ).fetchall()
        require(
            all(tid in ALLOW_PENDING and phase == "uncertain" for tid, phase in rows),
            "Merchant transaction requires reconciliation",
        )


BRIDGE_DOWN = "--merchant-bridge-down" in sys.argv
BRIDGE_INFO = ROOT / "machine-state/.runtime/merchants/bridge.json"


def idle_merchants():
    if BRIDGE_DOWN:
        # The old controller's bridge thread died (2026-09-25 ~10:19). Prove it
        # is really gone; the settled merchant journal is checked separately.
        require(not BRIDGE_INFO.exists(), "Merchant bridge is up; drop --merchant-bridge-down")
        return
    row = merchant({"action": "status"})
    require(
        not any(
            row.get(k)
            for k in ("input_owner", "handoff_granted", "handoff_active", "manual_handoff")
        ),
        "Merchant or manual input owns the surface",
    )
    require(
        not row.get("handoff_requested") or qualified_listing_request(row),
        "Unknown merchant handoff is pending",
    )
    for name, item in row["characters"].items():
        snapshot = item.get("snapshot") or {}
        require(
            all(
                p.get("id") in ALLOW_PENDING and p.get("phase") == "uncertain"
                for p in item.get("pending") or []
            )
            and not item.get("input_active")
            and not snapshot.get("trade")
            and not snapshot.get("request"),
            f"{name} has an active transaction",
        )


def no_trip(expected):
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
        "Natural town trip in progress; parking deferred without touching its journals",
    )
    require(journal_hashes() == expected, "Banking journals changed; town work may have started")


def kill_session():
    row = read_json(CHAR / "reports/desktop-farming/kill-session.json") or {}
    require(
        type(row.get("cursor")) is int
        and type(row.get("kills")) is int
        and row.get("stopped_at") is None,
        "Active kill metrics are unavailable",
    )
    return row


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
    require(
        not stop.exists()
        and not (read_json(CHAR / ".runtime/reload-resume.json") or {}).get("expires_at", 0)
        > time.time(),
        "Existing Stop or auto-resume intent must be preserved",
    )
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
    hashes = journal_hashes()
    no_trip(hashes)
    merchant_journal_settled()
    idle_merchants()
    session = kill_session()
    revision = intent["revision"]
    mark("ready_to_pause", revision=revision, target=target, route=route, kill_session=session)
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
            no_trip(hashes)
            loop = OvernightLoop(route)
            loop.refresh()
            trace = SOURCE / ".runtime" / "park-city-travel.jsonl"

            def record(event, **fields):
                with trace.open("a", encoding="utf-8") as stream:
                    stream.write(
                        json.dumps({"at": time.time(), "event": event, **fields}, default=str)
                        + "\n"
                    )

            loop.record = record
            limit = time.monotonic() + 180

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
                require(
                    (read_json(CHAR / "reports/banking/town-visit.json") or {}).get("phase")
                    == "complete",
                    "Natural town trip started during parking",
                )

            loop.check_stop = check_stop
            proof = park(
                loop,
                threading.Event(),
                lambda note: mark("parking", note=note),
                seconds=180,
                require_city=True,
            )
            check_stop()
            validate_handoff(info, proof)
            no_trip(hashes)
        idle_merchants()
        merchant_journal_settled()
        mark("parked", proof=proof, pause_revision=paused["revision"])
        print(json.dumps({"parked": True, "proof": proof}, default=str))
    finally:
        # Only our own marker is removed; a user Stop is never touched.
        if stop.exists() and stop.read_text(encoding="utf-8") == TAG:
            stop.unlink()


def restore_prior_intent():
    """Never leave a paused Farmer idle in the field after a failed park."""
    if STATE.get("phase") not in ("ready_to_pause", "parking"):
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
