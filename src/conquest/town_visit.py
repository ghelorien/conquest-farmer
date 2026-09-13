"""Durable required-town journeys completed only by resumed verified hunting."""
import json
from contextlib import closing
import math
from pathlib import Path
import sqlite3
import time
import uuid

from conquest.character_context import current, farmer_name, state_path
from conquest.discord_notify import read_json, write_json


def profile_id():
    context=current()
    return context.profile.id if context else farmer_name()


def kill_checkpoint(*, now=None, output=None, after_cursor=None, after_time=None):
    """Read the existing counter and verified-event journal; never reset either."""
    now=time.time() if now is None else now
    output=Path(output or state_path('reports/desktop-farming'))
    app=read_json(output/'app-state.json')
    result={'available':False,'observed_at':now}
    if (not isinstance(app,dict) or app.get('character')!=farmer_name() or app.get('kill_metrics_note')
            or app.get('kill_session_active') is not True
            or type(app.get('kill_session_started_at')) not in (int,float)
            or not math.isfinite(app['kill_session_started_at'])
            or type(app.get('kills')) is not int or app['kills']<0
            or type(app.get('updated_at')) not in (int,float)
            or not 0<=now-app.get('updated_at',0)<=5):
        return {**result,'reason':'Verified kill counter is unavailable or stale'}
    try:
        with closing(sqlite3.connect((output/'trial.sqlite3').resolve().as_uri()+'?mode=ro',
                                     uri=True,timeout=.05)) as db:
            db.execute('BEGIN')
            cursor=db.execute('SELECT COALESCE(MAX(rowid),0) FROM events').fetchone()[0]
            row=db.execute("SELECT rowid,time,payload FROM events WHERE event='kill_verified' "
                           'ORDER BY rowid DESC LIMIT 1').fetchone()
            resumed=(db.execute("SELECT rowid,time,payload FROM events WHERE event='kill_verified' "
                                'AND rowid>? AND time>=? ORDER BY rowid LIMIT 1',
                                (after_cursor,after_time)).fetchone()
                     if after_cursor is not None and after_time is not None else None)
        def evidence(value):
            if value is None:return None
            count=json.loads(value[2]).get('count')
            if type(count) is not int or not 1<=count<=32:raise ValueError('Invalid verified kill')
            if type(value[1]) not in (int,float) or not math.isfinite(value[1]):raise ValueError('Invalid kill time')
            return {'rowid':value[0],'time':value[1],'count':count}
        return {**result,'available':True,'cursor':cursor,'last_kill':evidence(row),
                'resume_kill':evidence(resumed),
                'session_id':app['kill_session_started_at'],'kills':app['kills']}
    except (OSError,ValueError,TypeError,sqlite3.Error):
        return {**result,'reason':'Verified kill journal is unavailable'}


class TownVisit:
    def __init__(self,path=None,*,clock=time.time,probe=None,profile=None):
        self.path=Path(path or state_path('reports/banking/town-visit.json'))
        self.clock=clock
        self.probe=probe or (lambda:kill_checkpoint(now=self.clock()))
        self.default_probe=probe is None
        self.profile=profile or profile_id()

    def state(self):
        row=read_json(self.path)
        if row and row.get('farmer_profile_id')!=self.profile:
            raise ValueError('Required town visit belongs to another farmer profile')
        return row

    def active_id(self):
        row=self.state()
        return row.get('town_visit_id') if row.get('phase') in ('town_work','returning_to_hunt') else None

    def begin(self,reason,*,hunt_map_id,route_id=None):
        if reason not in ('restock','urgent_banking'):
            raise ValueError('A town visit requires an existing restock or urgent-bank obligation')
        old=self.state()
        if old.get('phase') in ('town_work','returning_to_hunt'):
            if reason not in old['reasons']:
                old['reasons'].append(reason);write_json(self.path,old)
            return old
        history=list(old.get('history',[]))
        if old:history.append({key:value for key,value in old.items() if key!='history'})
        row={'version':1,'town_visit_id':uuid.uuid4().hex,'farmer_profile_id':self.profile,
             'phase':'town_work','reasons':[reason],'required_at':self.clock(),
             'hunt_map_id':hunt_map_id,'route_id':route_id,'baseline':self.probe(),
             'history':history}
        write_json(self.path,row)
        return row

    def returning(self,hunt_map_id,*,target):
        row=self.state()
        if row.get('phase') not in ('town_work','returning_to_hunt'):return None
        if (row.get('phase')=='returning_to_hunt' and row.get('return_map_id')==hunt_map_id
                and row.get('return_target')==target):
            return row  # Restart/hunt re-entry does not discard the first return baseline.
        row.update(phase='returning_to_hunt',return_started_at=self.clock(),
                   return_map_id=hunt_map_id,return_target=target,return_baseline=self.probe())
        write_json(self.path,row)
        return row

    def observe_hunting(self,health):
        row=self.state()
        if row.get('phase')!='returning_to_hunt':return None
        data=health.get('embedded_controls',{});control=data.get('control',{});life=data.get('life') or {}
        now=self.clock();baseline=row.get('return_baseline') or {}
        if (control.get('enabled') is not True or control.get('paused') or data.get('manual_mouse')
                or life.get('dead_candidate') is not False or life.get('map_id')!=row['return_map_id']
                or not 0<=now-data.get('observed_at',0)<=1
                or not row.get('return_target') or health.get('target')!=row['return_target']):
            return None
        observed=(kill_checkpoint(now=now,after_cursor=baseline.get('cursor'),
                    after_time=max(row['return_started_at'],baseline.get('observed_at',0)))
                  if self.default_probe else self.probe())
        if not observed.get('available'):return None
        if not baseline.get('available'):
            # Missing telemetry is not a reason to stop hunting. Establish an
            # explicit later baseline, then require a subsequent verified kill.
            row.update(return_baseline=observed,return_baseline_observed_at=now)
            write_json(self.path,row)
            return None
        first=row.get('baseline') or {}
        if (observed['session_id']!=baseline['session_id']
                or first.get('available') and observed['session_id']!=first['session_id']
                or observed['cursor']<baseline['cursor']
                or observed['kills']<=baseline['kills']):
            return None
        killed=observed.get('resume_kill') if self.default_probe else observed.get('last_kill')
        if (not killed or killed['rowid']<=baseline['cursor']
                or not max(row['return_started_at'],baseline['observed_at'])<=killed['time']<=now):
            return None
        row.update(phase='complete',completed_at=now,resumed_hunting=observed,
                   first_verified_resume_kill=killed,
                   elapsed_seconds=now-row['required_at'])
        write_json(self.path,row)
        return row
