"""Read a bounded player-object neighborhood for local calibration only."""

import json
import struct
from pathlib import Path
from conquest.worker import request

root = Path(__file__).resolve().parents[1]
info = root / ".runtime/memory-worker.json"
health = request(info, "health")
base = health["modules"][0]["base"]


def sample(addresses):
    return request(
        info,
        "sample",
        {
            "fields": [
                {"name": hex(a), "address": hex(a), "kind": "u64"} for a in addresses
            ]
        },
    )["fields"]


def u64(address):
    return sample([address])[0]["value"][0]


first = u64(base + 0x697970)
player = u64(first)
data = bytearray()
for offset in range(0, 4096, 512):
    data.extend(
        b"".join(
            struct.pack("<Q", f["value"][0])
            for f in sample(list(range(player + offset, player + offset + 512, 8)))
        )
    )
if first != u64(base + 0x697970) or player != u64(first):
    raise ValueError("Player pointer chain changed")
if request(info, "health")["target"] != health["target"]:
    raise ValueError("Process changed")
(root / "reports/player-object-current.bin").write_bytes(data)
result = {
    "qualified": False,
    "first": hex(first),
    "object": hex(player),
    "vtable": hex(struct.unpack_from("<Q", data)[0]),
    "vtable_rva": hex(struct.unpack_from("<Q", data)[0] - base),
    "name": data[0xA4:0xE4].split(b"\0")[0].decode("utf-8", errors="replace"),
    "fields": {
        hex(o): struct.unpack_from("<I", data, o)[0]
        for o in [0x78, 0x80, 0xE8, 0xEC, 0x3E0, 0x6F8, 0xA40]
    },
}
(root / "reports/player-object-current.json").write_text(json.dumps(result, indent=2))
print(json.dumps(result, indent=2))
