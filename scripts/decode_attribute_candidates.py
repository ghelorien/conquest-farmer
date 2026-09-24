import base64, json, struct
from pathlib import Path
from conquest.worker import request

root = Path(__file__).resolve().parents[1]
info = root / ".runtime/memory-worker.json"
health = request(info, "health")
base = health["modules"][0]["base"]


def read(a, n):
    r = request(info, "read-block", {"address": hex(a), "size": n})
    return base64.b64decode(r["data"], validate=True)


def u64(a):
    return struct.unpack("<Q", read(a, 8))[0]


def rol(v, n):
    n &= 31
    return ((v << n) | (v >> ((32 - n) & 31))) & 0xFFFFFFFF


shared = u64(base + 0x69C730)
player = u64(shared)
if u64(player) != base + 0x5CEF60:
    raise ValueError("Player type changed")
attrs = u64(player + 0x968)
header = read(attrs, 24)
mode, count = struct.unpack_from("<II", header, 8)
table = struct.unpack_from("<Q", header, 16)[0]
if not 1 <= count <= 1024 or mode not in range(4):
    raise ValueError(f"Invalid attribute bounds: {mode},{count}")
data = read(table, count * 4)
values = []
for i in range(count):
    if mode == 0:
        j = i
    elif mode == 1:
        j = count - 1 - i
    elif mode == 2:
        j = i // 2 + (count // 2 + 1 if count & 1 else 0)
    else:
        j = i // 2 + (count // 2 if not count & 1 else 0)
    if j >= count:
        break
    encoded = struct.unpack_from("<I", data, j * 4)[0]
    v = rol(encoded, j if mode in (1, 3) else -j)
    values.append({"index": i, "value": v, "storage_index": j})
if (
    u64(player + 0x968) != attrs
    or read(attrs, 24) != header
    or read(table, count * 4) != data
):
    raise ValueError("Attributes changed during sample")
if u64(base + 0x69C730) != shared or u64(shared) != player:
    raise ValueError("Player changed")
if request(info, "health")["target"] != health["target"]:
    raise ValueError("Process changed")
result = {
    "qualified": False,
    "process_identity": health["target"],
    "player": hex(player),
    "attributes": hex(attrs),
    "mode": mode,
    "count": count,
    "values": values,
}
(root / "reports/attribute-candidates-current.json").write_text(
    json.dumps(result, indent=2)
)
print(json.dumps(result, indent=2))
