"""Rebuild an auditable parity report from original events, including downtime."""
from collections import Counter
import json
from pathlib import Path
import sqlite3
import statistics
import time
from conquest.discord_notify import read_json,write_json

ROOT=Path(__file__).resolve().parents[1]


def completed_cycles(timeline):
    """Require one ordered natural trip, rather than unrelated event totals."""
    hunting=False;pending=None;completed=[]
    for row in sorted(timeline,key=lambda r:r['time']):
        event=row['event'];at=row['time']
        if event=='kill_verified':
            if type(row.get('count')) is not int or row['count']<=0:continue
            hunting=True
            if pending and pending.get('arrived_at'):
                completed.append({**pending,'hunting_resumed_at':at});pending=None
        elif event=='return_required':
            counts=row.get('supplies',{})
            required=(row.get('reason') in ('inventory_full','ammo_unavailable','potions_exhausted')
                or counts.get('arrows',3)<3 or counts.get('potions',1)<=0 or counts.get('free_slots',1)<=0)
            pending=({'return_started_at':at,'storage_receipts':[]} if hunting and required
                     and not row.get('validation_cycle') else None)
        elif event=='valuable_stored' and pending and not pending.get('restock_complete_at'):
            if row.get('verified_in_warehouse') is True:
                pending['storage_receipts'].append(row)
        elif event=='restock_complete' and pending:
            counts=row.get('supplies',{})
            if (pending['storage_receipts'] and counts.get('arrows',0)>=3
                    and counts.get('potions',0)>0 and counts.get('free_slots',0)>0):
                pending['restock_complete_at']=at
        elif event=='farming_area_reached' and pending and pending.get('restock_complete_at'):
            pending['arrived_at']=at
    return completed


def report(root=ROOT, *, now=None, extend=False):
    config=read_json(root/'reports/performance/unified-validation.json')
    start=config['started_at'];end=start+config['duration_seconds']
    now=time.time() if now is None else now
    observed=now if extend else min(now,end);elapsed=max(observed-start,1)
    database=root/'reports/desktop-farming/trial.sqlite3'
    with sqlite3.connect(database.as_uri()+'?mode=ro',uri=True,timeout=2) as db:
        rows=db.execute('select time,event,payload from events where rowid>? and time>=? and time<=? order by rowid',
            (config['baseline_event_id'],start,observed)).fetchall()
    counts=Counter();kills=[];delays=[];landed=None;moving=None;unpaired=0;malformed=0
    minimum_hp=None;timeline=[]
    for at,event,raw in rows:
        counts[event]+=1
        data=json.loads(raw)
        if event in ('kill_verified','farming_area_reached'):
            timeline.append({**data,'time':at,'event':event})
        if event=='kill_verified':
            count=data.get('count')
            if type(count) is int and count>0:kills.append((at,count))
            else:malformed+=1
        elif event=='movement_attempt':
            if landed is not None:unpaired+=1
            landed=None;moving=data.get('movement')
        elif event=='movement_verified' and data.get('arrived') and moving=='jump':
            landed=at;moving=None
        elif event=='attack_attempt':
            if landed is not None and data.get('ability')=='Scatter' and 0<=at-landed<=15:
                delays.append(at-landed)
            landed=None
        elif event=='trial_stopped':
            landed=moving=None
        elif event=='health_observation':
            ratio=data.get('health_ratio')
            if type(ratio) in (int,float) and 0<=ratio<=1:
                minimum_hp=ratio if minimum_hp is None else min(minimum_hp,ratio)
    route_counts=Counter();travels=[]
    path=root/'reports/overnight/events.jsonl'
    if path.exists():
        with path.open(encoding='utf-8') as source:
            for line in source:
                try:r=json.loads(line)
                except ValueError:continue
                if not start<=r.get('time',0)<=observed:continue
                route_counts[r['event']]+=1
                timeline.append(r)
                if r['event']=='runback_finished':travels.append(r)
    windows={str(n):{'complete':elapsed>=n,
        'kills':sum(k for at,k in kills if observed-n<at<=observed),
        'kills_per_minute':round(sum(k for at,k in kills if observed-n<at<=observed)*60/n,2)} for n in (60,300,900,3600)}
    total=sum(k for _,k in kills)
    cycles=completed_cycles(timeline)
    result={'started_at':start,'observed_until':observed,'generated_at':now,'complete':now>=end,
        'elapsed_seconds':elapsed,'verified_kills':total,'overall_kills_per_minute':round(total*60/elapsed,2),
        'windows':windows,'events':dict(counts),'route_events':dict(route_counts),'travel_receipts':travels,
        'minimum_observed_hp_percent':None if minimum_hp is None else round(minimum_hp*100,1),
        'jump_to_scatter':{'samples':len(delays),'median_seconds':round(statistics.median(delays),3) if delays else None,
            'p95_seconds':round(sorted(delays)[min(len(delays)-1,int(len(delays)*.95))],3) if delays else None,
            'verified_jumps_without_paired_attack':unpaired,
            'note':'Arrival-to-input delay; unpaired jumps alone do not prove a missed attack opportunity.'},
        'natural_restock_storage_return_observed':bool(cycles),'completed_cycles':cycles,'malformed_kill_events':malformed,
        'rate_target_met':now>=end and total*60/elapsed>=40,
        'all_downtime_retained':True,'farming_not_stopped_by_report':True}
    result.update(original_deadline=end,extended_observation=extend)
    output='unified-validation-extended-report.json' if extend else 'unified-validation-report.json'
    write_json(root/'reports/performance'/output,result)
    return result


if __name__=='__main__':
    result=report()
    print(json.dumps({k:v for k,v in result.items() if k not in ('travel_receipts','events','route_events')},indent=2))
