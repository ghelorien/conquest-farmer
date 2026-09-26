"""Read-only: one /health observation from the live farmer embedded worker."""
import json
import sys
import urllib.request
from pathlib import Path

RUNTIME = Path(
    r"C:\Users\Floor\AppData\Local\Packages\OpenAI.Codex_2p2nqsd0c76g0\LocalCache"
    r"\Local\Conquest\characters\6c98e401-89a9-4d8a-9bfd-9a2ed7880d26\.runtime"
)
info_path = max(RUNTIME.glob("embedded-worker-*.json"), key=lambda p: p.stat().st_mtime)
info = json.loads(info_path.read_text(encoding="utf-8"))
call = urllib.request.Request(
    f"http://127.0.0.1:{info['port']}/health",
    data=b"{}",
    headers={"Content-Type": "application/json", "X-Conquest-Token": info["token"]},
)
opener = urllib.request.OpenerDirector()
for handler in (
    urllib.request.HTTPHandler(),
    urllib.request.HTTPDefaultErrorHandler(),
    urllib.request.HTTPErrorProcessor(),
):
    opener.add_handler(handler)
with opener.open(call, timeout=30) as response:
    health = json.load(response)

out = Path(sys.argv[1]) if len(sys.argv) > 1 else None
if out:
    out.write_text(json.dumps(health, indent=1, default=str), encoding="utf-8")


def show(value, prefix="", depth=0):
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, (dict, list)) and depth < 2:
                size = len(item)
                print(f"{prefix}{key}: <{type(item).__name__} {size}>")
                if size <= 12 or key in ("window", "panels", "life", "embedded_controls"):
                    show(item, prefix + "  ", depth + 1)
            else:
                text = json.dumps(item, default=str)
                print(f"{prefix}{key}: {text[:200]}")
    elif isinstance(value, list):
        for index, item in enumerate(value[:12]):
            text = json.dumps(item, default=str)
            print(f"{prefix}[{index}] {text[:200]}")


show(health)
