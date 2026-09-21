"""Durable, operator-started all-character manual handoff.

This is intentionally an observation boundary, not a trading feature.  It
never drives the client.  The caller supplies already-read native-memory
snapshots and keeps the normal Stop/input fences authoritative.
"""
import json
import time
import uuid

from conquest.merchants.manual_sessions import canonical_ownership, ownership_digest


SCHEMA = '''
CREATE TABLE IF NOT EXISTS manual_handoffs(
 id TEXT PRIMARY KEY, phase TEXT NOT NULL CHECK(phase IN ('preparing','ready','ending','needs_attention','completed')),
 operator TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
 end_requested INTEGER NOT NULL DEFAULT 0, reason TEXT, completed_at REAL);
CREATE TABLE IF NOT EXISTS manual_handoff_participants(
 session_id TEXT NOT NULL REFERENCES manual_handoffs(id), target_profile_id TEXT NOT NULL,
 role TEXT NOT NULL CHECK(role IN ('Farmer','Merchant')), process_json TEXT, character_json TEXT,
 baseline_json TEXT, baseline_at REAL, last_json TEXT, last_at REAL, stable_digest TEXT,
 stable_since REAL, saw_window INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(session_id,target_profile_id));
CREATE TABLE IF NOT EXISTS manual_handoff_audit(
 id INTEGER PRIMARY KEY, session_id TEXT NOT NULL REFERENCES manual_handoffs(id), event TEXT NOT NULL,
 at REAL NOT NULL, payload TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS manual_handoff_no_delete BEFORE DELETE ON manual_handoffs
 BEGIN SELECT RAISE(ABORT,'Manual handoff is durable'); END;
CREATE TRIGGER IF NOT EXISTS manual_handoff_audit_no_update BEFORE UPDATE ON manual_handoff_audit
 BEGIN SELECT RAISE(ABORT,'Manual handoff audit is immutable'); END;
CREATE TRIGGER IF NOT EXISTS manual_handoff_audit_no_delete BEFORE DELETE ON manual_handoff_audit
 BEGIN SELECT RAISE(ABORT,'Manual handoff audit is immutable'); END;
'''


class ManualHandoffStore:
    def __init__(self, journal):
        self.journal = journal
        with journal.db() as db:
            db.executescript(SCHEMA)

    @staticmethod
    def _audit(db, session_id, event, now, **payload):
        db.execute('INSERT INTO manual_handoff_audit(session_id,event,at,payload) VALUES(?,?,?,?)',
                   (session_id,event,now,json.dumps(payload,sort_keys=True)))

    def active(self):
        with self.journal.db() as db:
            row=db.execute("SELECT * FROM manual_handoffs WHERE phase!='completed' ORDER BY created_at DESC LIMIT 1").fetchone()
            return self._view(db,row) if row else None

    def participant_identities(self, session_id):
        """Restart-only binding proof; never exposed as an operator command."""
        with self.journal.db() as db:
            rows=db.execute('SELECT target_profile_id,process_json FROM manual_handoff_participants WHERE session_id=?',(session_id,))
            return {row['target_profile_id']:json.loads(row['process_json']) if row['process_json'] else None
                    for row in rows}

    def identity_changed(self, target, reason, *, now=None):
        now=time.time() if now is None else float(now)
        with self.journal.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute("SELECT * FROM manual_handoffs WHERE phase!='completed' ORDER BY created_at DESC LIMIT 1").fetchone()
            if row is None:return None
            part=db.execute('SELECT process_json FROM manual_handoff_participants WHERE session_id=? AND target_profile_id=?',(row['id'],target)).fetchone()
            if part is None:return self._view(db,row)
            if not part['process_json']:
                return self._view(db,row)
            db.execute("UPDATE manual_handoffs SET phase='needs_attention',updated_at=?,reason=? WHERE id=?",
                       (now,'Game-process identity changed for '+target,row['id']))
            self._audit(db,row['id'],'identity_changed',now,target=target,reason=str(reason)[:160])
            return self._view(db,db.execute('SELECT * FROM manual_handoffs WHERE id=?',(row['id'],)).fetchone())

    def _view(self, db, row):
        if row is None:return None
        result=dict(row)
        result['end_requested']=bool(result['end_requested'])
        result['holds_automation']=result['phase']!='completed'
        result['ever_approved']=True  # Existing coordinator global-fence convention.
        result['target_profile_id']='__operator_manual_handoff__'
        # ManualRuntime.status projects every durable hold through the legacy
        # manual-session shape.  A global operator handoff has no request, but
        # must still provide those nullable fields to status/UI consumers.
        result.update(visitor=None,approval_binding=None,request_state=None,
                      expires_at=None,rebaseline=False)
        result['participants']=[dict(item) for item in db.execute(
            'SELECT target_profile_id,role,baseline_at,last_at,saw_window FROM manual_handoff_participants WHERE session_id=? ORDER BY role,target_profile_id',(row['id'],))]
        return result

    def start(self, participants, *, operator, now=None):
        now=time.time() if now is None else float(now)
        if not isinstance(operator,str) or not operator.strip():raise ValueError('Operator is required')
        if not participants or any(not isinstance(k,str) or not k for k in participants):
            raise ValueError('Attached Farmer and merchant profiles are required')
        if len(set(participants))!=len(participants):raise ValueError('Duplicate handoff participant')
        key='operator-handoff:'+uuid.uuid4().hex
        with self.journal.db() as db:
            db.execute('BEGIN IMMEDIATE')
            existing=db.execute("SELECT id FROM manual_handoffs WHERE phase!='completed' LIMIT 1").fetchone()
            if existing:raise ValueError('A global manual handoff is already active')
            db.execute('INSERT INTO manual_handoffs(id,phase,operator,created_at,updated_at) VALUES(?,\'preparing\',?,?,?)',
                       (key,operator,now,now))
            for target,role in participants.items():
                db.execute('INSERT INTO manual_handoff_participants(session_id,target_profile_id,role) VALUES(?,?,?)',
                           (key,target,role))
            self._audit(db,key,'started',now,operator=operator,participants=participants)
            return self._view(db,db.execute('SELECT * FROM manual_handoffs WHERE id=?',(key,)).fetchone())

    def end(self, session_id, *, operator, now=None):
        now=time.time() if now is None else float(now)
        with self.journal.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM manual_handoffs WHERE id=?',(session_id,)).fetchone()
            if row is None:raise ValueError('Manual handoff not found')
            if row['phase']=='completed':return self._view(db,row)
            db.execute("UPDATE manual_handoffs SET phase=CASE WHEN phase='needs_attention' THEN phase ELSE 'ending' END,end_requested=1,updated_at=? WHERE id=?",(now,session_id))
            self._audit(db,session_id,'end_requested',now,operator=operator)
            return self._view(db,db.execute('SELECT * FROM manual_handoffs WHERE id=?',(session_id,)).fetchone())

    def unavailable(self, target, reason, *, now=None):
        now=time.time() if now is None else float(now)
        with self.journal.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute("SELECT * FROM manual_handoffs WHERE phase!='completed' ORDER BY created_at DESC LIMIT 1").fetchone()
            if not row:return None
            participant=db.execute('SELECT 1 FROM manual_handoff_participants WHERE session_id=? AND target_profile_id=?',(row['id'],target)).fetchone()
            if not participant:return self._view(db,row)
            # Read gaps are expected during a handoff. Keep waiting and retain
            # the fence; only a qualified identity contradiction is attention.
            db.execute('UPDATE manual_handoffs SET updated_at=?,reason=? WHERE id=?',(now,'Waiting for fresh '+target+' observation: '+str(reason)[:160],row['id']))
            # No old stability may bridge an unreadable interval.
            db.execute('UPDATE manual_handoff_participants SET stable_digest=NULL,stable_since=NULL WHERE session_id=?',(row['id'],))
            last=db.execute("SELECT 1 FROM manual_handoff_audit WHERE session_id=? AND event='reader_gap' AND json_extract(payload,'$.target')=? LIMIT 1",(row['id'],target)).fetchone()
            if not last:
                self._audit(db,row['id'],'reader_gap',now,target=target)
            return self._view(db,db.execute('SELECT * FROM manual_handoffs WHERE id=?',(row['id'],)).fetchone())

    def observe(self, target, snapshot, *, bot_busy=False, mouse_idle=True, now=None):
        now=time.time() if now is None else float(now)
        with self.journal.db() as db:
            db.execute('BEGIN IMMEDIATE')
            session=db.execute("SELECT * FROM manual_handoffs WHERE phase!='completed' ORDER BY created_at DESC LIMIT 1").fetchone()
            if not session:return None
            part=db.execute('SELECT * FROM manual_handoff_participants WHERE session_id=? AND target_profile_id=?',(session['id'],target)).fetchone()
            if not part:return self._view(db,session)
            try:
                proof=canonical_ownership(snapshot,require_closed=False)
                if not 0 <= now-snapshot['timestamp'] <= 2:raise ValueError('Fresh memory observation is required')
                # `created_at` is written only after the runtime has acquired
                # its coordinator mutex and installed the durable global
                # fence.  A previously-read snapshot cannot be a baseline.
                if snapshot['timestamp'] < session['created_at']:
                    raise ValueError('Observation predates the handoff fence')
                process=json.dumps(proof['identity'],sort_keys=True)
                character=json.dumps({k:proof[k] for k in ('character','character_uid','server')},sort_keys=True)
            except (ValueError,KeyError,TypeError) as error:
                db.execute('UPDATE manual_handoffs SET updated_at=?,reason=? WHERE id=?',(now,'Waiting for fresh '+target+' observation: '+str(error)[:160],session['id']))
                db.execute('UPDATE manual_handoff_participants SET stable_digest=NULL,stable_since=NULL WHERE session_id=?',(session['id'],))
                if not str(session['reason'] or '').startswith('Waiting for fresh '+target+' observation:'):
                    self._audit(db,session['id'],'reader_gap',now,target=target)
                return self._view(db,db.execute('SELECT * FROM manual_handoffs WHERE id=?',(session['id'],)).fetchone())
            if part['process_json'] and (part['process_json']!=process or part['character_json']!=character):
                db.execute("UPDATE manual_handoffs SET phase='needs_attention',updated_at=?,reason=? WHERE id=?",(now,'Game-process identity changed for '+target,session['id']))
                self._audit(db,session['id'],'identity_changed',now,target=target)
                return self._view(db,db.execute('SELECT * FROM manual_handoffs WHERE id=?',(session['id'],)).fetchone())
            if bot_busy:
                db.execute('UPDATE manual_handoffs SET updated_at=?,reason=? WHERE id=?',(now,'Waiting for active bot transaction/input to settle',session['id']))
                return self._view(db,db.execute('SELECT * FROM manual_handoffs WHERE id=?',(session['id'],)).fetchone())
            closed=snapshot.get('request') is None and snapshot.get('trade') is None
            baseline=part['baseline_json']
            if baseline is None:
                if not closed:
                    db.execute('UPDATE manual_handoff_participants SET process_json=?,character_json=?,last_json=?,last_at=?,saw_window=1 WHERE session_id=? AND target_profile_id=?',
                               (process,character,json.dumps(snapshot),snapshot['timestamp'],session['id'],target))
                else:
                    db.execute('UPDATE manual_handoff_participants SET process_json=?,character_json=?,baseline_json=?,baseline_at=?,last_json=?,last_at=?,stable_digest=?,stable_since=? WHERE session_id=? AND target_profile_id=?',
                               (process,character,json.dumps(snapshot),snapshot['timestamp'],json.dumps(snapshot),snapshot['timestamp'],ownership_digest(snapshot),snapshot['timestamp'],session['id'],target))
                    self._audit(db,session['id'],'baseline',now,target=target)
                return self._refresh(db,session['id'],now,mouse_idle=mouse_idle)
            if not closed:
                # A modal on any participant makes the global handoff
                # interval ambiguous; all participants must settle again.
                db.execute('UPDATE manual_handoff_participants SET stable_digest=NULL,stable_since=NULL WHERE session_id=?',(session['id'],))
                db.execute('UPDATE manual_handoff_participants SET last_json=?,last_at=?,stable_digest=NULL,stable_since=NULL,saw_window=1 WHERE session_id=? AND target_profile_id=?',
                           (json.dumps(snapshot),snapshot['timestamp'],session['id'],target))
                return self._refresh(db,session['id'],now,mouse_idle=mouse_idle)
            digest=ownership_digest(snapshot)
            since=part['stable_since'] if part['stable_digest']==digest else snapshot['timestamp']
            db.execute('UPDATE manual_handoff_participants SET last_json=?,last_at=?,stable_digest=?,stable_since=? WHERE session_id=? AND target_profile_id=?',
                       (json.dumps(snapshot),snapshot['timestamp'],digest,since,session['id'],target))
            return self._refresh(db,session['id'],now,mouse_idle=mouse_idle)

    def _refresh(self, db, session_id, now, *, mouse_idle=True):
        session=db.execute('SELECT * FROM manual_handoffs WHERE id=?',(session_id,)).fetchone()
        rows=list(db.execute('SELECT * FROM manual_handoff_participants WHERE session_id=?',(session_id,)))
        if session['phase']=='needs_attention':return self._view(db,session)
        if any(row['baseline_json'] is None for row in rows):
            db.execute("UPDATE manual_handoffs SET phase='preparing',updated_at=?,reason='Waiting for closed-window baseline' WHERE id=?",(now,session_id))
            return self._view(db,db.execute('SELECT * FROM manual_handoffs WHERE id=?',(session_id,)).fetchone())
        if session['phase']=='preparing':
            db.execute("UPDATE manual_handoffs SET phase='ready',updated_at=?,reason=NULL WHERE id=?",(now,session_id))
            self._audit(db,session_id,'ready',now)
            return self._view(db,db.execute('SELECT * FROM manual_handoffs WHERE id=?',(session_id,)).fetchone())
        finish=bool(session['end_requested']) or any(row['saw_window'] for row in rows)
        stable=all(row['stable_since'] is not None and row['last_at']-row['stable_since']>=5
                   and 0 <= now-row['last_at'] <= 2 for row in rows)
        if finish and stable and mouse_idle:
            self._complete(db,session,rows,now)
        elif finish and stable:
            db.execute('UPDATE manual_handoffs SET updated_at=?,reason=? WHERE id=?',
                       (now,'Waiting for mouse to be idle before handoff settlement',session_id))
        elif session['reason'] and not (str(session['reason']).startswith('Waiting for fresh ') and
                                        any(row['last_at'] is None or now-row['last_at']>2 for row in rows)):
            db.execute('UPDATE manual_handoffs SET updated_at=?,reason=NULL WHERE id=?',(now,session_id))
        return self._view(db,db.execute('SELECT * FROM manual_handoffs WHERE id=?',(session_id,)).fetchone())

    def _complete(self, db, session, rows, now):
        # One SQLite transaction joins durable audit, exclusion gap, current
        # baseline, new-stock flag, and replan notification.  It records no
        # sale: user-operated changes remain deliberately un-attributed.
        for row in rows:
            current=json.loads(row['last_json']); before=json.loads(row['baseline_json'])
            target=row['target_profile_id']; at=current['timestamp']
            db.execute('INSERT INTO events(character,event,payload,timestamp) VALUES(?,?,?,?)',(target,'sales_observation_gap',json.dumps({
                'from':before['timestamp'],'to':at,'reason':'operator_manual_handoff','session_id':session['id'],'sales_receipt':False}),at))
            original=db.execute('SELECT started_at FROM sales_baseline WHERE character=?',(target,)).fetchone()
            baseline={key:current[key] for key in ('identity','timestamp','inventory','booth','silver','request','trade')}
            db.execute('INSERT OR REPLACE INTO sales_baseline VALUES(?,?,?)',(target,json.dumps(baseline),original[0] if original else at))
            before_items={item['uid']:tuple(item[k] for k in ('uid','type_id','plus','gem1','gem2','quantity','bound'))
                          for item in before['inventory']+before['booth']}
            added=any(before_items.get(item['uid']) != tuple(item[k] for k in ('uid','type_id','plus','gem1','gem2','quantity','bound'))
                      for item in current['inventory'])
            if added and row['role']=='Merchant':db.execute('INSERT OR REPLACE INTO state VALUES(?,?,?)',(target,'new_stock','true'))
            db.execute('INSERT INTO manual_replans(session_id,target_profile_id,settled_at,merchant_pending,farmer_pending) VALUES(?,?,?,?,?)',
                       (session['id']+':'+target,target,at,int(row['role']=='Merchant'),int(row['role']=='Farmer')))
            from conquest.merchants.manual_sessions import _ownership_delta
            self._audit(db,session['id'],'settled_target',now,target=target,sales_receipt=False,
                        ownership_delta=_ownership_delta(canonical_ownership(before),canonical_ownership(current)))
        db.execute("UPDATE manual_handoffs SET phase='completed',completed_at=?,updated_at=?,reason=NULL WHERE id=?",(now,now,session['id']))
        self._audit(db,session['id'],'completed',now)
