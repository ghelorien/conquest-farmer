"""One-use controller replacement onto the cleanup release. Run elevated.

Modelled on the reviewed r38 reload helper. It sends no game input, never
touches a trade, and launches the new controller with Farming Off. It only
proceeds at a safe point: Farmer Off, alive and in Market; no merchant input
owner, handoff, manual session, open trade/request or pending transaction.
Rollback: `release.py rollback --data-root ROOT` restores the prior release.
"""

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

SOURCE = Path(__file__).resolve().parents[1]
ROOT = Path(
    r"C:\Users\Floor\AppData\Local\Packages\OpenAI.Codex_2p2nqsd0c76g0"
    r"\LocalCache\Local\Conquest"
)
PROFILE = "6c98e401-89a9-4d8a-9bfd-9a2ed7880d26"
CHAR = ROOT / "characters" / PROFILE
RESULT = SOURCE / ".runtime/deploy-cleanup.json"
TITLE = "Conquest \u2014 Parasite \u00b7 Spiritual \u00b7 Dutch"
GAMES = {
    "Parasite": (18532, 134345064188672222, 458796),
    "Spiritual": (632952, 134345856627182944, 474678740),
    "Dutch": (635124, 134345856693697656, 1310752),
}
CLIENT = r"C:\Program Files\Classic Conquer 2.0\bin\64\ImConquer.exe"
# Spiritual disconnected to the login screen at ~20:13 on 2026-09-24.
LOGGED_OUT_OK = {"Spiritual"}
FARMER_DEAD_OK = "--farmer-dead" in sys.argv
BRIDGE_DOWN = "--merchant-bridge-down" in sys.argv
BRIDGE_DOWN_PID = None  # set to the old controller pid in main()
ALLOW_PENDING = {
    sys.argv[i + 1]
    for i, arg in enumerate(sys.argv[:-1])
    if arg == "--allow-pending"
}
os.environ["CONQUEST_DATA_ROOT"] = str(ROOT)
os.environ["CONQUEST_PROFILE_ID"] = PROFILE
sys.path.insert(0, str(SOURCE / "src"))
from conquest.discord_notify import read_json, write_json, process_alive  # noqa: E402
from conquest.memory import MemorySession  # noqa: E402
from conquest.memory_build_layout import CLIENT_SHA256_1078  # noqa: E402
from conquest.merchants.trade_reader_1078 import manual_ownership  # noqa: E402
from conquest.merchants.manual_sessions import canonical_ownership  # noqa: E402
from conquest.merchants.bridge import request as merchant  # noqa: E402
from conquest.worker import request  # noqa: E402
from conquest.win32 import WindowsBackend  # noqa: E402
from conquest.release import (  # noqa: E402
    verify_release,
    activate_release,
    launch_active_release,
)

SETTLED = "('verified','aborted','operator_overridden')"


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def database(path):
    require(path.is_file(), "Required journal missing: " + path.name)
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    return db


def journal_proof():
    route = read_json(CHAR / "reports/overnight/status.json") or {}
    require(
        type(route.get("pid")) is int and process_alive(route["pid"]) is False,
        "A route controller process is still running",
    )
    launch = read_json(CHAR / ".runtime/route-controller-launch.json") or {}
    require(
        not launch.get("pid") or process_alive(launch["pid"]) is False,
        "The launched route controller is still running",
    )
    pending = {}
    for name, path in (
        ("deliveries", CHAR / "reports/banking/merchant-deliveries.sqlite3"),
        ("merchants", ROOT / "machine-state/reports/merchants/journal.sqlite3"),
    ):
        with database(path) as db:
            rows = db.execute(
                f"SELECT id, phase FROM transactions WHERE phase NOT IN {SETTLED}"
            ).fetchall()
            # An explicitly named uncertain delivery may stay held across a
            # controller-only reload (as r38 did); it is reconciled afterwards.
            pending[name] = [
                dict(r)
                for r in rows
                if not (r["id"] in ALLOW_PENDING and r["phase"] == "uncertain")
            ]
    require(
        not any(pending.values()),
        f"Pending transactions need reconciliation: {pending}",
    )
    banking = CHAR / "reports/banking"
    hashes = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted([*banking.glob("*.json"), *banking.glob("*.jsonl")])
    }
    return {"hashes": hashes, "route_phase": route.get("phase")}


def user_stop_clear():
    require(
        not (CHAR / ".runtime/overnight.stop").exists()
        and not ctypes.windll.user32.GetAsyncKeyState(0x7A) & 0x8000
        and not ctypes.windll.user32.GetAsyncKeyState(0x7B) & 0x8000,
        "Manual Stop supersedes this controller reload",
    )


def farmer_safe_place(life, monsters):
    """Market, or inside Phoenix city (y <= 258) with no monster within 18.

    r27 lost the Farmer at (245,264), just outside the saved PhoenixCity
    boundary, during a controller-restart gap.
    """
    if life.get("map_id") == 1036:
        return True
    position = life.get("position") or [0, 999]
    # (193,266) is the Phoenix respawn / Market-return arrival point.
    at_respawn = max(abs(a - b) for a, b in zip(position, (193, 266))) <= 3
    if life.get("map_id") != 1011 or (position[1] > 258 and not at_respawn):
        return False
    return not any(
        isinstance(m, dict)
        and m.get("position")
        and max(abs(a - b) for a, b in zip(m["position"], position)) <= 18
        for m in monsters
    )


def live_controller(backend, pid):
    user_stop_clear()
    app = read_json(CHAR / "reports/desktop-farming/app-state.json") or {}
    info = Path(app.get("worker_info_path") or "")
    require(
        app.get("pid") == pid
        and info.is_file()
        and info.name == f"embedded-worker-{pid}.json"
        and app.get("manual_stop_revision") is None,
        "Controller identity or manual Stop changed",
    )
    windows = [
        w for w in backend.windows(pid) if w["visible"] and w["title"] == TITLE
    ]
    require(len(windows) == 1, "Expected one exact controller window")
    h = request(info, "health")
    d = h["embedded_controls"]
    c = d["control"]
    life = d.get("life") or {}
    require(
        h.get("profile_id") == PROFILE
        and h["target"] == backend.identity(GAMES["Parasite"][0])
        and h["window"]["hwnd"] == GAMES["Parasite"][2]
        and c["enabled"] is False
        and not c.get("paused")
        and d.get("external_execution") is False
        and not d.get("manual_mouse")
        and not d.get("manual_input_fence")
        and d.get("observations_available")
        and 0 <= time.time() - d.get("observed_at", 0) <= 2
        and (
            (
                farmer_safe_place(life, d.get("monsters") or [])
                and life.get("dead_candidate") is False
                and life.get("current_hp", 0) > life.get("max_hp", 0) * 0.6
            )
            or (
                # A ghost cannot be harmed during the restart gap; the route's
                # own Revive runs after Farming is switched On again.
                FARMER_DEAD_OK
                and life.get("map_id") == 1011
                and life.get("dead_candidate") is True
                and life.get("current_hp") == 0
            )
        ),
        "Farmer is not Off, alive and healthy in Market, or manual input is active: "
        + json.dumps(
            {
                "enabled": c.get("enabled"),
                "paused": c.get("paused"),
                "external": d.get("external_execution"),
                "manual_mouse": d.get("manual_mouse"),
                "fence": d.get("manual_input_fence"),
                "observations": d.get("observations_available"),
                "age": time.time() - d.get("observed_at", 0),
                "life": {k: life.get(k) for k in ("map_id", "current_hp", "dead_candidate")},
            },
            default=str,
        ),
    )
    if BRIDGE_DOWN and pid == BRIDGE_DOWN_PID:
        # Only the old controller, whose bridge thread died: prove it is gone.
        # stable() still reads every merchant's trade/request from memory and
        # journal_proof() requires settled merchant transactions.
        require(
            not (ROOT / "machine-state/.runtime/merchants/bridge.json").exists(),
            "Merchant bridge is up; drop --merchant-bridge-down",
        )
        return {
            "identity": backend.identity(pid),
            "window": windows[0],
            "health": h,
            "merchant_status": None,
        }
    s = merchant({"action": "status"})
    manual = merchant({"action": "manual-status"})
    from conquest.merchants.handoff import qualified_listing_request

    require(
        s.get("input_owner") is None
        # As r33: a waiting, ungranted, qualified listing request holds no input.
        and (s.get("handoff_requested") is None or qualified_listing_request(s))
        and s.get("handoff_granted") is False
        and s.get("handoff_active") is False
        and not s.get("manual_handoff")
        and not manual.get("sessions")
        and not (manual.get("farmer") or {}).get("session")
        and not (manual.get("farmer") or {}).get("input_fenced")
        and all(not v.get("manual_input_fence") for v in s["characters"].values()),
        "Controller has active merchant input, handoff or manual ownership",
    )
    return {
        "identity": backend.identity(pid),
        "window": windows[0],
        "health": h,
        "merchant_status": s,
    }


def native_observation(backend):
    observed = {}
    user = ctypes.WinDLL("user32", use_last_error=True)
    get_class = user.GetClassNameW
    get_class.argtypes = (ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int)
    get_class.restype = ctypes.c_int
    for name, (pid, created, hwnd) in GAMES.items():
        identity = backend.identity(pid)
        require(
            identity["creation_time_100ns"] == created
            and identity["path"].casefold() == CLIENT.casefold(),
            name + " game process changed",
        )
        titles = [w["title"] for w in backend.windows(pid) if w["hwnd"] == hwnd]
        if name in LOGGED_OUT_OK and titles == ["[ClassicConquer]"]:
            # A merchant already at the login screen cannot hold a trade;
            # record it and never read its (absent) character memory.
            observed[name] = {"logged_out": True, "canonical": "logged_out"}
            continue
        require(
            titles == [f"[{name} - ClassicConquer]"],
            name + " exact logged-in native HWND changed",
        )
        label = ctypes.create_unicode_buffer(128)
        require(
            get_class(hwnd, label, len(label)) and label.value == "ImGuiShell",
            name + " native window class changed",
        )
        with MemorySession(pid, CLIENT_SHA256_1078) as session:
            snap = manual_ownership(session, name)
        require(
            snap["identity"] == identity
            and snap["map_id"] in ((1036, 1011) if name == "Parasite" else (1036,))
            and (snap["hp"] > 0 or (FARMER_DEAD_OK and name == "Parasite"))
            and 0 <= time.time() - snap["timestamp"] <= 2
            and snap["trade"] is None
            and snap["request"] is None,
            name + " is not idle in Market with no trade or request",
        )
        observed[name] = {
            "snapshot": snap,
            "canonical": canonical_ownership(snap, require_closed=True),
        }
    return observed


def stable(backend):
    user_stop_clear()
    first = native_observation(backend)
    second = native_observation(backend)
    require(
        all(
            first[n]["canonical"] == second[n]["canonical"]
            and (
                first[n].get("logged_out")
                or first[n]["snapshot"]["timestamp"]
                < second[n]["snapshot"]["timestamp"]
            )
            for n in GAMES
        ),
        "Native ownership did not stabilize",
    )
    return second


def merchant_owners(allowed):
    user = ctypes.WinDLL("user32", use_last_error=True)
    get_window = user.GetWindow
    get_window.argtypes = (ctypes.c_void_p, ctypes.c_uint)
    get_window.restype = ctypes.c_void_p
    owners = {}
    for name in ("Dutch", "Spiritual"):
        owners[name] = int(get_window(GAMES[name][2], 4) or 0)
        require(
            owners[name] in allowed, name + " HWND belongs to an unexpected controller"
        )
    return owners


def main(release, dry_run):
    global RESULT
    RESULT = SOURCE / ".runtime" / f"deploy-{release.name}.json"
    require(not RESULT.exists(), "This exact deployment was already attempted")
    verified = verify_release(release)
    backend = WindowsBackend()
    app = read_json(CHAR / "reports/desktop-farming/app-state.json") or {}
    old_pid = app.get("pid")
    require(type(old_pid) is int, "No running controller recorded")
    global BRIDGE_DOWN_PID
    BRIDGE_DOWN_PID = old_pid
    proof = journal_proof()
    old = live_controller(backend, old_pid)
    initial = stable(backend)
    owners = merchant_owners((0, old["window"]["hwnd"]))
    metrics_path = CHAR / "reports/desktop-farming/kill-session.json"
    metrics = read_json(metrics_path)
    require(metrics and metrics.get("stopped_at") is None, "No active kill-session")
    if dry_run:
        life = old["health"]["embedded_controls"]["life"]
        print(
            json.dumps(
                {
                    "dry_run": "all safe-point checks passed",
                    "release": verified.get("manifest_sha256"),
                    "old_controller": old["identity"],
                    "farmer": {
                        k: life.get(k) for k in ("map_id", "position", "current_hp", "max_hp")
                    },
                    "merchant_owners": owners,
                    "route_phase": proof["route_phase"],
                },
                default=str,
                indent=1,
            )
        )
        return
    last = live_controller(backend, old_pid)
    before_close = stable(backend)
    require(
        last["identity"] == old["identity"]
        and journal_proof() == proof
        and read_json(metrics_path) == metrics,
        "Controller, journals or metrics changed before close",
    )
    record = {
        "phase": "closing",
        "at": time.time(),
        "release": str(release),
        "old_controller": old["identity"],
        "journal_proof": proof,
        "kill_session": metrics,
        "observations": {"initial": initial, "before_close": before_close},
        "observation_notice": "Controller reload may create a sales observation "
        "gap. Native snapshots are observations, not sales or transfer receipts.",
    }
    write_json(RESULT, record)
    subprocess.run(
        [
            sys.executable,
            "-B",
            str(SOURCE / "scripts/close_controller.py"),
            "--pid",
            str(old_pid),
            "--created",
            str(old["identity"]["creation_time_100ns"]),
            "--hwnd",
            str(old["window"]["hwnd"]),
            "--data-root",
            str(ROOT),
        ],
        check=True,
        # close_controller derives the expected title from the profiles.
        env={**os.environ, "PYTHONPATH": str(SOURCE / "src")},
    )
    deadline = time.monotonic() + 45
    while process_alive(old_pid):
        require(
            backend.identity(old_pid) == old["identity"]
            and time.monotonic() < deadline,
            "Old controller failed normal close",
        )
        user_stop_clear()
        time.sleep(0.2)
    detached = stable(backend)
    record["observations"]["detached"] = detached
    record.update(phase="detached", at=time.time())
    write_json(RESULT, record)
    merchant_owners((0,))
    require(journal_proof() == proof, "Journals changed during normal close")
    closed = read_json(metrics_path)
    require(
        {k: v for k, v in closed.items() if k != "stopped_at"}
        == {k: v for k, v in metrics.items() if k != "stopped_at"}
        and type(closed.get("stopped_at")) in (int, float),
        "Normal close altered kill-session counters or provenance",
    )
    # Keep the same measured session across a controller-only replacement.
    closed["stopped_at"] = None
    write_json(metrics_path, closed)
    user_stop_clear()
    activate_release(release, state_root=ROOT)
    record.update(phase="activated", at=time.time())
    write_json(RESULT, record)
    launched = launch_active_release(
        [
            "--profile-id",
            PROFILE,
            "--embed-client",
            "--client-pid",
            str(GAMES["Parasite"][0]),
            "--client-started",
            str(GAMES["Parasite"][1]),
            "--client-hwnd",
            str(GAMES["Parasite"][2]),
        ],
        state_root=ROOT,
    )
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        user_stop_clear()
        app = read_json(CHAR / "reports/desktop-farming/app-state.json") or {}
        pid = app.get("pid")
        if type(pid) is int and pid != old_pid:
            try:
                new = live_controller(backend, pid)
            except (OSError, ValueError, KeyError):
                time.sleep(0.3)
                continue
            final = stable(backend)
            record["observations"]["after_restart"] = final
            record["merchant_owners_after"] = merchant_owners(
                (0, new["window"]["hwnd"])
            )
            require(journal_proof() == proof, "New controller changed journals")
            record.update(
                phase="launched_off",
                at=time.time(),
                new_controller=new["identity"],
                launcher_pid=launched.pid,
                farming_enabled=False,
            )
            write_json(RESULT, record)
            print("launched_off", json.dumps(new["identity"]))
            return
        time.sleep(0.3)
    raise ValueError("New controller did not verify Off in time")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--farmer-dead", action="store_true")
    parser.add_argument("--allow-pending", action="append", default=[])
    parser.add_argument("--merchant-bridge-down", action="store_true")
    args = parser.parse_args()
    try:
        main(args.release, args.dry_run)
    except Exception as error:
        if not args.dry_run:
            prior = read_json(RESULT) or {}
            prior.update(
                phase="failed",
                at=time.time(),
                error_type=type(error).__name__,
                error=str(error),
            )
            write_json(RESULT, prior)
        raise
