"""Replace the running Conquest controller with a built release (run elevated).

Safe point only: farmer route not running (Farming Off), no merchant input
owner or handoff, no pending merchant transaction. Closes the controller with
a normal WM_CLOSE (same integrity), then runs start_r51.py for the release,
which launches the app detached and switches Farming On once. Every step is
written to restart-<release>.json next to this script.
Usage: python restart_release.py <release-id>
"""

import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

ROOT = Path(
    r"C:\Users\Floor\AppData\Local\Packages\OpenAI.Codex_2p2nqsd0c76g0"
    r"\LocalCache\Local\Conquest"
)
PROFILE = "6c98e401-89a9-4d8a-9bfd-9a2ed7880d26"
CHAR = ROOT / "characters" / PROFILE
HERE = Path(__file__).resolve().parent
SRC = r"C:\Users\Floor\Documents\ChatGPT\cf-smooth\src"
os.environ["CONQUEST_DATA_ROOT"] = str(ROOT)
os.environ["CONQUEST_PROFILE_ID"] = PROFILE
sys.path.insert(0, SRC)

from conquest.discord_notify import read_json, write_json, process_alive  # noqa: E402
from conquest.merchants.bridge import request as merchant  # noqa: E402
from conquest.win32 import WindowsBackend  # noqa: E402
from conquest.worker import request as worker  # noqa: E402

RESULT = None


def mark(**fields):
    write_json(RESULT, {"at": time.time(), **fields})


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def main():
    global RESULT
    release_id = sys.argv[1]
    RESULT = HERE / f"restart-{release_id}.json"
    app = read_json(CHAR / "reports/desktop-farming/app-state.json") or {}
    pid = app.get("pid")
    require(type(pid) is int and process_alive(pid), "No running controller to replace")
    route = read_json(CHAR / "reports/overnight/status.json") or {}
    require(
        not process_alive(route.get("pid")) or route.get("phase") in ("needs_attention", "stopped"),
        "The route controller is running; stop at a safe point first",
    )
    health = worker(Path(app["worker_info_path"]), "health")
    embedded = health["embedded_controls"]
    life = embedded.get("life") or {}
    require(
        embedded["control"]["enabled"] is False
        and not embedded.get("manual_mouse")
        and not embedded.get("manual_input_fence")
        and life.get("map_id") in (1036, 1011)
        and life.get("dead_candidate") is False,
        "Farmer must be Off, alive and in Market or Phoenix",
    )
    status = merchant({"action": "status"})
    require(
        status.get("input_owner") is None
        and not status.get("handoff_active")
        and not status.get("handoff_granted")
        and not status.get("manual_handoff"),
        "A merchant holds input or a handoff",
    )
    journal = ROOT / "machine-state/reports/merchants/journal.sqlite3"
    with sqlite3.connect(journal.as_uri() + "?mode=ro", uri=True, timeout=3) as db:
        open_rows = db.execute(
            "SELECT id, phase FROM transactions WHERE phase NOT IN "
            "('verified','aborted','operator_overridden')"
        ).fetchall()
    require(not open_rows, f"Merchant transactions need reconciliation: {open_rows}")
    backend = WindowsBackend()
    identity = backend.identity(pid)
    windows = [w for w in backend.windows(pid) if w["visible"] and w["title"].startswith("Conquest")]
    require(len(windows) == 1, "Expected one controller window")
    mark(phase="closing", old_controller=identity, farmer=life)
    subprocess.run(
        [
            sys.executable,
            "-B",
            r"C:\Users\Floor\Documents\ChatGPT\cf-smooth\scripts\close_controller.py",
            "--pid",
            str(pid),
            "--created",
            str(identity["creation_time_100ns"]),
            "--hwnd",
            str(windows[0]["hwnd"]),
            "--data-root",
            str(ROOT),
        ],
        check=True,
        env={**os.environ, "PYTHONPATH": SRC},
    )
    deadline = time.monotonic() + 60
    while process_alive(pid):
        require(time.monotonic() < deadline, "Old controller did not close normally")
        time.sleep(0.5)
    mark(phase="closed", old_controller=identity)
    # A deploy stop (deploy-stop-<release>.json, written just before the
    # explicit Farming Off) leaves the route's Stop marker; clear only that one.
    stop = CHAR / ".runtime/overnight.stop"
    deploy_stop = read_json(HERE / f"deploy-stop-{release_id}.json") or {}
    if (
        stop.exists()
        and deploy_stop.get("requested_at")
        and stop.stat().st_mtime >= deploy_stop["requested_at"] - 1
        and stop.read_text(encoding="utf-8") == "Stopped by user: Farming Off"
    ):
        stop.unlink()
        mark(phase="deploy_stop_cleared", old_controller=identity)
    result = subprocess.run(
        [sys.executable, "-B", str(HERE / "start_r51.py"), release_id]
        + (["--no-farming"] if "--no-farming" in sys.argv else []),
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": SRC},
    )
    mark(
        phase="started" if result.returncode == 0 else "start_failed",
        stdout=result.stdout[-2000:],
        stderr=result.stderr[-4000:],
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        if RESULT is not None:
            mark(phase="failed", error_type=type(error).__name__, error=str(error))
        raise
