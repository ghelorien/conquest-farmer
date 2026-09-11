"""Buy verified Painkiller units from the inspected open Pharmacist shop."""
import argparse
import json
from pathlib import Path
import time
import yaml

from conquest.addressing import WorkerPointerSession,PlayerLayout,resolve_player
from conquest.memory_inventory import InventoryLayout,MemoryInventoryReader
from conquest.worker import request

parser=argparse.ArgumentParser()
parser.add_argument('--count',type=int,default=15,choices=range(1,31))
args=parser.parse_args()
stop=yaml.safe_load(Path('profiles/pharmacist-stop.yaml').read_text())
if not stop.get('shop_memory_qualified') or not stop.get('npc_entity_id'):
    raise ValueError('Memory NPC identity and open-shop item reader must be connected before buying; visual fallback is disabled')
info=json.loads(Path('reports/desktop-farming/app-state.json').read_text())['worker_info_path']
layout=PlayerLayout.model_validate(yaml.safe_load(Path('profiles/classic-1074-player-candidate.yaml').read_text()))
session=WorkerPointerSession(info,layout.expected_sha256)
inventory=MemoryInventoryReader(session,layout,InventoryLayout.model_validate(
    yaml.safe_load(Path('profiles/classic-1074-inventory-candidate.yaml').read_text())))
report={'vendor':'pharmacist','standing_position':[466,333],'purchases':[]}
try:
    for _ in range(args.count):
        before=inventory.read()
        if before.count(1000020)>=args.count:
            break
        health=request(info,'health')
        life=health['embedded_controls']['life']
        if (health['embedded_controls']['control']['enabled'] or life['map_id']!=1002
                or life['position']!=[466,333] or life['dead_candidate']):
            raise ValueError('Restock requires the inspected living character at the Pharmacist, with farming Off')
        if before.silver<60 or len(before.items)>=before.capacity:
            raise ValueError('Insufficient silver or inventory space')
        addresses=resolve_player(session,layout)
        request(info,'foreground-click',{'point':[186,228],'button':'right',
            'expected_size':[1036,793],'require_foreground':False,'expires_at':time.time()+4,
            'guard':{'name_address':hex(addresses['name']),'name':'Parasite',
                     'hp_address':hex(addresses['max_hp']),'max_hp':life['max_hp']}})
        deadline=time.monotonic()+2
        while True:
            after=inventory.read()
            if after.count(1000020)==before.count(1000020)+1 and after.silver==before.silver-60:
                break
            if time.monotonic()>=deadline:
                raise ValueError('Purchase was not verified; no repeat purchase issued')
            time.sleep(.1)
        event={'potions':after.count(1000020),'silver':after.silver}
        report['purchases'].append(event)
        print(json.dumps(event),flush=True)
finally:
    Path('reports/pharmacist-restock.json').write_text(json.dumps(report,indent=2))
