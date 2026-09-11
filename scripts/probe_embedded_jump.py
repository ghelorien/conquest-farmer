"""One ordinary Control-message click on a checked four-tile town segment.

Arrival alone does not establish a jump. Preserve timing and intermediate
positions for comparison with walking; do not enable a return-jump loop here.
"""
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import struct
import time

import yaml

from conquest.addressing import resolve_player
from conquest.memory_health import HealthLayout, HealthWorkerSession
from conquest.memory_life import read_life
from conquest.navigation import read_terrain
from conquest.worker import request


def main():
    state=json.loads(Path('reports/desktop-farming/app-state.json').read_text())
    info=state['worker_info_path']
    health=request(info,'health')
    if health['input_revision']<9 or health['embedded_controls']['control']['enabled']:
        raise ValueError('The current worker must support Control messages with farming Off')
    layout=HealthLayout.model_validate(yaml.safe_load(Path('profiles/classic-1074-health-candidate.yaml').read_text()))
    session=HealthWorkerSession(info,layout.player.expected_sha256)
    before=read_life(session,layout,'Parasite')
    if (before.ghost_candidate or before.current_hp<before.max_hp*.8
            or before.position!=(430,380) or before.map_id!=1002):
        raise ValueError('Healthy, recovered town baseline changed; no input sent')
    window=health['window']
    if (window['foreground']==window['root_hwnd'] or window['minimized']
            or window['client_size']!=[1036,793]):
        raise ValueError('Unfocused calibrated viewport is required')
    destination=(434,380)
    terrain=read_terrain(r'C:\Program Files\Classic Conquer 2.0',1002)
    segment=[(x,380) for x in range(430,435)]
    if not all(terrain.walkable(point) for point in segment):
        raise ValueError('The exact town segment is blocked')
    actual=resolve_player(session,layout.player)
    def sample():
        data=session.read_block(actual['object']+0xd8,0x78)
        return {'position':list(struct.unpack_from('<II',data)),
                'motion_candidate_words':{hex(0xd8+i):struct.unpack_from('<I',data,i)[0]
                                          for i in range(0,len(data),4)}}
    baseline=sample()
    time.sleep(.2)
    if sample()['position']!=baseline['position'] or baseline['position']!=list(before.position):
        raise ValueError('Player is moving; no input sent')
    result={'jump_qualified':False,'arrival_verified':False,'before':asdict(before),
            'baseline':baseline,'expected_position':list(destination),
            'terrain_sha256':terrain.source_sha256,'checked_segment':segment,
            'samples':[],'note':'Control messages and arrival do not alone distinguish jumping from walking.'}
    start=time.monotonic()
    try:
        result['input']=request(info,'background-click',{
            'health_profile':layout.model_dump(mode='json'),'character':'Parasite',
            'point':[646,460],'expected_size':[1036,793], 'position_cursor':False,
            'control':True,'move_settle_seconds':.2,'expires_at':time.time()+4})
        result['dispatch_elapsed']=time.monotonic()-start
        for _ in range(20):
            row=sample()
            row['elapsed']=time.monotonic()-start
            row['window']=request(info,'health')['window']
            result['samples'].append(row)
            time.sleep(.05)
        result['after']=asdict(read_life(session,layout,'Parasite'))
        result['arrival_verified']=all(row['position']==list(destination) for row in result['samples'][-3:])
    except Exception as error:
        result.update(error=str(error),delivery_may_be_partial=True)
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output=Path(f'reports/embedded-jump-{stamp}.json')
    output.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps({'report':str(output),'arrival_verified':result['arrival_verified'],
        'jump_qualified':False,'dispatch_elapsed':result.get('dispatch_elapsed'),
        'positions':[(round(row['elapsed'],3),row['position']) for row in result['samples']],
        'after':result.get('after'),'error':result.get('error')},indent=2))


if __name__=='__main__': main()
