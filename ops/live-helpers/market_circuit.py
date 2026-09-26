"""Supervised Market movement circuit with the farmer parked in Market (user
request 09-26: "find a way to move the character smoothly around and reach
the correct spots"; screenshots allowed for troubleshooting).

Runs the running release's own movement code under the route controller lock:
delivery_route.approach_merchant for Dutch's trade view, the verified
approach_market_warehouse corridor, and plain checked travel to the exit tile
beside the Market Controller (no dialog, no teleport). Movement only: no trade,
deposit or withdrawal is ever issued. Every leg is timed; every stall, reroute
and progress recovery is logged, and a stall saves a screenshot of the farmer
client (read-only capture). Merchant refill keeps priority: before each leg the
test waits while a merchant holds input or a refill is due. Farming stays Off.
Run elevated. Usage: python market_circuit.py <release-id> [rounds]
"""

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

RELEASE = sys.argv[1]
ROUNDS = int(sys.argv[2]) if len(sys.argv) > 2 else 1
SOURCE = Path(r"C:\Users\Floor\Documents\ChatGPT\Conquest-releases") / RELEASE
HERE = Path(__file__).resolve().parent
ROOT = Path(
    r"C:\Users\Floor\AppData\Local\Packages\OpenAI.Codex_2p2nqsd0c76g0"
    r"\LocalCache\Local\Conquest"
)
PROFILE = "6c98e401-89a9-4d8a-9bfd-9a2ed7880d26"
CHAR = ROOT / "characters" / PROFILE
STAMP = int(time.time())
RESULT = HERE / f"market-circuit-{STAMP}.json"
TRACE = HERE / f"market-circuit-{STAMP}.jsonl"
SHOTS = HERE / f"market-circuit-{STAMP}"
EXIT = (214, 218)
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
from conquest.meteor_banking import approach_market_warehouse  # noqa: E402

STATE = {"release": RELEASE, "legs": []}
STALL_EVENTS = (
    "town_movement_stalled",
    "travel_progress_recovery",
    "merchant_approach_deferred",
    "market_movement_recovery",
    "town_path_retry",
)


def mark(phase, **fields):
    STATE.update(phase=phase, at=time.time(), **fields)
    write_json(RESULT, STATE)


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def shot(label):
    """Read-only capture of the farmer client; never blocks the travel loop."""
    SHOTS.mkdir(exist_ok=True)
    path = SHOTS / f"{time.strftime('%H%M%S')}-{label}.png"
    subprocess.Popen(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(HERE / "capture_farmer.ps1"),
            "-Out",
            str(path),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=0x08000000,
    )
    return path.name


def journals_clear():
    journal = ROOT / "machine-state/reports/merchants/journal.sqlite3"
    with sqlite3.connect(journal.as_uri() + "?mode=ro", uri=True, timeout=3) as db:
        rows = db.execute(
            "SELECT id, phase FROM transactions WHERE phase NOT IN "
            "('verified','aborted','operator_overridden')"
        ).fetchall()
    require(not rows, f"Merchant transaction requires reconciliation: {rows}")


def merchants_quiet(horizon=75):
    """True when no merchant holds input and no refill is due within horizon."""
    status = merchant({"action": "status"})
    if (
        status.get("input_owner") is not None
        or status.get("handoff_active")
        or status.get("handoff_requested")
        or status.get("manual_handoff")
    ):
        return False, "merchant input or handoff"
    now = time.time()
    for name, row in (status.get("characters") or {}).items():
        refill = (row or {}).get("refill") or {}
        if refill.get("enabled") is not True:
            continue
        due = refill.get("next_check")
        if refill.get("pending") is True or (
            isinstance(due, (int, float)) and due <= now + horizon
        ):
            return False, f"{name} refill due"
    return True, None


def wait_for_merchants(record):
    """Refill has priority; the farmer only waits (Off, reading memory)."""
    started = time.monotonic()
    reason = None
    while time.monotonic() - started < 240:
        quiet, reason = merchants_quiet()
        if quiet:
            if time.monotonic() - started > 1:
                record("circuit_waited_for_merchants", seconds=round(time.monotonic() - started, 1))
            return
        time.sleep(2)
    raise ValueError(f"Merchants stayed busy for 240 s: {reason}")


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
    journals_clear()
    revision = control["revision"]
    mark("ready", farmer=life.get("position"))
    with controller_guard() as acquired:
        require(acquired, "Route controller lock remains held")
        loop = OvernightLoop(app.get("selected_route") or "bandit")
        loop.refresh()
        leg = {}

        def record(event, **fields):
            row = {"at": time.time(), "event": event, **fields}
            if leg and event in STALL_EVENTS + ("route_click_rerouted",):
                leg.setdefault("events", []).append(
                    {k: v for k, v in row.items() if k not in ("activity",)}
                )
                if event in STALL_EVENTS:
                    row["screenshot"] = shot(f"{leg['name']}-{event}")
                    leg.setdefault("screenshots", []).append(row["screenshot"])
            with TRACE.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(row, default=str) + "\n")

        loop.record = record
        limit = time.monotonic() + 1500

        def check_stop():
            require(time.monotonic() < limit, "Circuit deadline reached")
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
        from conquest.city_travel import CLIENT_ROOT
        from conquest.navigation import read_terrain

        loop.terrain = read_terrain(CLIENT_ROOT, 1036)

        def dutch():
            snapshot = merchant({"action": "status"})["characters"]["Dutch"]["snapshot"]
            plan = {"merchant": "Dutch", "position": snapshot["position"]}
            return delivery_route.approach_merchant(loop, plan, merchant)

        def warehouse():
            approach_market_warehouse(loop, "Circuit: Market warehouse")
            return bool((loop.town("vendor-status", vendor_type=0) or {}).get("reachable"))

        def exit_tile():
            loop.travel(EXIT, activity="Circuit: Market exit beside the Controller")
            here = tuple(loop.living()["embedded_controls"]["life"]["position"])
            return max(abs(a - b) for a, b in zip(here, EXIT)) <= 1

        steps = [("dutch", dutch), ("warehouse", warehouse), ("dutch", dutch),
                 ("exit", exit_tile), ("warehouse", warehouse), ("exit", exit_tile)]
        for round_number in range(ROUNDS):
            for name, action in steps:
                wait_for_merchants(record)
                journals_clear()
                before = tuple(loop.living()["embedded_controls"]["life"]["position"])
                leg.clear()
                leg.update(name=name, round=round_number + 1, start=before)
                started = time.monotonic()
                try:
                    leg["reached"] = bool(action())
                except Exception as error:  # a leg failure is data, not a stop
                    if "Farmer identity" in str(error) or "deadline" in str(error):
                        raise
                    leg["reached"] = False
                    leg["error"] = f"{type(error).__name__}: {str(error)[:300]}"
                    leg.setdefault("screenshots", []).append(shot(f"{name}-error"))
                leg["seconds"] = round(time.monotonic() - started, 1)
                leg["end"] = tuple(loop.living()["embedded_controls"]["life"]["position"])
                leg["reroutes"] = sum(
                    e["event"] == "route_click_rerouted" for e in leg.get("events", [])
                )
                leg["stalls"] = sum(
                    e["event"] in STALL_EVENTS for e in leg.get("events", [])
                )
                STATE["legs"].append(dict(leg))
                mark("running")
        mark("finished")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        mark("failed", error_type=type(error).__name__, error=str(error)[:500])
        raise
