"""Read-only comparison of exact 1078 booth code pins in one known process."""

import argparse
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from conquest.memory import MemorySession
from conquest.memory_build_layout import CLIENT_SHA256_1078
from conquest.merchants.booth_target_1078 import _PROJECTION_CODE
from conquest.merchants.observe_1078 import _actor_identity


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--created", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--collect", action="store_true",
                        help="Also run the read-only owned-booth target preflight")
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Refusing to overwrite an existing diagnostic")
    with MemorySession(args.pid, CLIENT_SHA256_1078) as session:
        if session.identity["creation_time_100ns"] != args.created:
            raise ValueError("Target process was replaced")
        modules = [m for m in session.modules if m["name"].casefold() == "imconquer.exe"]
        if len(modules) != 1:
            raise ValueError("Expected exactly one client module")
        module = modules[0]
        character, uid, server = _actor_identity(session)
        pins = []
        for rva, expected_hex in _PROJECTION_CODE.items():
            expected = bytes.fromhex(expected_hex)
            first = session.read(module["base"] + rva, len(expected) + 2)
            second = session.read(module["base"] + rva, len(expected) + 2)
            if first != second:
                raise ValueError(f"Loaded code changed during observation at RVA 0x{rva:x}")
            difference = next((i for i, (left, right) in enumerate(zip(expected, first))
                               if left != right), None)
            pins.append({"rva": hex(rva), "address": hex(module["base"] + rva),
                         "expected_hex": expected_hex,
                         "loaded_hex": first[:len(expected)].hex(),
                         "loaded_next_two_hex": first[len(expected):].hex(),
                         "first_difference": difference, "matches": difference is None})
        target = None
        actor_kind = None
        hit_test_code = None
        if args.collect:
            from conquest.memory_build_layout import entity_reader_layout
            from conquest.memory_entities import MemoryEntityReader
            from conquest.merchants.booth_target_1078 import collect, _owned_scene_actor
            from conquest.merchants.reader_1078 import open_read_only_1078
            snapshot = open_read_only_1078(session, character).read_manual_ownership()
            if (snapshot["identity"] != session.identity
                    or snapshot["character_uid"] != uid
                    or snapshot["character"] != character
                    or server != b"Classic_US"
                    or snapshot["server"] != "America"):
                raise ValueError("Merchant ownership identity changed before booth preflight")
            actor = _owned_scene_actor(
                session, MemoryEntityReader(session, entity_reader_layout(session)),
                snapshot, module)
            kind_reads = {offset: session.read(actor["address"] + offset, 4)
                          for offset in (0x2B0, 0x2C0, 0x2D0)}
            if any(session.read(actor["address"] + offset, 4) != raw
                   for offset, raw in kind_reads.items()):
                raise ValueError("Owned booth hit-test fields changed during observation")
            actor_kind = {"actor_address": hex(actor["address"]),
                          "kind_candidates": {hex(offset): struct.unpack("<I", raw)[0]
                                              for offset, raw in kind_reads.items()}}
            code_start, code_size = 0x1ABE80, 0x1ABFC4 - 0x1ABE80
            code = session.read(module["base"] + code_start, code_size)
            if session.read(module["base"] + code_start, code_size) != code:
                raise ValueError("1078 hit-test code changed during observation")
            hit_test_code = {"start_rva": hex(code_start), "loaded_hex": code.hex()}
            try:
                target = collect(session, snapshot)
            except ValueError as error:
                target = {"read_only": True, "input_qualified": False,
                          "preflight_error": str(error)}
        session.assert_identity()
        result = {"read_only": True, "identity": session.identity,
                  "character": character, "character_uid": uid,
                  "server": server.decode("utf-8"),
                  "client_sha256": CLIENT_SHA256_1078, "module_base": hex(module["base"]),
                  "module_size": module["size"], "pins": pins,
                  "first_mismatch": next((pin for pin in pins if not pin["matches"]), None),
                  "owned_actor_kind": actor_kind,
                  "hit_test_code": hit_test_code,
                  "booth_target_preflight": target}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as target:
        json.dump(result, target, indent=2)
    print(json.dumps({"output": str(args.output),
                      "first_mismatch_rva": result["first_mismatch"]["rva"]
                      if result["first_mismatch"] else None}))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        import traceback
        marker = sys.argv.index("--output")
        diagnostic = Path(sys.argv[marker + 1]).with_suffix(".error.txt")
        if not diagnostic.exists():
            diagnostic.write_text("".join(traceback.format_exception(error)), encoding="utf-8")
        raise
