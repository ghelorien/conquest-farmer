"""Durable required-town journeys completed only by resumed verified hunting."""
import json
from contextlib import closing
import math
from pathlib import Path
import sqlite3
import time
import uuid
from copy import deepcopy

from conquest.character_context import current, farmer_name, state_path
from conquest.discord_notify import read_json, write_json


def profile_id():
    context=current()
    return context.profile.id if context else farmer_name()


def _process_identity(target):
    return (isinstance(target,dict) and type(target.get('pid')) is int and target['pid']>0
            and type(target.get('creation_time_100ns')) is int and target['creation_time_100ns']>0
            and isinstance(target.get('path'),str) and bool(target['path']))


def _checkpoint_available(observed,now):
    return (observed.get('available') is True
            and type(observed.get('session_id')) in (int,float)
            and math.isfinite(observed['session_id']) and 0<observed['session_id']<=now
            and type(observed.get('observed_at')) in (int,float)
            and 0<=now-observed['observed_at']<=5
            and type(observed.get('cursor')) is int and observed['cursor']>=0
            and type(observed.get('kills')) is int and observed['kills']>=0)


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

    def begin(self,reason,*,hunt_map_id,route_id=None,target=None,urgent_items=None):
        if reason not in ('restock','urgent_banking','merchant_acceptance'):
            raise ValueError('A town visit requires an existing restock or urgent-bank obligation')
        urgent_intent=None
        if reason=='urgent_banking' and target is not None and urgent_items is not None:
            if not _process_identity(target) or not isinstance(urgent_items,list) or not urgent_items:
                raise ValueError('Urgent bank intent needs an exact process and inventory items')
            urgent_intent=[]
            for item in urgent_items:
                if (not isinstance(item,dict) or type(item.get('uid')) is not int
                        or item['uid']<=0 or type(item.get('type_id')) is not int
                        or item['type_id']<=0):
                    raise ValueError('Urgent bank item identity is invalid')
                urgent_intent.append({'uid':item['uid'],'type_id':item['type_id']})
            if len({item['uid'] for item in urgent_intent})!=len(urgent_intent):
                raise ValueError('Urgent bank item identities are duplicated')
        old=self.state()
        if old.get('phase') in ('town_work','returning_to_hunt'):
            prior_intent=old.get('urgent_intent')
            terminal_urgent=(bool(old.get('town_work_completed_at'))
                             and old.get('town_work_completed_kind')=='urgent_banking')
            if urgent_intent is not None:
                if (prior_intent is not None
                        and (prior_intent!=urgent_intent or old.get('urgent_target')!=target)
                        and not terminal_urgent):
                    raise ValueError('Unresolved urgent bank intent changed during the town visit')
                if (prior_intent is None and not terminal_urgent
                        and any(old.get(field) for field in
                                ('urgent_target','urgent_recovery_claim',
                                 'urgent_recovery_attempted_at',
                                 'urgent_banking_tail_completed_at',
                                 'urgent_followup_completed_at'))):
                    raise ValueError('Unresolved urgent state needs reconciliation')
            changed=reason not in old['reasons']
            if changed:old['reasons'].append(reason)
            if old.get('town_work_completed_at'):
                completed={
                    'completed_at':old.pop('town_work_completed_at'),
                    'kind':old.pop('town_work_completed_kind',None)}
                if completed['kind']=='urgent_banking':
                    for field in ('urgent_intent','urgent_target',
                                  'urgent_banking_tail_completed_at','urgent_followup_completed_at',
                                  'urgent_recovery_claim','urgent_recovery_attempted_at'):
                        if field in old:completed[field]=old.pop(field)
                old.setdefault('town_work_history',[]).append(completed)
                old['phase']='town_work'
                changed=True
            if urgent_intent is not None and old.get('urgent_intent') is None:
                old.update(urgent_intent=urgent_intent,urgent_target=deepcopy(target))
                changed=True
            if changed:write_json(self.path,old)
            return old
        history=list(old.get('history',[]))
        if old:history.append({key:value for key,value in old.items() if key!='history'})
        row={'version':2,'town_visit_id':uuid.uuid4().hex,'farmer_profile_id':self.profile,
             'phase':'town_work','reasons':[reason],'required_at':self.clock(),
             'hunt_map_id':hunt_map_id,'route_id':route_id,'baseline':self.probe(),
             'history':history}
        if urgent_intent is not None:
            row.update(urgent_intent=urgent_intent,urgent_target=deepcopy(target))
        write_json(self.path,row)
        return row

    def record_urgent_tail(self,stage,*,target):
        """Checkpoint successfully returned native work, not an inferred result."""
        field={'banking':'urgent_banking_tail_completed_at',
               'followup':'urgent_followup_completed_at'}.get(stage)
        if field is None:raise ValueError('Unknown urgent bank tail stage')
        row=self.state()
        if (row.get('phase')!='town_work' or 'urgent_banking' not in row.get('reasons',[])
                or not _process_identity(target) or row.get('urgent_target')!=target
                or not row.get('urgent_intent')):
            raise ValueError('Urgent bank tail does not match the active visit and process')
        if stage=='followup' and not row.get('urgent_banking_tail_completed_at'):
            raise ValueError('Urgent bank followup cannot precede banking')
        if not row.get(field):
            row[field]=self.clock();write_json(self.path,row)
        return row

    def claim_restock_tail_recovery(self,*,visit_id,target,evidence):
        """Bind one reviewed interrupted restock tail; this sends no game input."""
        row=self.state()
        if (row.get('phase')!='town_work' or row.get('reasons')!=['restock']
                or row.get('town_visit_id')!=visit_id or row.get('town_work_completed_at')
                or row.get('restock_recovery_claim') or row.get('restock_recovery_attempted_at')
                or not _process_identity(target) or not isinstance(evidence,dict)
                or not evidence.get('reference')):
            raise ValueError('Restock recovery claim does not match one unfinished visit')
        row.update(restock_recovery_target=deepcopy(target),
                   restock_recovery_claim={**deepcopy(evidence),'claimed_at':self.clock()})
        write_json(self.path,row)
        return row

    def start_restock_tail_once(self,*,target):
        """Consume the reviewed continuation before its first transaction input."""
        row=self.state()
        if (row.get('phase')!='town_work' or row.get('reasons')!=['restock']
                or not row.get('restock_recovery_claim')
                or row.get('restock_recovery_attempted_at')
                or row.get('town_work_completed_at')
                or not _process_identity(target) or row.get('restock_recovery_target')!=target):
            raise ValueError('Restock recovery is unclaimed or already attempted')
        row['restock_recovery_attempted_at']=self.clock()
        write_json(self.path,row)
        return row

    def claim_urgent_recovery(self,*,visit_id,target,intent,evidence):
        """Persist one explicitly reviewed legacy continuation, without game input."""
        row=self.state()
        if (row.get('phase')!='town_work' or row.get('reasons')!=['urgent_banking']
                or row.get('town_visit_id')!=visit_id or row.get('urgent_recovery_claim')
                or row.get('urgent_recovery_attempted_at') or not _process_identity(target)
                or not isinstance(intent,list) or not intent
                or not isinstance(evidence,dict) or not evidence.get('reference')):
            raise ValueError('Urgent recovery claim does not match one unfinished visit')
        if row.get('urgent_target') is not None and row['urgent_target']!=target:
            raise ValueError('Urgent recovery process differs from the original visit')
        if row.get('urgent_intent') is not None and row['urgent_intent']!=intent:
            raise ValueError('Urgent recovery items differ from the original visit')
        if (len({item.get('uid') for item in intent})!=len(intent)
                or any(type(item.get('uid')) is not int or item['uid']<=0
                       or type(item.get('type_id')) is not int or item['type_id']<=0
                       or item.get('storage_map_id',1011) not in (1011,1036)
                       for item in intent)):
            raise ValueError('Urgent recovery item identities are invalid')
        row.update(urgent_target=deepcopy(target),urgent_intent=deepcopy(intent),
                   urgent_recovery_claim={**deepcopy(evidence),'claimed_at':self.clock()})
        write_json(self.path,row)
        return row

    def start_urgent_recovery_once(self,*,target):
        row=self.state()
        if (row.get('phase')!='town_work' or row.get('reasons')!=['urgent_banking']
                or not row.get('urgent_recovery_claim') or not _process_identity(target)
                or row.get('urgent_target')!=target or row.get('urgent_recovery_attempted_at')):
            raise ValueError('Urgent recovery is unclaimed or already attempted')
        row['urgent_recovery_attempted_at']=self.clock()
        write_json(self.path,row)
        return row

    def reconcile_urgent_town_work(self,*,target,bag,warehouse,meteor,transaction_holds,needs_town):
        """Complete only a fully checkpointed urgent tail with fresh native ownership."""
        row=self.state()
        if row.get('phase')!='town_work' or row.get('reasons')!=['urgent_banking']:
            return False
        intent=row.get('urgent_intent')
        if (not isinstance(intent,list) or not intent or not _process_identity(target)
                or row.get('urgent_target')!=target
                or not row.get('urgent_banking_tail_completed_at')
                or not row.get('urgent_followup_completed_at')
                or transaction_holds or needs_town):
            return False
        try:
            wanted={item['uid']:item['type_id'] for item in intent}
            if len(wanted)!=len(intent):return False
            carried={item['uid']:item['type_id'] for item in bag['items']}
            phoenix={item['uid']:item['type_id'] for item in warehouse['items']}
            # Market and Phoenix are separate warehouses. Market ownership is
            # evidenced by its verified pre-return receipts and terminal bank
            # check; only Phoenix-destined items require this live local deque.
            market=[item for item in intent if item.get('storage_map_id',1011)==1036]
            local=[item for item in intent if item.get('storage_map_id',1011)==1011]
            if (any(uid in carried for uid in wanted)
                    or any(phoenix.get(item['uid'])!=item['type_id'] for item in local)
                    or any(item['uid'] in phoenix for item in market)):
                return False
            # A completed Meteor journal is required when this visit packed a
            # scroll. It must show exact terminal receipts for every banked UID.
            started=meteor.get('started_at')
            if market or (type(started) in (int,float) and started>=row['required_at']):
                verified=meteor.get('market_verified_at');finished=meteor.get('completed_at')
                if (meteor.get('phase')!='completed' or meteor.get('exchange_verified') is not True
                        or type(started) not in (int,float) or started<row['required_at']
                        or type(verified) not in (int,float)
                        or type(finished) not in (int,float)
                        or not started<=verified<=finished):
                    return False
                receipts={receipt.get('stored'):receipt for receipt in meteor.get('receipts',[])
                          if receipt.get('verified_in_warehouse') is True}
                if (any(receipts.get(item['uid'],{}).get('type_id')!=item['type_id']
                        for item in market)
                        or receipts.get(meteor.get('scroll_uid'),{}).get('type_id')!=720027
                        or meteor.get('scroll_uid') in carried
                        or meteor.get('scroll_uid') in phoenix
                        or meteor.get('user_confirmed_scroll_consumption')
                        or meteor.get('user_confirmed_scroll_transfer')):
                    return False
        except (KeyError,TypeError,ValueError):
            return False
        self.complete_town_work('urgent_banking')
        return True

    def complete_town_work(self,kind):
        """Persist the successful end of native town work before return begins.

        This must be called only after banking, supplies and any required
        handoff have returned successfully. An absent marker after a crash is
        deliberately not inferred from full supplies or a later hunting kill:
        the last warehouse or monetary input may have been submitted without
        its receipt reaching this process.
        """
        if kind not in ('restock','urgent_banking','merchant_acceptance'):
            raise ValueError('Unknown completed town work')
        row=self.state()
        if row.get('phase')!='town_work':
            raise ValueError('No active town work can be completed')
        if kind not in row.get('reasons',[]):
            raise ValueError('Town work completion does not match the visit')
        if row.get('town_work_completed_at'):
            return row
        row.update(town_work_completed_at=self.clock(),town_work_completed_kind=kind)
        write_json(self.path,row)
        return row

    def require_town_work_complete(self):
        row=self.state()
        if row.get('phase') in ('town_work','returning_to_hunt') and not row.get('town_work_completed_at'):
            raise ValueError(
                'Unfinished town work needs read-only transaction reconciliation before return; '
                'do not replay a warehouse, purchase, or monetary action')
        return row

    def returning(self,hunt_map_id,*,target):
        row=self.require_town_work_complete()
        if row.get('phase') not in ('town_work','returning_to_hunt'):return None
        if not _process_identity(target):
            raise ValueError('Required town return needs the exact game process identity')
        if row.get('phase')=='returning_to_hunt':
            if row.get('return_map_id')!=hunt_map_id or row.get('return_target')!=target:
                raise ValueError('Required town return target changed; review the unfinished visit')
            return row  # Restart/hunt re-entry does not discard the first return baseline.
        row.update(phase='returning_to_hunt',return_started_at=self.clock(),
                   return_map_id=hunt_map_id,return_target=deepcopy(target),return_baseline=self.probe())
        write_json(self.path,row)
        return row

    def observe_hunting(self,health):
        row=self.state()
        if row.get('phase')!='returning_to_hunt':return None
        if not row.get('town_work_completed_at'):
            # A legacy or interrupted visit must not become "complete" merely
            # because a later process observed a verified hunting kill.
            return None
        data=health.get('embedded_controls',{});control=data.get('control',{});life=data.get('life') or {}
        now=self.clock();baseline=row.get('return_baseline') or {}
        if (control.get('enabled') is not True or control.get('paused') or data.get('manual_mouse') is not False
                or data.get('manual_input_fence') is not False
                or life.get('dead_candidate') is not False or life.get('map_id')!=row['return_map_id']
                or type(life.get('current_hp')) not in (int,float) or not life['current_hp']>0
                or not 0<=now-data.get('observed_at',0)<=1
                or not _process_identity(row.get('return_target'))
                or health.get('target')!=row['return_target']):
            return None
        observed=(kill_checkpoint(now=now,after_cursor=baseline.get('cursor'),
                    after_time=max(row['return_started_at'],baseline.get('observed_at',0)))
                  if self.default_probe else self.probe())
        if not _checkpoint_available(observed,now):return None
        if not baseline.get('available'):
            # Missing telemetry is not a reason to stop hunting. Establish an
            # explicit later baseline, then require a subsequent verified kill.
            row.update(return_baseline=observed,return_baseline_observed_at=now)
            write_json(self.path,row)
            return None
        if observed['session_id']!=baseline.get('session_id'):
            # Stop closes SessionKills; a later On starts a new counter without
            # changing the game process. Preserve the old proof and establish a
            # fresh baseline only under the same live, unfenced return identity.
            # The observation doing this can never complete the visit itself.
            previous=baseline.get('session_id')
            if (type(previous) not in (int,float) or not math.isfinite(previous)
                    or observed['session_id']<=previous
                    or observed['session_id']<baseline.get('observed_at',now)
                    or observed['cursor']<baseline.get('cursor',0)):
                return None
            row.setdefault('return_baseline_history',[]).append({
                'reason':'kill_session_changed','replaced_at':now,
                'previous_baseline':baseline,'new_session_id':observed['session_id'],
                'target':deepcopy(health['target']),'map_id':life['map_id']})
            row.update(return_baseline=observed,return_baseline_observed_at=now)
            write_json(self.path,row)
            return None
        if (observed['cursor']<baseline['cursor']
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
