"""Compare a bounded monster attribute candidate; never enables attacks."""

import json
from pathlib import Path
import struct
from conquest.memory_health import HealthWorkerSession, decode_attribute
from conquest.worker import request

state = json.loads(Path("reports/desktop-farming/app-state.json").read_text())
info = state["worker_info_path"]
health = request(info, "health")
session = HealthWorkerSession(info, health["expected_sha256"])
base = health["modules"][0]["base"]
rows = []
for monster in health["embedded_controls"]["monsters"][:8]:
    obj = monster["object_address"]
    row = {
        "id": monster["entity_id"],
        "type": monster["type_id"],
        "name": monster["name"],
        "max_hp": monster["max_hp"],
    }
    try:
        record = session.read_block(obj, 0x988)
        if (
            struct.unpack_from("<Q", record)[0] != base + 0x5C5E20
            or struct.unpack_from("<I", record, 0x78)[0] != monster["entity_id"]
        ):
            raise ValueError("Actor changed")
        ptr = struct.unpack_from("<Q", record, 0x978)[0]
        header = session.read_block(ptr, 24)
        mode, count = struct.unpack_from("<II", header, 8)
        if mode not in range(4) or not 2 <= count <= 1024:
            raise ValueError("No compatible attribute table at +0x978")
        table_ptr = struct.unpack_from("<Q", header, 16)[0]
        table = session.read_block(table_ptr, count * 4)
        hp = decode_attribute(table, mode, count, 1)
        if (
            session.read_block(ptr, 24) != header
            or session.read_block(table_ptr, count * 4) != table
        ):
            raise ValueError("Attribute table changed")
        if session.read_block(obj + 0x78, 4) != record[0x78:0x7C]:
            raise ValueError("Actor ID changed")
        row.update(
            candidate_hp=hp,
            in_bounds=0 <= hp <= monster["max_hp"],
            mode=mode,
            count=count,
        )
    except (ValueError, OSError) as error:
        row["error"] = str(error)
    rows.append(row)
result = {"qualified": False, "process_identity": health["target"], "rows": rows}
Path("reports/monster-health-candidates.json").write_text(
    json.dumps(result, indent=2), encoding="utf-8"
)
print(json.dumps(result, indent=2))
