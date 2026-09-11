"""Local read-only calibration notes; never publish the captured client bytes."""
import base64
import json
import struct
from pathlib import Path
from capstone import Cs, CS_ARCH_X86, CS_MODE_64
from conquest.worker import request

root=Path(__file__).resolve().parents[1]
info=root/'.runtime/memory-worker.json'
health=request(info,'health')
base=health['modules'][0]['base']
def read(address,size):
    blocks=[]
    for offset in range(0,size,65536):
        n=min(65536,size-offset)
        r=request(info,'read-block',{'address':hex(address+offset),'size':n})
        data=base64.b64decode(r['data'],validate=True)
        if len(data)!=n or int(r['address'],0)!=address+offset: raise ValueError('Invalid read response')
        blocks.append(data)
    if request(info,'health')['target'] != health['target']: raise ValueError('Process changed')
    return b''.join(blocks)

code=read(base+0x1000,0x550000)
(root/'reports/runtime-code.bin').write_bytes(code)
module_data=read(base+0x641000,0x6c000)
(root/'reports/runtime-data.bin').write_bytes(module_data)
trace=json.loads((root/'reports/player-root-trace.json').read_text())
player=int(trace['object'],16)
refs=json.loads((root/'reports/player-reference-scan.json').read_text())['candidates']['player_pointer']['addresses']
targets={player,*[int(a,16) for a in refs]}
links=[]
for i in range(0,len(module_data)-7,8):
    value=struct.unpack_from('<Q',module_data,i)[0]
    if value in targets:
        links.append({'rva':hex(0x641000+i),'value':hex(value)})
vtable=read(base+0x5cef40,512)
cs=Cs(CS_ARCH_X86,CS_MODE_64)
cs.skipdata=True
matches=[]
for address,size,mnemonic,op in cs.disasm_lite(code,base+0x1000):
    if '0x3e0]' in op or '0x697970' in op:
        matches.append({'rva':hex(address-base),'mnemonic':mnemonic,'op':op})
result={'qualified':False,'process_identity':health['target'],'root_links':links,
    'player_vtable_functions':[hex(v-base) for v in struct.unpack('<64Q',vtable)],'max_hp_references':matches}
(root/'reports/runtime-code-notes.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result,indent=2))
