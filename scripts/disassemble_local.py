import sys
from pathlib import Path
from capstone import Cs, CS_ARCH_X86, CS_MODE_64
data=(Path(__file__).resolve().parents[1]/'reports/runtime-code.bin').read_bytes()
cs=Cs(CS_ARCH_X86,CS_MODE_64)
for arg in sys.argv[1:]:
    pieces=arg.split(':')
    rva=int(pieces[0],16)
    size=int(pieces[1],16) if len(pieces)>1 else 0x100
    print('AT',hex(rva))
    for ins in cs.disasm(data[rva-0x1000:rva-0x1000+size],rva):
        print(f'{ins.address:x}: {ins.mnemonic:8} {ins.op_str}')
