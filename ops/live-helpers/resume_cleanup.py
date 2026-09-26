"""One-use Farming On after the verified r39 controller reload."""

import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(
    r"C:\Users\Floor\AppData\Local\Packages\OpenAI.Codex_2p2nqsd0c76g0"
    r"\LocalCache\Local\Conquest"
)
PROFILE = "6c98e401-89a9-4d8a-9bfd-9a2ed7880d26"
CHAR = ROOT / "characters" / PROFILE
HERE = Path(__file__).resolve().parent
RESULT = HERE / "resume-cleanup.json"
os.environ["CONQUEST_DATA_ROOT"] = str(ROOT)
os.environ["CONQUEST_PROFILE_ID"] = PROFILE
sys.path.insert(0, str(HERE.parent / "src"))

from conquest.discord_notify import read_json, write_json  # noqa: E402
from conquest.merchants.bridge import request as merchant  # noqa: E402
from conquest.memory import MemorySession  # noqa: E402
from conquest.memory_build_layout import CLIENT_SHA256_1078  # noqa: E402
from conquest.merchants.trade_reader_1078 import manual_ownership  # noqa: E402
from conquest.worker import request as worker  # noqa: E402
from conquest.win32 import WindowsBackend  # noqa: E402


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def main():
    global RESULT
    release = sys.argv[1]
    RESULT = HERE / f"resume-{release}.json"
    require(not RESULT.exists(), "This one-use Start was already attempted")
    deploy = read_json(HERE / f"deploy-{release}.json") or {}
    require(
        deploy.get("phase") == "launched_off" and deploy.get("farming_enabled") is False,
        "r39 controller reload is not verified",
    )
    controller = deploy["new_controller"]
    app = read_json(CHAR / "reports/desktop-farming/app-state.json") or {}
    require(
        app.get("pid") == controller["pid"]
        and app.get("manual_stop_revision") is None
        and WindowsBackend().identity(controller["pid"]) == controller
        and not (CHAR / ".runtime/overnight.stop").exists(),
        "Controller identity or manual Stop changed",
    )
    info = Path(app["worker_info_path"])
    health = worker(info, "health")
    embedded = health["embedded_controls"]
    control = embedded["control"]
    life = embedded["life"]
    require(
        health["profile_id"] == PROFILE
        and health["target"]["pid"] == 18532
        and control["enabled"] is False
        and not control.get("paused")
        and not embedded.get("manual_mouse")
        and not embedded.get("manual_input_fence")
        and life["map_id"] in (1036, 1011)
        and life.get("dead_candidate") is False
        and life["current_hp"] > life["max_hp"] * 0.6
        and 0 <= time.time() - embedded["observed_at"] <= 2,
        "Farmer Off, living Market identity, or manual input changed",
    )
    status = merchant({"action": "status"})
    manual = merchant({"action": "manual-status"})
    require(
        status.get("input_owner") is None
        and status.get("handoff_requested") is None
        and not status.get("manual_handoff")
        and not manual.get("sessions")
        and not (manual.get("farmer") or {}).get("session")
        and not (manual.get("farmer") or {}).get("input_fenced"),
        "Merchant input, handoff or manual ownership is active",
    )
    with MemorySession(18532, CLIENT_SHA256_1078) as session:
        farmer = manual_ownership(session, "Parasite")
    require(
        farmer["identity"] == health["target"]
        and farmer["map_id"] in (1036, 1011)
        and 0 <= time.time() - farmer["timestamp"] <= 2
        and farmer["trade"] is None
        and farmer["request"] is None,
        "Fresh native farmer state changed before Start",
    )
    write_json(
        RESULT,
        {
            "phase": "submitting_once",
            "at": time.time(),
            "controller": controller,
            "revision_before": control.get("revision"),
            "life": life,
        },
    )
    current = worker(info, "controls", {"enabled": True, "explicit_restart": True})
    require(
        current["enabled"] is True,
        "Farming control response is uncertain; do not replay Start",
    )
    write_json(
        RESULT,
        {
            "phase": "resumed",
            "at": time.time(),
            "controller": controller,
            "revision": current.get("revision"),
        },
    )
    print(json.dumps({"resumed": True, "revision": current.get("revision")}))


if __name__ == "__main__":
    main()
