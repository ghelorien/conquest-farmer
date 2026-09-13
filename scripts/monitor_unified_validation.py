"""Read-only, two-hour parity telemetry; never controls or stops the farmer."""
from collections import Counter
import json
import hashlib
from pathlib import Path
import sqlite3
import time
from conquest.discord_notify import read_json,write_json

ROOT=Path.cwd()
CONFIG=ROOT/'reports/performance/unified-validation.json'
OUT=ROOT/'reports/performance/unified-validation-status.json'
RELEASE_DIRECTORIES=frozenset({'src','scripts','docs','tests'})
RELEASE_FILES=frozenset({'AGENTS.md','README.md','pyproject.toml'})


def source_integrity(config):
    """Check only manifest-listed source files; never walk live data junctions."""
    try:
        root=Path(config['release_root']).resolve(strict=True)
        manifest=Path(config['release_manifest_path']).resolve(strict=True)
        if manifest!=root/'RELEASE-MANIFEST.json':return False
        raw=manifest.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=config['release_manifest_sha256']:return False
        value=json.loads(raw)
        if value.get('version')!=1 or not value.get('files'):return False
        for relative,digest in value['files'].items():
            path=Path(relative)
            if path.is_absolute() or not path.parts or '..' in path.parts:return False
            allowed=((len(path.parts)>1 and path.parts[0] in RELEASE_DIRECTORIES)
                     or (len(path.parts)==1 and path.name in RELEASE_FILES))
            if not allowed:return False
            # Reject junctions/symlinks as well as lexical traversal. A
            # release entry may never alias an allowlisted name to private
            # runtime data, even when both paths remain beneath root.
            candidate=root
            for part in path.parts:
                candidate=candidate/part
                if (candidate.is_symlink()
                        or getattr(candidate,'is_junction',lambda:False)()):return False
            target=candidate.resolve(strict=True)
            if not target.is_relative_to(root):return False
            if not target.is_file():return False
            if hashlib.sha256(target.read_bytes()).hexdigest()!=digest:return False
        return True
    except (OSError,ValueError,KeyError,TypeError,AttributeError):return False


def qualification(config,now,kills,app,route,previous):
    """Read both durable journals and keep every observed interruption."""
    from contextlib import contextmanager,closing
    from conquest.character_context import state_path
    from conquest.cycle_validation import evaluate
    from conquest.merchants.delivery_operation import status
    class ReadOnlyJournal:
        @contextmanager
        def db(self):
            path=Path(state_path('reports/banking/merchant-deliveries.sqlite3')).resolve()
            with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=1)) as db:
                db.row_factory=sqlite3.Row
                yield db
        def trace(self,key):
            with self.db() as db:
                return [dict(r) for r in db.execute('SELECT stage,status,payload FROM transaction_steps WHERE transaction_id=? ORDER BY id',(key,))]
    interruptions=list(previous.get('interruptions',[]))
    source_checked=previous.get('source_checked_at',0)
    if not source_checked or now-source_checked>=60 or now>=config['started_at']+config['duration_seconds']:
        source_checked=now
        if not source_integrity(config):interruptions.append('Release source manifest is missing or changed during qualification')
    for name,actual in (('app_pid',app.get('pid')),('route_pid',route.get('pid'))):
        if not config.get(name):interruptions.append('Qualification lacks baseline '+name)
        elif actual!=config[name]:interruptions.append(name+' changed during qualification')
    for name,snapshot in (('App',app),('Route',route)):
        if not -2<=now-snapshot.get('updated_at',0)<=15:
            interruptions.append(name+' telemetry became stale during qualification')
    if route.get('phase') in ('stopped','needs_attention','failed'):
        interruptions.append('Native route stopped or required attention')
    if config.get('kill_session_started_at')!=app.get('kill_session_started_at'):
        interruptions.append('Verified kill session changed during qualification')
    if route.get('validation_cycle'):
        interruptions.append('Forced restock cannot qualify a natural cycle')
    visits=read_json(state_path('reports/banking/town-visit.json'))
    visits=list(visits.get('history',[]))+([visits] if visits else [])
    source=ReadOnlyJournal();deliveries=[]
    with source.db() as db:
        rows=db.execute("SELECT id,created,updated,before_json,character,phase FROM transactions WHERE kind='farmer_delivery' AND created>=?",(config['started_at'],)).fetchall()
    from conquest.character_context import database_character
    for row in rows:
        before=json.loads(row['before_json']);merchant=before.get('merchant',{});farmer=before.get('farmer',{})
        record=database_character(row['character'])
        deliveries.append({**status(source,row['id']),'started_at':row['created'],
            'verified_at':row['updated'] if row['phase']=='verified' else None,
            'merchant_identity':{'record_id':row['character'],'profile_id':getattr(record,'profile_id',None),
                'character':merchant.get('character'),'character_uid':merchant.get('character_uid')},
            'farmer_identity':{'profile_id':before.get('farmer_profile_id'),
                'character':farmer.get('character'),'character_uid':farmer.get('character_uid')}})
    with closing(sqlite3.connect(Path(state_path('reports/merchants/journal.sqlite3')).resolve().as_uri()+'?mode=ro',uri=True,timeout=1)) as db:
        refills=[{**json.loads(raw),'event_id':event_id,'observed_at':at,'character':character}
            for event_id,character,at,raw in db.execute("SELECT id,character,timestamp,payload FROM events WHERE event='refill_checked' AND timestamp BETWEEN ? AND ?",
            (config['started_at'],min(now,config['started_at']+config['duration_seconds'])))]
        pending_merchants=[row[0] for row in db.execute("SELECT id FROM transactions WHERE created>=? AND phase NOT IN ('verified','aborted')",(config['started_at'],))]
    # Pending work during a normal action is not a permanent interruption. Its
    # presence at the qualification boundary still prevents a successful run.
    pending_notes=['New merchant transactions remain unresolved'] if pending_merchants else []
    interruptions=list(dict.fromkeys(interruptions))
    result=evaluate(config,now=now,kill_events=kills,visits=visits,deliveries=deliveries,refill_events=refills,interruptions=interruptions+pending_notes)
    return {**result,'target_met':result['qualified'],'interruptions':interruptions,
            'source_checked_at':source_checked,
            'unresolved_merchant_operations':pending_merchants,
            'refill_results':refills,'delivery_results':deliveries}


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
                if not start<=at<=end:continue
                counts[event]+=1
                if event=='kill_verified':kills.append([at,json.loads(raw)['count']])
            app=read_json(ROOT/'reports/desktop-farming/app-state.json')
            route=read_json(ROOT/'reports/overnight/status.json')
            elapsed=max(min(now,end)-start,1)
            windows={str(n):{'complete':elapsed>=n,'kills':sum(k for t,k in kills if now-n<t<=now),
                'kills_per_minute':round(sum(k for t,k in kills if now-n<t<=now)*60/n,2)} for n in (60,300,900)}
            report={'started_at':start,'observed_at':now,'cursor':cursor,'phase':'complete' if now>=end else 'monitoring',
                'elapsed_seconds':round(elapsed,1),'total_kills':sum(k for t,k in kills),
                'overall_kills_per_minute':round(sum(k for t,k in kills)*60/elapsed,2),
                'windows':windows,'event_counts':dict(counts),'kill_events':kills,
                'app_pid':app.get('pid'),'route_phase':route.get('phase'),'route_activity':route.get('activity'),
                'route_error':route.get('detail'),'route_cycles':route.get('cycles'),
                'target_met':False,
                'observer_is_read_only':True}
            report.update(qualification(config, now, kills, app, route, old if same else {}))
            write_json(OUT,report)
            old=report;same=True
        except (OSError,ValueError,sqlite3.Error):
            if not same:old={};same=True
            old.setdefault('interruptions',[]).append('Qualification telemetry was unavailable during observation')
            if now>=end:raise
        if now>=end:break
        time.sleep(5)

if __name__=='__main__':main()
