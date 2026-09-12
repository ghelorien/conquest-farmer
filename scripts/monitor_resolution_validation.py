"""Bounded read-only validation of the current live resolution run.

Never starts/stops/reloads farming. A transient status-file gap keeps the last
confirmed worker connection; only fresh worker responses populate life samples.
"""
import argparse
import json
import sqlite3
import time

from conquest.discord_notify import read_json, write_json
from conquest.worker import request


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--seconds',type=int,default=600)
    parser.add_argument('--started-at',type=float)
    parser.add_argument('--report',default='reports/performance/resolution-endurance-monitor.json')
    args=parser.parse_args()
    if not 1<=args.seconds<=3600:raise ValueError('Observation duration must be 1–3600 seconds')
    run=read_json('reports/performance/resolution-live-run.json')
    started=args.started_at if args.started_at is not None else run['started_at']
    if not 0<started<=time.time():raise ValueError('Run start must be a past timestamp')
    app=read_json('reports/desktop-farming/app-state.json')
    info=app.get('worker_info_path')
    if not info:raise ValueError('No initial worker connection available')
    report=args.report
    samples=read_json(report,{}).get('samples',[])
    deadline=time.monotonic()+args.seconds
    last_publish=0
    db=sqlite3.connect('file:reports/desktop-farming/trial.sqlite3?mode=ro',uri=True)
    events=[]
    # Cache the tail once; subsequent queries read only newly committed rows.
    rows=db.execute('select rowid,time,event,payload from events order by rowid desc limit 50000').fetchall()
    cursor=max((r[0] for r in rows),default=0)
    events.extend((t,e,json.loads(p)) for _,t,e,p in reversed(rows) if t>=started)
    try:
        while time.monotonic()<deadline:
            fresh=read_json('reports/desktop-farming/app-state.json')
            if fresh.get('worker_info_path'):
                app=fresh
                info=fresh['worker_info_path']
            try:
                health=request(info,'health')
            except (ValueError,OSError) as error:
                print('Observation retry: '+str(error),flush=True)
                time.sleep(3)
                continue
            data=health['embedded_controls']
            life=data.get('life') or {}
            now=time.time()
            sample={'at':now,'app_pid':app.get('pid'),'position':life.get('position'),
                    'hp':life.get('current_hp'),'max_hp':life.get('max_hp'),
                    'dead':life.get('dead_candidate'),'flying':bool(life.get('status',0)&0x8000000),
                    'enabled':data['control']['enabled'],'execution':data['control']['execution_state'],
                    'manual_mouse':data.get('manual_mouse')}
            samples.append(sample)
            if now-last_publish>=30:
                for rowid,t,event,payload in db.execute(
                        'select rowid,time,event,payload from events where rowid>? order by rowid',(cursor,)):
                    cursor=rowid
                    if t>=started:events.append((t,event,json.loads(payload)))
                elapsed=now-started
                kills=sum(p.get('count',0) for _,e,p in events if e=='kill_verified')
                route=read_json('reports/overnight/status.json')
                summary={'sample':sample,'seconds':elapsed,'verified_kills':kills,
                         'kills_per_minute':kills*60/max(elapsed,1),
                         'movement_stalls':sum(e=='movement_stuck' for _,e,_ in events),
                         'errors':[(t,p) for t,e,p in events if e=='trial_error'],
                         'route':{k:v for k,v in route.items() if k in ('phase','activity','updated_at','pid','detail')}}
                print(json.dumps(summary),flush=True)
                write_json(report,{'summary':summary,'samples':samples})
                last_publish=now
            time.sleep(3)
    finally:
        db.close()
    print('Bounded endurance observation finished; farming was not changed',flush=True)


if __name__=='__main__':main()
