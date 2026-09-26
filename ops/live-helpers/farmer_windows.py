"""Read-only: list the farmer client's open GUI windows through the worker.

Diagnostic only (never used for input): the worker RPC is slower than the
render loop, so frame recency is checked with a wide tolerance.
"""
import json
import struct
from pathlib import Path

RUNTIME = Path(
    r"C:\Users\Floor\AppData\Local\Packages\OpenAI.Codex_2p2nqsd0c76g0\LocalCache"
    r"\Local\Conquest\characters\6c98e401-89a9-4d8a-9bfd-9a2ed7880d26\.runtime"
)
info_path = max(RUNTIME.glob("embedded-worker-*.json"), key=lambda p: p.stat().st_mtime)

from conquest.memory_health import HealthWorkerSession
from conquest.merchants.memory import GuiReader
from conquest.worker import request

health = request(info_path, "health")
session = HealthWorkerSession(info_path, health["expected_sha256"])
gui = GuiReader.for_session(session)
s = session


def u(address, fmt):
    return struct.unpack(fmt, s.read_block(address, struct.calcsize(fmt)))


context = u(gui.base + gui.context_rva, "<Q")[0]
count, capacity, array = struct.unpack("<IIQ", s.read_block(context + 0x3E58, 16))
addresses = struct.unpack("<" + "Q" * count, s.read_block(array, count * 8))
frame = u(context + 0x3E38, "<I")[0]
life = health["embedded_controls"]["life"]
print("position", life["position"], "status", hex(life["status"]), "frame", frame, "count", count)
points = [(668, 466), (668, 402), (732, 530), (796, 498), (444, 546), (956, 290)]
for ptr in addresses:
    raw = s.read_block(ptr, 0x250)
    last_frame = struct.unpack_from("<I", raw, 0x248)[0]
    visible = raw[0x97]
    name = (
        s.read_block(struct.unpack_from("<Q", raw)[0], 128).split(b"\0")[0].decode("utf-8", "replace")
    )
    x, y, w, h = struct.unpack_from("<4f", raw, 0x18)
    recent = frame - last_frame
    hits = [p for p in points if x <= p[0] <= x + w and y <= p[1] <= y + h]
    if visible and recent < 400:
        print(
            json.dumps(
                {
                    "name": name,
                    "frames_ago": recent,
                    "geometry": [round(x), round(y), round(w), round(h)],
                    "covers_route_clicks": hits,
                }
            )
        )
