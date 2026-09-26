"""Stop the running route for a release swap (explicit Farming Off), then wait.

Refuses unless the farmer is alive in Market/Phoenix with no merchant input
owner, handoff or pending merchant transaction. Records deploy-stop-<id>.json
so restart_release.py clears only this deploy's Stop marker.
Usage: python deploy_stop.py <release-id>
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


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


release_id = sys.argv[1]
app = read_json(CHAR / "reports/desktop-farming/app-state.json") or {}
info = Path(app["worker_info_path"])
embedded = worker(info, "health")["embedded_controls"]
life = embedded.get("life") or {}
require(
    life.get("map_id") in (1036, 1011)
    and life.get("dead_candidate") is False
    and not embedded.get("manual_mouse")
    and not embedded.get("manual_input_fence"),
    f"Farmer is not alive in Market/Phoenix or manual input is active: {life}",
)
status = merchant({"action": "status"})
require(
    status.get("input_owner") is None
    and not status.get("handoff_active")
    and not status.get("handoff_granted"),
    "A merchant holds input or a handoff",
)
journal = ROOT / "machine-state/reports/merchants/journal.sqlite3"
with sqlite3.connect(journal.as_uri() + "?mode=ro", uri=True, timeout=3) as db:
    open_rows = db.execute(
        "SELECT id, phase FROM transactions WHERE phase NOT IN "
        "('verified','aborted','operator_overridden')"
    ).fetchall()
require(not open_rows, f"Merchant transactions need reconciliation: {open_rows}")
requested_at = time.time()
write_json(HERE / f"deploy-stop-{release_id}.json", {"requested_at": requested_at, "farmer": life})
current = worker(info, "controls", {"enabled": False, "explicit_stop": True})
print("control", current.get("enabled"), "revision", current.get("revision"))
deadline = time.monotonic() + 90
while time.monotonic() < deadline:
    route = read_json(CHAR / "reports/overnight/status.json") or {}
    if not process_alive(route.get("pid")) or route.get("phase") in ("stopped", "needs_attention"):
        print("route", route.get("phase"), "after", round(time.time() - requested_at, 1), "s")
        break
    time.sleep(1)
else:
    raise SystemExit("Route did not stop within 90 s")
