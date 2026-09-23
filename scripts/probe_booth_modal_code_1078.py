"""Bounded read-only loaded-code evidence for exact 1078 booth modal semantics.

No input, writes to process memory, qualification, or runtime-state changes.
Only unique diagnostic output is created. Captured code stays local.
"""
import argparse
import bisect
import json
from pathlib import Path
import struct
import sys
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
# Existing isolated diagnostic dependency, never the production environment.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "Conquest-diagnostic-python"))
from capstone import Cs, CS_ARCH_X86, CS_MODE_64
from conquest.memory import MemorySession
from conquest.memory_build_layout import CLIENT_SHA256_1078
from conquest.merchants.observe_1078 import _actor_identity
from conquest.merchants.reader_1078 import open_read_only_1078


def main(args):
    if args.output.exists():
        raise ValueError("Refusing to overwrite existing diagnostic")
    with MemorySession(args.pid, CLIENT_SHA256_1078) as session:
        if session.identity["creation_time_100ns"] != args.created:
            raise ValueError("Target process was replaced")
        modules = [m for m in session.modules if m["name"].casefold() == "imconquer.exe"]
        if len(modules) != 1:
            raise ValueError("Expected exactly one module")
        module = modules[0]
        base = module["base"]
        character, uid, server = _actor_identity(session)
        if character not in ("Dutch", "Spiritual") or server != b"Classic_US":
            raise ValueError("Not a selected merchant")
        reader = open_read_only_1078(session, character)
        before = reader.read_manual_ownership()
        if not before["booth_open"] or before["trade"] or before["request"]:
            raise ValueError("Require open owned booth with closed trade/request")
        model = reader._model(25, reader.booth_vtable_rva)
        model_raw = session.read(model, 0x70)
        header = session.read(base, 4096)
        pe = struct.unpack_from("<I", header, 0x3c)[0]
        count = struct.unpack_from("<H", header, pe + 6)[0]
        optsize = struct.unpack_from("<H", header, pe + 20)[0]
        optional = pe + 24
        pdata_rva, pdata_size = struct.unpack_from("<II", header, optional + 112 + 3 * 8)
        if not 0 < pdata_size < 2 * 1024 * 1024:
            raise ValueError("Unexpected runtime-function table")
        pdata = session.read(base + pdata_rva, pdata_size)
        functions = [struct.unpack_from("<III", pdata, pos)[:2]
                     for pos in range(0, len(pdata) - 11, 12)]
        starts = [entry[0] for entry in functions]
        sections = []
        for index in range(count):
            pos = optional + optsize + 40 * index
            size, rva = struct.unpack_from("<II", header, pos + 8)
            flags = struct.unpack_from("<I", header, pos + 36)[0]
            if rva + size > module["size"]:
                raise ValueError("Unexpected section bounds")
            if header[pos:pos+8].rstrip(b"\0") in (b".text", b".rdata"):
                if size > 12 * 1024 * 1024:
                    raise ValueError("Selected section exceeds diagnostic bound")
                raw = b"".join(session.read(base+rva+off, min(1048576, size-off))
                               for off in range(0, size, 1048576))
                sections.append((rva, flags, raw))
        strings = {}
        labels = ("Add Item to Booth", "##Amount", "Confirm", "Cancel", "OK")
        for rva, flags, raw in sections:
            if flags & 0x20000000:
                continue
            for label in labels:
                needle = label.encode() + b"\0"
                start = 0
                while (pos := raw.find(needle, start)) >= 0:
                    strings[rva+pos] = label
                    start = pos+1
        cs = Cs(CS_ARCH_X86, CS_MODE_64)
        cs.skipdata = True
        targets = {base+rva: label for rva, label in strings.items()}
        refs = []
        selected = set()
        def select(address):
            index = bisect.bisect_right(starts, address) - 1
            if index >= 0 and functions[index][0] <= address < functions[index][1]:
                selected.add(functions[index])
        for rva, flags, raw in sections:
            if not flags & 0x20000000:
                continue
            for address, size, mnemonic, operand in cs.disasm_lite(raw, base+rva):
                if "[rip + 0x" not in operand and "[rip - 0x" not in operand:
                    continue
                expression = operand.split("[rip ", 1)[1].split("]", 1)[0]
                sign, value = expression.split(" ")
                target = address + size + (int(value, 16) * (1 if sign == "+" else -1))
                if target in targets:
                    refs.append({"rva": hex(address-base), "label": targets[target],
                                 "target_rva": hex(target-base), "instruction": mnemonic+" "+operand})
                    if targets[target] == "Add Item to Booth":
                        select(address-base)
        table = session.read(base + reader.booth_vtable_rva, 0x80)
        vtable = []
        for index, value in enumerate(struct.unpack("<16Q", table)):
            if base <= value < base + module["size"]:
                vtable.append({"slot": hex(index*8), "method_rva": hex(value-base)})
                select(value-base)
        for value in args.rva:
            select(int(value, 16))
        disassembly = []
        rip_strings = []
        for start, end in sorted(selected):
            if end-start > 0x18000:
                raise ValueError("Function exceeds diagnostic bound")
            raw = session.read(base+start, end-start)
            if session.read(base+start, end-start) != raw:
                raise ValueError("Loaded code changed")
            disassembly.append({"start_rva": hex(start), "end_rva": hex(end),
                                "code_hex": raw.hex(), "instructions":
                [f"{a:x}: {m} {o}" for a, _, m, o in cs.disasm_lite(raw, start)]})
            for address, size, mnemonic, operand in cs.disasm_lite(raw, start):
                if mnemonic != "lea" or "[rip + 0x" not in operand:
                    continue
                displacement = int(operand.split("[rip + ", 1)[1].split("]", 1)[0], 16)
                target = address + size + displacement
                if not 0 <= target < module["size"] - 128:
                    continue
                candidate = session.read(base + target, 128)
                value = candidate.split(b"\0", 1)[0]
                if value and all(32 <= byte < 127 for byte in value) and len(value) < 128:
                    if session.read(base + target, len(value)+1) != value+b"\0":
                        raise ValueError("Referenced label changed")
                    rip_strings.append({"instruction_rva": hex(address), "target_rva": hex(target),
                                        "text": value.decode("ascii")})
        after = reader.read_manual_ownership()
        stable = ("identity", "character_uid", "inventory", "booth", "own_booth_uid", "booth_open")
        if any(before[key] != after[key] for key in stable) or reader._model(25, reader.booth_vtable_rva) != model:
            raise ValueError("Merchant ownership changed")
        session.assert_identity()
        result = {"read_only": True, "input_qualified": False, "identity": session.identity,
                  "character": character, "character_uid": uid, "module_base": hex(base),
                  "client_sha256": CLIENT_SHA256_1078, "model_address": hex(model),
                  "model_hex": model_raw.hex(), "vtable": vtable, "label_references": refs,
                  "rip_strings": rip_strings, "functions": disassembly}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--created", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rva", action="append", default=[])
    arguments = parser.parse_args()
    try:
        main(arguments)
    except Exception:
        with arguments.output.with_suffix(".error.txt").open("x", encoding="utf-8") as stream:
            stream.write(traceback.format_exc())
        raise
