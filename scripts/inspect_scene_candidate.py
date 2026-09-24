"""Read-only scene diagnostics from the current embedded connection."""

import argparse
from collections import Counter
import json
from pathlib import Path
import yaml
from conquest.addressing import WorkerPointerSession
from conquest.memory_entities import EntityLayout, MemoryEntityReader, sample_fields

parser = argparse.ArgumentParser()
parser.add_argument("--worker-info", required=True, type=Path)
args = parser.parse_args()
layout = EntityLayout.model_validate(
    yaml.safe_load(Path("profiles/classic-1074-entities-candidate.yaml").read_text())
)
session = WorkerPointerSession(args.worker_info, layout.expected_sha256)
reader = MemoryEntityReader(session, layout)
base, collection, trace = reader._resolve()
header = sample_fields(
    session,
    [
        (collection + offset, "u64")
        for offset in (layout.begin_offset, layout.end_offset, layout.capacity_offset)
    ],
)
begin, end, capacity = header
if not begin <= end <= capacity or (end - begin) % 16 or (end - begin) // 16 > 4096:
    raise ValueError("Unexpected scene bounds")
objects = sample_fields(
    session, [(entry + 8, "u64") for entry in range(begin, end, 16)]
)
specs = [
    (0, "u64"),
    (0x78, "u32"),
    (0x80, "u32"),
    (0x94, "utf8"),
    (0xA4, "utf8"),
    (0xD8, "xy_u32"),
    (0xE8, "xy_u32"),
]
vtables = sample_fields(session, [(obj, "u64") for obj in objects])
rows = []
actors = [
    obj for obj, vt in zip(objects, vtables) if vt == base + layout.monster_vtable_rva
]
for obj in actors[:64]:
    try:
        values = sample_fields(
            session, [(obj + offset, kind) for offset, kind in specs]
        )
        rows.append(
            {
                "object": hex(obj),
                "vtable_rva": hex(values[0] - base),
                "id78": values[1],
                "kind80": values[2],
                "name94": values[3],
                "namea4": values[4],
                "posd8": values[5],
                "pose8": values[6],
            }
        )
    except (ValueError, OSError) as error:
        rows.append({"object": hex(obj), "error": str(error)})
root_object = trace[0][1]
root_words = sample_fields(
    session, [(root_object + offset, "u64") for offset in range(0, 256, 8)]
)
result = {
    "collection": hex(collection),
    "trace": [[hex(a), hex(v)] for a, v in trace],
    "root_words": {hex(i * 8): hex(v) for i, v in enumerate(root_words)},
    "object_count": len(objects),
    "vtable_counts": dict(Counter(hex(v - base) for v in vtables)),
    "rows": rows,
}
Path("reports/current-scene-candidates.json").write_text(
    json.dumps(result, indent=2), encoding="utf-8"
)
print(json.dumps({"object_count": len(objects), "rows": rows}, indent=2))
