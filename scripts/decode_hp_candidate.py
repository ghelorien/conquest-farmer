import base64
import json
import struct
from pathlib import Path
from conquest.worker import request

MASK = 0xFFFFFFFF


def rol(x, n):
    n &= 31
    x &= MASK
    return ((x << n) | (x >> ((32 - n) & 31))) & MASK


def ror(x, n):
    return rol(x, -n)


def decode(data, offset):
    s = struct.unpack_from("<44I", data, offset)
    a, b, c, d = struct.unpack_from("<4I", data, offset + 176)
    a = (a - s[42]) & MASK
    c = (c - s[43]) & MASK
    for i in range(20, 0, -1):
        a, b, c, d = d, a, b, c
        u = rol(d * (2 * d + 1), 5)
        t = rol(b * (2 * b + 1), 5)
        c = ror((c - s[2 * i + 1]) & MASK, t) ^ u
        a = ror((a - s[2 * i]) & MASK, u) ^ t
    d = (d - s[1]) & MASK
    b = (b - s[0]) & MASK
    return [a, b, c, d]


root = Path(__file__).resolve().parents[1]
info = root / ".runtime/memory-worker.json"
health = request(info, "health")
base = health["modules"][0]["base"]


def read(a, n):
    r = request(info, "read-block", {"address": hex(a), "size": n})
    return base64.b64decode(r["data"], validate=True)


shared = struct.unpack("<Q", read(base + 0x69C730, 8))[0]
player = struct.unpack("<Q", read(shared, 8))[0]
data = read(player, 4096)
if struct.unpack_from("<Q", data)[0] != base + 0x5CEF60:
    raise ValueError("Player type mismatch")
result = {
    "qualified": False,
    "process_identity": health["target"],
    "root_rva": "0x69c730",
    "pointer_offsets": [0, 0],
    "object": hex(player),
    "name": data[0x94:0xD4].split(b"\0")[0].decode(),
    "max_hp": struct.unpack_from("<I", data, 0x3D0)[0],
    "candidate_current_hp_words": decode(data, 0x494),
    "other_attribute_words": decode(data, 0x604),
}
words = result["candidate_current_hp_words"]
result["candidate_current_hp"] = (
    sum(w << (8 * i) for i, w in enumerate(words))
    if all(0 <= w <= 255 for w in words)
    else None
)
if (
    struct.unpack("<Q", read(base + 0x69C730, 8))[0] != shared
    or struct.unpack("<Q", read(shared, 8))[0] != player
):
    raise ValueError("Player changed")
if request(info, "health")["target"] != health["target"]:
    raise ValueError("Process changed")
(root / "reports/decoded-hp-candidate.json").write_text(json.dumps(result, indent=2))
(root / "reports/actual-player-current.bin").write_bytes(data)
print(json.dumps(result, indent=2))
for filename in ["player-candidate-baseline.bin", "player-candidate-now.bin"]:
    old = (root / "reports" / filename).read_bytes()
    print(filename, decode(old, 0x4A4))
