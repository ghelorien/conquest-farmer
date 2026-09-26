"""Switch Farming On with the explicit-restart marker (as start_r51.py does)."""
import json
import os
import sys
from pathlib import Path

ROOT = Path(
    r"C:\Users\Floor\AppData\Local\Packages\OpenAI.Codex_2p2nqsd0c76g0"
    r"\LocalCache\Local\Conquest"
)
CHAR = ROOT / "characters" / "6c98e401-89a9-4d8a-9bfd-9a2ed7880d26"
os.environ["CONQUEST_DATA_ROOT"] = str(ROOT)
sys.path.insert(0, r"C:\Users\Floor\Documents\ChatGPT\cf-smooth\src")
from conquest.discord_notify import read_json  # noqa: E402
from conquest.worker import request as worker  # noqa: E402

app = read_json(CHAR / "reports/desktop-farming/app-state.json") or {}
info = Path(app["worker_info_path"])
embedded = worker(info, "health")["embedded_controls"]
if embedded.get("manual_mouse") or embedded.get("manual_input_fence"):
    raise SystemExit("Manual input is active; Farming not switched On")
current = worker(info, "controls", {"enabled": True, "explicit_restart": True})
print(json.dumps({"enabled": current.get("enabled"), "revision": current.get("revision")}))
