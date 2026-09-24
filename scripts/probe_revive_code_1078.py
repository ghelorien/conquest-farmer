"""Read-only, bounded exact-1078 Revive string and loaded-code references.

This does not focus a window, send input, or qualify a Revive click.
"""

import argparse
import base64
import bisect
import json
from pathlib import Path
import struct
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(
    0, str(Path(__file__).resolve().parents[2] / "Conquest-diagnostic-python")
)
from capstone import Cs, CS_ARCH_X86, CS_MODE_64
from conquest.memory_build_layout import CLIENT_SHA256_1078
from conquest.worker import request


class BridgeSession:
    """App-owned authenticated reads, with an exact process/module guard."""

    def __init__(self, info_path, pid, created, profile_id):
        self.info_path, self.pid, self.created, self.profile_id = (
            info_path,
            pid,
            created,
            profile_id,
        )
        self.identity = None
        self.modules = None
        self.expected_sha256 = CLIENT_SHA256_1078
        self.total_bytes = 0

    def __enter__(self):
        health = request(self.info_path, "health")
        if (
            health.get("worker") != "live"
            or health.get("expected_sha256") != CLIENT_SHA256_1078
            or health.get("profile_id") != self.profile_id
            or health.get("target", {}).get("pid") != self.pid
            or health.get("target", {}).get("creation_time_100ns") != self.created
            or health.get("embedded_controls", {}).get("control", {}).get("enabled")
            is not False
        ):
            raise ValueError(
                "Exact stopped farmer worker, client or process identity changed"
            )
        self.identity, self.modules = health["target"], health["modules"]
        return self

    def __exit__(self, *_):
        self.assert_identity()

    def assert_identity(self):
        health = request(self.info_path, "health")
        if (
            health.get("worker") != "live"
            or health.get("expected_sha256") != self.expected_sha256
            or health.get("profile_id") != self.profile_id
            or health.get("target") != self.identity
            or health.get("modules") != self.modules
            or health.get("embedded_controls", {}).get("control", {}).get("enabled")
            is not False
        ):
            raise ValueError("Farmer worker, process, module or control state changed")

    def read(self, address, size):
        if (
            not 0 < size <= 12 * 1024 * 1024
            or self.total_bytes + size > 32 * 1024 * 1024
        ):
            raise ValueError("Loaded-code diagnostic exceeded total read bound")
        blocks = []
        for offset in range(0, size, 65536):
            length = min(65536, size - offset)
            result = request(
                self.info_path,
                "read-block",
                {"address": hex(address + offset), "size": length},
            )
            if (
                result.get("address") != hex(address + offset)
                or result.get("size") != length
            ):
                raise ValueError("Read-block address or size changed")
            decoded = base64.b64decode(result["data"], validate=True)
            if len(decoded) != length:
                raise ValueError("Read-block returned incomplete data")
            blocks.append(decoded)
        self.total_bytes += size
        return b"".join(blocks)


def inspect(info_path, pid, created, profile_id):
    with BridgeSession(info_path, pid, created, profile_id) as session:
        modules = [
            m for m in session.modules if m["name"].casefold() == "imconquer.exe"
        ]
        if len(modules) != 1:
            raise ValueError("Expected one exact game module")
        module = modules[0]
        base = module["base"]
        header = session.read(base, 4096)
        pe = struct.unpack_from("<I", header, 0x3C)[0]
        count = struct.unpack_from("<H", header, pe + 6)[0]
        optional = pe + 24
        sections_at = optional + struct.unpack_from("<H", header, pe + 20)[0]
        pdata_rva, pdata_size = struct.unpack_from(
            "<II", header, optional + 112 + 3 * 8
        )
        if not 0 < pdata_size < 2 * 1024 * 1024:
            raise ValueError("Unexpected runtime-function table size")
        pdata = session.read(base + pdata_rva, pdata_size)
        functions = [
            struct.unpack_from("<II", pdata, pos)
            for pos in range(0, len(pdata) - 11, 12)
        ]
        starts = [row[0] for row in functions]
        sections = []
        for index in range(count):
            pos = sections_at + 40 * index
            name = header[pos : pos + 8].rstrip(b"\0")
            size, rva = struct.unpack_from("<II", header, pos + 8)
            if name not in (b".text", b".rdata"):
                continue
            if not 0 < size < 12 * 1024 * 1024 or rva + size > module["size"]:
                raise ValueError("Unexpected selected section bounds")
            raw = b"".join(
                session.read(base + rva + offset, min(1048576, size - offset))
                for offset in range(0, size, 1048576)
            )
            sections.append((name, rva, raw))
        labels = ("Revive", "revive", "Reborn", "##Revive")
        strings = {}
        for name, rva, raw in sections:
            if name != b".rdata":
                continue
            for label in labels:
                for encoding in ("ascii", "utf-16le"):
                    needle = label.encode(encoding) + (
                        b"\0\0" if encoding == "utf-16le" else b"\0"
                    )
                    start = 0
                    while (at := raw.find(needle, start)) >= 0:
                        strings[base + rva + at] = {
                            "rva": hex(rva + at),
                            "label": label,
                            "encoding": encoding,
                        }
                        start = at + 1
        cs = Cs(CS_ARCH_X86, CS_MODE_64)
        cs.skipdata = True
        references = []
        selected = set()
        for name, rva, raw in sections:
            if name != b".text":
                continue
            for address, size, mnemonic, operand in cs.disasm_lite(raw, base + rva):
                if "[rip + 0x" not in operand and "[rip - 0x" not in operand:
                    continue
                sign, value = operand.split("[rip ", 1)[1].split("]", 1)[0].split(" ")
                target = address + size + int(value, 16) * (1 if sign == "+" else -1)
                if target not in strings:
                    continue
                reference = {
                    "rva": hex(address - base),
                    "instruction": mnemonic + " " + operand,
                    "string": strings[target],
                }
                references.append(reference)
                relative = address - base
                slot = bisect.bisect_right(starts, relative) - 1
                if slot >= 0 and functions[slot][0] <= relative < functions[slot][1]:
                    selected.add(functions[slot])
        disassembly = []
        for start, end in sorted(selected):
            if not 0 < end - start < 0x12000:
                raise ValueError("Referenced function exceeds bound")
            raw = session.read(base + start, end - start)
            disassembly.append(
                {
                    "start_rva": hex(start),
                    "end_rva": hex(end),
                    "instructions": [
                        f"{address - base:x}: {mnemonic} {operand}"
                        for address, _, mnemonic, operand in cs.disasm_lite(
                            raw, base + start
                        )
                    ],
                }
            )
        session.assert_identity()
        return {
            "read_only": True,
            "input_qualified": False,
            "identity": session.identity,
            "module_sha256": CLIENT_SHA256_1078,
            "strings": list(strings.values()),
            "references": references,
            "functions": disassembly,
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--created", type=int, required=True)
    parser.add_argument("--worker-info", type=Path, required=True)
    parser.add_argument("--profile-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Refusing to overwrite diagnostic")
    result = inspect(args.worker_info, args.pid, args.created, args.profile_id)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2)
    print(
        json.dumps(
            {
                "output": str(args.output),
                "strings": len(result["strings"]),
                "references": len(result["references"]),
                "functions": len(result["functions"]),
            }
        )
    )
