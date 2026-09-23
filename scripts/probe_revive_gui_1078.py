"""Capture only native-memory Revive candidates on the exact 1078 client.

Read-only: this neither focuses the game nor presses Revive.  It does not
qualify the Revive button by itself.
"""

import argparse
from dataclasses import asdict
import json
from pathlib import Path
from types import SimpleNamespace

if __name__ == "__main__":
    from _bootstrap import activate
    activate(__file__)

from conquest.memory import MemorySession
from conquest.memory_build_layout import CLIENT_SHA256_1078
from conquest.memory_life import MemoryLifeReader
from conquest.merchants.memory import GuiReader


def observe(pid, created):
    with MemorySession(pid, CLIENT_SHA256_1078) as session:
        if session.identity["creation_time_100ns"] != created:
            raise ValueError("Game process identity changed")
        adapter = SimpleNamespace(
            expected_sha256=session.expected_sha256,
            modules=session.modules,
            identity=session.identity,
            read=session.read,
            read_block=session.read,
            assert_identity=session.assert_identity,
        )
        life_reader = MemoryLifeReader.for_session(adapter, "Parasite")
        before = asdict(life_reader.read())
        windows = GuiReader.for_session(adapter).windows()
        after = asdict(life_reader.read())
        stable = ("status", "appearance", "map_id", "position", "current_hp",
                  "max_hp", "revive_gate_value", "ghost_candidate")
        if any(before[key] != after[key] for key in stable):
            raise ValueError("Life changed while reading Revive GUI")
        session.assert_identity()
        return {
            "qualified": False,
            "reason": "Read-only Revive candidate; no 1078 click qualified",
            "identity": session.identity,
            "life": {key: before[key] for key in stable},
            "windows": [{"name": window["name"], "geometry": window["geometry"]}
                        for window in windows[:64]],
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--created", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Refusing to overwrite an existing diagnostic")
    result = observe(args.pid, args.created)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as target:
        json.dump(result, target, indent=2)
    print(json.dumps({"output": str(args.output), "ghost": result["life"]["ghost_candidate"]}))


if __name__ == "__main__":
    main()
