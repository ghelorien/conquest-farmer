"""Read-only live motion watch; never sends input or changes farming intent."""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import time

import yaml

from conquest.addressing import resolve_player
from conquest.memory_health import HealthLayout,HealthWorkerSession
from conquest.worker import request


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--seconds',type=int,default=45,choices=range(1,91))
    parser.add_argument('--hz',type=int,default=25,choices=range(1,101))
    args=parser.parse_args()
    state=json.loads(Path('reports/desktop-farming/app-state.json').read_text())
    info=state['worker_info_path']
    health=request(info,'health')
    layout=HealthLayout.model_validate(yaml.safe_load(Path('profiles/classic-1074-health-candidate.yaml').read_text()))
    session=HealthWorkerSession(info,layout.player.expected_sha256)
    addresses=resolve_player(session,layout.player)
    fields=[{'name':'position','address':hex(addresses['position']),'kind':'xy_u32'},
            {'name':'motion','address':hex(addresses['object']+0x118),'kind':'u32'},
            {'name':'frame','address':hex(addresses['object']+0x11c),'kind':'u32'},
            {'name':'status','address':hex(addresses['object']+0x30),'kind':'u64'}]
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output=Path(f'reports/foreground-recovery-motion-{stamp}.json')
    result={'app_pid':state['pid'],'process_identity':health['target'],
            'player_object':addresses['object'],'window_before':health['window'],
            'control_before':health['embedded_controls']['control'],'requested_hz':args.hz,
            'samples':[],'errors':[]}
    print(json.dumps({'watch_started':str(output),'app_pid':state['pid']}),flush=True)
    start=time.monotonic()
    while time.monotonic()-start<args.seconds:
        iteration=time.monotonic()
        try:
            response=request(info,'sample',{'fields':fields})
            values={f['name']:f['value'] for f in response['fields']}
            result['samples'].append({'elapsed':round(time.monotonic()-start,3),
                'rpc_seconds':round(time.monotonic()-iteration,4),
                'position':values['position'],'motion':values['motion'][0],
                'frame':values['frame'][0],'status':values['status'][0]})
        except Exception as error:
            result['errors'].append({'elapsed':round(time.monotonic()-start,3),'error':str(error)})
            break
        if len(result['samples'])%args.hz==0:
            output.write_text(json.dumps(result,indent=2),encoding='utf-8')
        time.sleep(max(0,1/args.hz-(time.monotonic()-iteration)))
    result['motion_ids']=sorted(set(row['motion'] for row in result['samples']))
    result['jump_motion_observed']=bool(set(result['motion_ids']) & {130,131})
    result['mapping_source']='C:/Program Files/Classic Conquer 2.0/ini/ActionSound.ini'
    output.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps({'report':str(output),'samples':len(result['samples']),
        'motion_ids':result['motion_ids'],'jump_motion_observed':result['jump_motion_observed'],
        'positions':list(dict.fromkeys(tuple(row['position']) for row in result['samples'])),
        'errors':result['errors']}),flush=True)


if __name__=='__main__': main()
