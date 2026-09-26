"""Read-only diagnostic: sample raw fields of non-Role scene objects."""
import collections
import struct
from pathlib import Path

RUNTIME = Path(
    r"C:\Users\Floor\AppData\Local\Packages\OpenAI.Codex_2p2nqsd0c76g0\LocalCache"
    r"\Local\Conquest\characters\6c98e401-89a9-4d8a-9bfd-9a2ed7880d26\.runtime"
)
info_path = max(RUNTIME.glob("embedded-worker-*.json"), key=lambda p: p.stat().st_mtime)

from conquest.memory_health import HealthWorkerSession
from conquest.memory_entities import MemoryEntityReader
from conquest.worker import request

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
fx, fy = health["embedded_controls"]["life"]["position"]
shown = collections.Counter()
for obj in objects:
    raw = session.read_block(obj, 0x140)
    rva = struct.unpack_from("<Q", raw)[0] - base
    if rva in (0x5E12D0, 0x5EA728) or shown[rva] >= 4:
        continue
    shown[rva] += 1
    # Scan for plausible tile pairs near the farmer anywhere in the record.
    hits = []
    for off in range(8, 0x138, 4):
        x, y = struct.unpack_from("<II", raw, off)
        if 0 < x < 2048 and 0 < y < 2048 and max(abs(x - fx), abs(y - fy)) <= 40:
            hits.append((hex(off), (x, y)))
    text = "".join(chr(b) if 32 <= b < 127 else "." for b in raw[0x20:0xC0])
    print(hex(rva), hex(obj), "tile-like:", hits[:4], "text:", text[:120])
