"""Read-only diagnostic: every scene object near the farmer, grouped by class."""
import collections
import json
import struct
import sys
from pathlib import Path

RUNTIME = Path(
    r"C:\Users\Floor\AppData\Local\Packages\OpenAI.Codex_2p2nqsd0c76g0\LocalCache"
    r"\Local\Conquest\characters\6c98e401-89a9-4d8a-9bfd-9a2ed7880d26\.runtime"
)
info_path = max(RUNTIME.glob("embedded-worker-*.json"), key=lambda p: p.stat().st_mtime)

from conquest.memory_health import HealthWorkerSession
from conquest.memory_entities import MemoryEntityReader
from conquest.worker import request

radius = int(sys.argv[1]) if len(sys.argv) > 1 else 14
health = request(info_path, "health")
session = HealthWorkerSession(info_path, health["expected_sha256"])
reader = MemoryEntityReader.for_session(session)
base, collection, _ = reader._resolve()
p = reader.layout
begin, end = (
    struct.unpack("<Q", session.read_block(collection + o, 8))[0]
    for o in (p.begin_offset, p.end_offset)
)
entries = session.read_block(begin, end - begin)
objects = [struct.unpack_from("<Q", entries, i + 8)[0] for i in range(0, len(entries), 16)]
life = health["embedded_controls"]["life"]
fx, fy = life["position"]
print("farmer", life["position"], "objects", len(objects))
by_vtable = collections.Counter()
rows = []
for obj in objects:
    raw = session.read_block(obj, 0x100)
    vtable = struct.unpack_from("<Q", raw)[0]
    rva = vtable - base
    by_vtable[hex(rva)] += 1
    uid = struct.unpack_from("<I", raw, 0x78)[0]
    kind = struct.unpack_from("<I", raw, 0x80)[0]
    name = raw[0xA4:0xA4 + 64].split(b"\0")[0].decode("utf-8", "replace")
    x, y = struct.unpack_from("<II", raw, 0xE8)
    dx, dy = struct.unpack_from("<ii", raw, 0xF8)
    if 0 < x < 2048 and 0 < y < 2048 and max(abs(x - fx), abs(y - fy)) <= radius:
        rows.append((max(abs(x - fx), abs(y - fy)), hex(rva), uid, kind, name, (x, y), (dx, dy)))
print("classes", dict(by_vtable))
for row in sorted(rows):
    print(json.dumps({"d": row[0], "class": row[1], "uid": row[2], "kind": row[3],
                      "name": row[4], "tile": row[5], "draw": row[6]}, ensure_ascii=False))
