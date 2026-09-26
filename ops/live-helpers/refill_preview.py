"""Read-only: the bot's own refill preview for each merchant (no input)."""
import json
import os
import sys
from pathlib import Path

ROOT = Path(
    r"C:\Users\Floor\AppData\Local\Packages\OpenAI.Codex_2p2nqsd0c76g0"
    r"\LocalCache\Local\Conquest"
)
os.environ["CONQUEST_DATA_ROOT"] = str(ROOT)
sys.path.insert(0, r"C:\Users\Floor\Documents\ChatGPT\cf-smooth\src")

from conquest.merchants.bridge import request as merchant  # noqa: E402

out = Path(__file__).resolve().parent
for name in sys.argv[1:] or ["Dutch", "Spiritual"]:
    try:
        result = merchant({"action": "merchant-refill-preview-1078", "character": name})
    except Exception as error:  # noqa: BLE001 - diagnostic output only
        print(name, "preview error:", type(error).__name__, str(error)[:300])
        continue
    (out / f"refill-preview-{name}.json").write_text(
        json.dumps(result, indent=1, default=str), encoding="utf-8"
    )
    print(name, "keys:", list(result)[:25])
    for key in ("eligible", "blocker", "blockers", "reason", "capability", "next", "plan", "candidates"):
        if key in result:
            print("  ", key, "=", json.dumps(result[key], default=str)[:600])
