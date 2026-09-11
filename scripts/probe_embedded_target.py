"""Inspect targets, or issue one explicitly selected ID/point diagnostic.

No automatic target selection, retries, farming enable, window activation or
cursor movement. --send requires a visually checked sprite point. Candidate
HP, ammunition and kill-counter changes are evidence, not loot attribution.
"""
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import struct
import time

import yaml

from conquest.addressing import PlayerLayout, resolve_player
from conquest.memory_health import HealthLayout, HealthWorkerSession, MemoryHealthReader, decode_attribute
from conquest.memory_inventory import InventoryLayout, MemoryInventoryReader
from conquest.worker import request


def read_actor(session, base, actor):
    obj=actor['object_address']
    record=session.read_block(obj,0x988)
    if (struct.unpack_from('<Q',record)[0]!=base+0x5c5e20
            or struct.unpack_from('<I',record,0x78)[0]!=actor['entity_id']
            or struct.unpack_from('<I',record,0x80)[0]!=actor['type_id']):
        raise ValueError('Target object identity changed')
    ptr=struct.unpack_from('<Q',record,0x978)[0]
    header=session.read_block(ptr,24)
    mode,count=struct.unpack_from('<II',header,8)
    if mode not in range(4) or not 2<=count<=1024:
        raise ValueError('Monster attribute candidate is unavailable')
    table_ptr=struct.unpack_from('<Q',header,16)[0]
    table=session.read_block(table_ptr,count*4)
    hp=decode_attribute(table,mode,count,1)
    maximum=struct.unpack_from('<I',record,0x3e0)[0]
    if not 0<=hp<=maximum:
        raise ValueError('Monster HP candidate is outside bounds')
    if (session.read_block(ptr,24)!=header or session.read_block(table_ptr,count*4)!=table
            or session.read_block(obj+0x78,16)!=record[0x78:0x88]
            or session.read_block(obj+0xe8,24)!=record[0xe8:0x100]):
        raise ValueError('Target changed during observation')
    return {'id':actor['entity_id'],'type_id':actor['type_id'],
            'position':list(struct.unpack_from('<II',record,0xe8)),
            'draw_position':list(struct.unpack_from('<ii',record,0xf8)),
            'hp_candidate':hp,'max_hp':maximum}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--target-id',type=int)
    parser.add_argument('--point',type=int,nargs=2,metavar=('X','Y'))
    parser.add_argument('--send',action='store_true')
    args=parser.parse_args()
    if args.send and (args.target_id is None or args.point is None):
        raise ValueError('--send requires one exact target ID and inspected sprite point')
    state=json.loads(Path('reports/desktop-farming/app-state.json').read_text())
    info=state['worker_info_path']
    health=request(info,'health')
    player=PlayerLayout.model_validate(yaml.safe_load(Path('profiles/classic-1074-player-candidate.yaml').read_text()))
    hp_layout=HealthLayout.model_validate(yaml.safe_load(Path('profiles/classic-1074-health-candidate.yaml').read_text()))
    inventory_layout=InventoryLayout.model_validate(yaml.safe_load(Path('profiles/classic-1074-inventory-candidate.yaml').read_text()))
    session=HealthWorkerSession(info,player.expected_sha256)
    addresses=resolve_player(session,player)
    base=next(m['base'] for m in session.modules if m['name'].casefold()==player.module.casefold())
    def player_fields():
        session.assert_identity()
        return {'position':list(struct.unpack('<II',session.read_block(addresses['position'],8))),
                'level':struct.unpack('<I',session.read_block(addresses['level'],4))[0],
                'map_id':struct.unpack('<I',session.read_block(addresses['map'],4))[0],
                'kill_counter':struct.unpack('<I',session.read_block(addresses['kill_counter'],4))[0]}
    player_before=player_fields()
    if player_before['map_id']!=1002 or health['window']['client_size']!=[1036,793]:
        raise ValueError('The inspected map or viewport changed')
    if not health['embedded_controls']['observations_available']:
        raise ValueError('Live monster list is unavailable; no input sent')
    rows=[]
    actor=None
    for candidate in health['embedded_controls']['monsters']:
        dx=candidate['position'][0]-player_before['position'][0]
        dy=candidate['position'][1]-player_before['position'][1]
        projected=[518+(dx-dy)*32,396+(dx+dy)*16]
        if (50<=projected[0]<=986 and 60<=projected[1]<=653):
            rows.append({'id':candidate['entity_id'],'type_id':candidate['type_id'],'name':candidate['name'],
                         'position':candidate['position'],'projected_feet':projected,
                         'draw_position':candidate['draw_position'],'distance':max(abs(dx),abs(dy))})
        if candidate['entity_id']==args.target_id:
            actor=candidate
    rows.sort(key=lambda row:row['distance'])
    result={'qualified':False,'generated_at':datetime.now(timezone.utc).isoformat(),
            'player_before':player_before,'window_before':health['window'],'nearby':rows}
    if args.target_id is None:
        print(json.dumps(result,indent=2))
        return
    if actor is None:
        raise ValueError('Selected monster ID is not currently observed')
    target=read_actor(session,base,actor)
    result['target_before']=target
    result['health_before']=asdict(MemoryHealthReader(session,hp_layout,'Parasite').read())
    inventory_reader=MemoryInventoryReader(session,player,inventory_layout)
    result['inventory_before']=asdict(inventory_reader.read())
    if not args.send:
        print(json.dumps(result,indent=2))
        return
    if health['embedded_controls']['control']['enabled']:
        raise ValueError('Turn farming Off before a diagnostic')
    if target['hp_candidate']<=0 or result['inventory_before']['equipped_ammo'] is None:
        raise ValueError('Target HP or equipped arrows are unavailable')
    point=args.point
    draw=target['draw_position']
    if not (50<=point[0]<=986 and 60<=point[1]<=653 and
            abs(point[0]-draw[0])<=24 and draw[1]-48<=point[1]<=draw[1]+8):
        raise ValueError('Point is outside the selected actor sprite region or inspected scene')
    if result['inventory_before']['equipped_ammo']['amount']<=0:
        raise ValueError('Equipped arrows are empty')
    time.sleep(.2)
    fresh=read_actor(session,base,actor)
    if fresh!=target or player_fields()!=player_before:
        raise ValueError('Player or selected target changed before click; no input sent')
    result['point']=point
    try:
        result['input']=request(info,'background-click',{
            'health_profile':hp_layout.model_dump(mode='json'),'character':'Parasite',
            'point':point,'expected_size':[1036,793],'position_cursor':False,
            'move_settle_seconds':.2,'expires_at':time.time()+4})
        samples=[]
        result['samples']=samples
        for _ in range(15):
            sample={'player':player_fields(),'window':request(info,'health')['window']}
            try:
                sample['target']=read_actor(session,base,actor)
            except (OSError,ValueError) as error:
                sample['target_error']=str(error)
            try:
                current=inventory_reader.read()
                sample['equipped_ammo']=asdict(current.equipped_ammo) if current.equipped_ammo else None
            except (OSError,ValueError) as error:
                sample['ammo_error']=str(error)
            samples.append(sample)
            time.sleep(.15)
        result['health_after']=asdict(MemoryHealthReader(session,hp_layout,'Parasite').read())
        result['inventory_after']=asdict(inventory_reader.read())
    except Exception as error:
        result.update(error=str(error),delivery_may_be_partial=True)
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output=Path(f'reports/embedded-target-{args.target_id}-{stamp}.json')
    output.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2))
    print(f'Report: {output}')


if __name__=='__main__':
    main()
