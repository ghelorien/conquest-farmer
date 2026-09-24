import json
import re
from pathlib import Path
from capstone import Cs, CS_ARCH_X86, CS_MODE_64

root = Path(__file__).resolve().parents[1]
code = (root / "reports/runtime-code.bin").read_bytes()
cs = Cs(CS_ARCH_X86, CS_MODE_64)
cs.skipdata = True
roots = []
attrs = []
for a, n, m, o in cs.disasm_lite(code, 0x1000):
    rip = re.search(r"\[rip ([+-]) (0x[0-9a-f]+)\]", o)
    if rip:
        target = a + n + int(rip[2], 16) * (1 if rip[1] == "+" else -1)
        if target == 0x697970:
            roots.append((hex(a), m, o))
    if 0x180000 <= a <= 0x1D0000 and any(
        f"+ {v}]" in o
        for v in ["0x3d8", "0x3dc", "0x3e0", "0x4a0", "0x4a4", "0x610", "0x614"]
    ):
        attrs.append((hex(a), m, o))
print(json.dumps({"root_refs": roots, "attribute_refs": attrs}, indent=2))
