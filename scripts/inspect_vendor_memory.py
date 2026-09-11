"""Bounded read-only vendor layout diagnostic. Does not capture or send input."""
import collections
import json
from pathlib import Path
import re
import struct
import yaml
from conquest.worker import request
from conquest.memory_health import HealthWorkerSession
from conquest.memory_entities import EntityLayout,MemoryEntityReader,sample_fields

state=json.loads(Path('reports/desktop-farming/app-state.json').read_text())
health=request(state['worker_info_path'],'health')
session=HealthWorkerSession(state['worker_info_path'],health['expected_sha256'])
layout=EntityLayout.model_validate(yaml.safe_load(Path('profiles/classic-1074-entities-candidate.yaml').read_text()))
base,collection,trace=MemoryEntityReader(session,layout)._resolve()
header=sample_fields(session,[(collection+offset,'u64') for offset in (0x58,0x60,0x68)])
begin,end,capacity=header
if not(0<begin<=end<=capacity and (end-begin)%16==0 and (capacity-begin)//16<=4096):
    raise ValueError('Invalid scene collection')
entries=session.read_block(begin,end-begin)
objects=[struct.unpack_from('<Q',entries,i+8)[0] for i in range(0,len(entries),16)]
vtables=sample_fields(session,[(obj,'u64') for obj in objects])
rows=[]
for obj,vt in zip(objects,vtables):
    rva=vt-base
    if rva in (0x5ccc90,0x5cd298,0x5cd278,0x5cef40,0x5cdaf0):
        continue
    data=session.read_block(obj,0x200)
    if rva==layout.monster_vtable_rva and struct.unpack_from('<I',data,0x80)[0] in layout.monster_type_ids:
        continue
    strings=[(hex(m.start()),m.group().decode(errors='replace')) for m in re.finditer(rb'[A-Za-z][A-Za-z0-9_ -]{3,}',data)]
    rows.append({'object':obj,'vtable_rva':hex(rva),'strings':strings,
        'u32':{hex(i):struct.unpack_from('<I',data,i)[0] for i in range(0x20,0x180,4)}})
    if len(rows)>=128:break
result={'source':'read_only_memory','qualified':False,'life':health['embedded_controls'].get('life'),
        'vtable_counts':dict(collections.Counter(hex(v-base) for v in vtables)),'candidates':rows}
Path('reports/vendor-memory-candidates.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps(result,indent=2))
