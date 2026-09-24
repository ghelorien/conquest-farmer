import json
import struct
from pathlib import Path
from conquest.worker import request

root = Path(__file__).resolve().parents[1]
info = root / ".runtime/memory-worker.json"
health = request(info, "health")
base = health["modules"][0]["base"]


def read(address, size):
    result = bytearray()
    for offset in range(0, size, 512):
        fields = [
            {"name": str(a), "address": hex(a), "kind": "u64"}
            for a in range(address + offset, address + min(offset + 512, size), 8)
        ]
        result.extend(
            b"".join(
                struct.pack("<Q", f["value"][0])
                for f in request(info, "sample", {"fields": fields})["fields"]
            )
        )
    return bytes(result)


scan = json.loads((root / "reports/hp-213-scan.json").read_text())
objects = [int(a, 16) - 0xA4 for a in scan["candidates"]["player_name"]["addresses"]]
matches = [o for o in objects if struct.unpack("<Q", read(o, 8))[0] == base + 0x5CEF40]
if len(matches) != 1:
    raise ValueError("Expected one player object")
player = matches[0]
data = read(player, 4096)
(root / "reports/player-candidate-baseline.bin").write_bytes(data)
module = read(base + 0x690000, 0x10000)
links = []
for offset in range(0, len(module) - 7, 8):
    value = struct.unpack_from("<Q", module, offset)[0]
    if value == player:
        links.append(hex(0x690000 + offset))
result = {
    "qualified": False,
    "process_identity": health["target"],
    "object": hex(player),
    "direct_player_root_rvas": links,
    "hp_213_u32_offsets": [
        hex(i)
        for i in range(len(data) - 3)
        if struct.unpack_from("<I", data, i)[0] == 213
    ],
    "hp_213_f32_offsets": [
        hex(i)
        for i in range(len(data) - 3)
        if struct.unpack_from("<f", data, i)[0] == 213
    ],
}
(root / "reports/player-root-trace.json").write_text(json.dumps(result, indent=2))
print(json.dumps(result, indent=2))
