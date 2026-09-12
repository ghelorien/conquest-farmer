"""Read-only, two-hour parity telemetry; never controls or stops the farmer."""
from collections import Counter
import json
from pathlib import Path
import sqlite3
import time
from conquest.discord_notify import read_json,write_json

ROOT=Path(__file__).resolve().parents[1]
CONFIG=ROOT/'reports/performance/unified-validation.json'
OUT=ROOT/'reports/performance/unified-validation-status.json'

def main():
    config=read_json(CONFIG);start=config['started_at'];end=start+config['duration_seconds']
    old=read_json(OUT)
    same=old.get('started_at')==start
    cursor=old.get('cursor',config['baseline_event_id']) if same else config['baseline_event_id']
    kills=old.get('kill_events',[]) if same else []
    counts=Counter(old.get('event_counts',{}) if same else {})
    dbpath=ROOT/'reports/desktop-farming/trial.sqlite3'
    while True:
        now=time.time()
        try:
            with sqlite3.connect(dbpath.as_uri()+'?mode=ro',uri=True,timeout=1) as db:
                rows=db.execute('select rowid,time,event,payload from events where rowid>? order by rowid limit 50000',(cursor,)).fetchall()
            for rid,at,event,raw in rows:
                cursor=rid
                if at<start:continue
                counts[event]+=1
                if event=='kill_verified':kills.append([at,json.loads(raw)['count']])
            app=read_json(ROOT/'reports/desktop-farming/app-state.json')
            route=read_json(ROOT/'reports/overnight/status.json')
            elapsed=max(now-start,1)
            windows={str(n):{'complete':elapsed>=n,'kills':sum(k for t,k in kills if now-n<t<=now),
                'kills_per_minute':round(sum(k for t,k in kills if now-n<t<=now)*60/n,2)} for n in (60,300,900)}
            report={'started_at':start,'observed_at':now,'cursor':cursor,'phase':'complete' if now>=end else 'monitoring',
                'elapsed_seconds':round(elapsed,1),'total_kills':sum(k for t,k in kills),
                'overall_kills_per_minute':round(sum(k for t,k in kills)*60/elapsed,2),
                'windows':windows,'event_counts':dict(counts),'kill_events':kills,
                'app_pid':app.get('pid'),'route_phase':route.get('phase'),'route_activity':route.get('activity'),
                'route_error':route.get('detail'),'route_cycles':route.get('cycles'),
                'target_met':elapsed>=900 and windows['900']['kills_per_minute']>=40,
                'farmer_control_untouched':True}
            write_json(OUT,report)
        except (OSError,ValueError,sqlite3.Error):
            if now>=end:raise
        if now>=end:break
        time.sleep(5)

if __name__=='__main__':main()
