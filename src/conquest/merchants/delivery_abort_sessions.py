"""Exact, input-free session bindings for the operator's probe-abort workflow."""
import json
import hashlib
from copy import deepcopy
from conquest.merchants.delivery import exact_items

from conquest.merchants.manual_sessions import (
    BindingMismatch, TERMINAL_PHASES, _digest, _json, canonical_ownership,
    request_fingerprint, _fresh, _now, _number,
)


def _summary(records):
    stream=hashlib.sha256()
    for record in records:
        encoded=_json(record).encode('utf-8')
        stream.update(len(encoded).to_bytes(8,'big'));stream.update(encoded)
    return dict(count=len(records),max_id=records[-1]['id'] if records else None,
                last_digest=records[-1]['digest'] if records else None,stream_sha256=stream.hexdigest())


def _highwater(db, table, session_id):
    row=db.execute(f'SELECT COUNT(*),MAX(id) FROM {table} WHERE session_id=?',(session_id,)).fetchone()
    last=db.execute(f'SELECT digest FROM {table} WHERE id=?',(row[1],)).fetchone()
    return dict(count=row[0],max_id=row[1],last_digest=last[0] if last else None)


def _no_extra_holds(db):
    if (db.execute("SELECT 1 FROM manual_rebaseline WHERE phase!='completed'").fetchone()
            or db.execute("SELECT 1 FROM state WHERE name='manual_reader_hold' AND value!='null'").fetchone()):
        raise BindingMismatch('Reader/rebaseline holds cannot use the abort exception')


def _audit_chain(db):
    previous=''
    for record in db.execute('SELECT * FROM manual_audit ORDER BY id'):
        expected=_digest(dict(session_id=record['session_id'],event=record['event'],at=record['at'],
                              payload=json.loads(record['payload_json']),previous_digest=previous))
        if record['previous_digest']!=previous or record['digest']!=expected:
            raise BindingMismatch('Manual audit chain integrity failed')
        previous=record['digest']


def _snapshot_time(db, clock=None):
    """Pin one SQLite snapshot before sampling its chronology authority.

    BEGIN alone is deferred: the first read must complete before choosing the
    clock value. A caller-held BEGIN IMMEDIATE already excludes new writes,
    but performs the same harmless read for one unambiguous API contract.
    """
    if not db.in_transaction:db.execute('BEGIN')
    db.execute('SELECT id FROM manual_sessions ORDER BY id LIMIT 1').fetchone()
    return _now(clock() if clock is not None else None)


def capture(store, db, probe, *, clock=None, closed_after=None):
    """Bind both false admissions and their complete immutable history.

    This does not certify their historical outcome. Later reader errors are
    retained; only the original exact admission and the current abort pair can
    qualify the narrowly scoped close operation.
    """
    from conquest.merchants.delivery_probe_ownership import historical_local_trade
    now=_snapshot_time(db,clock)
    source, target = probe['farmer_profile_id'], probe['target_profile_id']
    accepted = probe['accepted_at']
    _no_extra_holds(db)
    _audit_chain(db)
    records = []
    rows = list(db.execute("SELECT * FROM manual_sessions WHERE phase NOT IN "
                          "('completed','request_withdrawn','declined_verified','operator_overridden') ORDER BY id"))
    if len(rows) != 2 or {row['target_profile_id'] for row in rows} != {source, target}:
        raise BindingMismatch('Abort requires exactly the two named unapproved probe holds')
    for row in rows:
        role = 'farmer' if row['target_profile_id'] == source else 'merchant'
        peer = probe['intent']['merchant' if role == 'farmer' else 'farmer']
        original = canonical_ownership(probe['intent'][role])
        visitor = dict(target_profile_id=row['target_profile_id'], visitor_name=peer['character'],
                       visitor_server=peer['server'], visitor_uid=peer['character_uid'])
        if (row['phase'] != 'needs_attention' or row['ever_approved']
                or not accepted <= row['created_at'] <= now
                or row['terminal_json'] is not None or row['stable_digest'] is not None
                or json.loads(row['visitor_json']) != visitor
                or json.loads(row['process_json']) != original['identity']
                or json.loads(row['character_json']) != {k: original[k] for k in ('character','character_uid','server')}):
            raise BindingMismatch('Abort may not bypass a genuine, approved or changed manual interval')
        requests = [dict(r) for r in db.execute('SELECT * FROM manual_requests WHERE session_id=? ORDER BY id', (row['id'],))]
        declines = [dict(r) for r in db.execute('SELECT * FROM manual_declines WHERE session_id=?', (row['id'],))]
        claims = [dict(r) for r in db.execute('SELECT c.* FROM manual_decline_claims c JOIN manual_requests r ON r.id=c.request_id WHERE r.session_id=?', (row['id'],))]
        audits = [dict(r) for r in db.execute('SELECT * FROM manual_audit WHERE session_id=? ORDER BY id', (row['id'],))]
        evidence = [dict(r) for r in db.execute('SELECT * FROM manual_evidence WHERE session_id=? ORDER BY id', (row['id'],))]
        if (declines or claims or not evidence or not audits
                or any(a['event'] not in ('session_started','approval_pending','needs_attention',
                                         'windows_observed','observation_ignored') for a in audits)):
            raise BindingMismatch('Abort cannot bypass approval, decline, input or operator history')
        if db.execute('SELECT 1 FROM visitor_permissions WHERE target_profile_id=? AND visitor_name=? AND visitor_server=? AND visitor_uid=? AND allowed=1',
                      (row['target_profile_id'],peer['character'],peer['server'],peer['character_uid'])).fetchone():
            raise BindingMismatch('An allowlisted manual visitor cannot use probe-abort reclassification')
        if requests:
            if (role != 'merchant' or len(requests) != 1 or requests[0]['state'] != 'pending'
                    or requests[0]['id'] != row['current_request_id']):
                raise BindingMismatch('Abort requires a pristine merchant request, never a farmer request')
        elif row['current_request_id'] is not None:
            raise BindingMismatch('Abort request binding is missing')
        first = evidence[0]
        snapshot = json.loads(first['snapshot_json'])
        proof = historical_local_trade(probe, probe['character'], target, source, role, snapshot)
        if (first['error'] or first['digest'] != _digest(snapshot)
                or first['ownership_digest'] != _digest(proof) or first['recorded_at'] < accepted):
            raise BindingMismatch('Original bot admission evidence is not exact')
        if requests:
            request = requests[0]; binding = json.loads(request['binding_json'])
            if (snapshot.get('request') is None or request['before_json'] != _json(proof)
                    or binding.get('evidence_digest') != first['digest']
                    or binding.get('ownership_digest') != _digest(proof)
                    or binding.get('session_id') != row['id'] or binding.get('request_id') != request['id']
                    or binding.get('target_profile_id') != row['target_profile_id']
                    or binding.get('visitor') != visitor or binding.get('game_process_identity') != original['identity']
                    or binding.get('character_identity') != json.loads(row['character_json'])
                    or binding.get('request_fingerprint') != request['fingerprint']
                    or request['fingerprint'] != request_fingerprint(snapshot)):
                raise BindingMismatch('Original pending request differs from the supervised probe')
        elif snapshot.get('trade') is None:
            raise BindingMismatch('Original admission was not the exact bot trade')
        elif role == 'farmer' and row['created_at'] < probe['updated_at']:
            raise BindingMismatch('Farmer hold predates the exact durable offer boundary')
        # All subsequent evidence is preserved, including explicit reader
        # failures. It is not substituted for the fresh bilateral abort proof.
        if any(record['digest'] != _digest(json.loads(record['snapshot_json'])) for record in evidence):
            raise BindingMismatch('Manual evidence history changed')
        for record in evidence:
            observed=json.loads(record['snapshot_json'])
            if not row['created_at']<=record['recorded_at']<=now:
                raise BindingMismatch('Manual evidence record chronology changed')
            if record['error'] is None:
                if record['ownership_digest']!=_digest(_fresh(observed,record['recorded_at'])):
                    raise BindingMismatch('Manual ownership evidence digest differs')
            elif record['ownership_digest'] is not None:
                raise BindingMismatch('Reader-error evidence cannot claim ownership authority')
            if not isinstance(observed,dict):continue
            if any(key in observed and observed[key]!=original[key] for key in ('identity','character','character_uid','server')):
                raise BindingMismatch('Manual history contains a changed process or character')
            if (observed.get('request') is None and observed.get('trade') is None
                    and record['error'] is None):
                if closed_after is None or observed.get('timestamp',0)<closed_after:
                    raise BindingMismatch('Unexpected closed-window history predates durable abort submission')
                current=canonical_ownership(observed)
                expected=deepcopy(original)
                if role=='merchant':expected.update(booth=current['booth'],silver=current['silver'])
                if current!=expected:raise BindingMismatch('Post-abort observation changed unrelated ownership')
            for field in ('request','trade'):
                modal=observed.get(field)
                if isinstance(modal,dict):
                    if (modal.get('participant')!=peer['character']
                            or type(modal.get('participant_uid')) is not int or modal['participant_uid']!=peer['character_uid']
                            or modal.get('server',observed.get('server'))!=peer['server']):
                        raise BindingMismatch('Manual history contains an incomplete or different participant')
                    if field=='request' and (role!='merchant' or not requests
                            or request_fingerprint(observed)!=requests[0]['fingerprint']):
                        raise BindingMismatch('Manual history contains a different request')
                if field=='trade' and isinstance(modal,dict):
                    if (any(modal.get(key) is not False for key in ('accepted','other_accepted'))
                            or any(type(modal.get(key)) is not int or modal[key]!=0 for key in ('own_silver','other_silver'))
                            or modal.get('items' if role=='farmer' else 'own_items')):
                        raise BindingMismatch('Manual history contains approval, currency or reverse-offer input')
                    offered=exact_items(modal.get('own_items' if role=='farmer' else 'items',[]))
                    selected=exact_items(probe['intent']['items'])
                    if any(selected.get(uid)!=details for uid,details in offered.items()):
                        raise BindingMismatch('Manual history contains a different item offer')
        records.append(dict(row=dict(row), requests=requests, declines=declines, claims=claims,
                            evidence=_summary(evidence), audit=_summary(audits)))
    return records


def binding(runtime, probe, *, clock=None, closed_after=None):
    store = runtime.manual_sessions
    with store.db() as db:
        records = capture(store, db, probe, clock=clock,closed_after=closed_after)
        # Derive presentation bindings from the same snapshot, not a new
        # connection that may already include an observational suffix.
        views=[store._view(db,record['row']) for record in records]
    return {'records': records, 'digest': _digest(records),
            'holds': sorted(views, key=lambda row: row['id'])}


def _prefix(db, expected, records):
    """The operator confirms an immutable prefix, never unknown new authority."""
    if len(records)!=len(expected['records']):raise BindingMismatch('Named abort sessions changed')
    for before,after in zip(expected['records'],records):
        ignored={'updated_at','last_observed_at','reason'}
        if ({k:v for k,v in before['row'].items() if k not in ignored}
                !={k:v for k,v in after['row'].items() if k not in ignored}
                or before['requests']!=after['requests'] or before['declines']!=after['declines']
                or before['claims']!=after['claims']):
            raise BindingMismatch('Session authority changed after operator preview')
        for table,field in (('manual_evidence','evidence'),('manual_audit','audit')):
            prefix=[dict(row) for row in db.execute(f'SELECT * FROM {table} WHERE session_id=? AND id<=? ORDER BY id',
                                                   (before['row']['id'],before[field]['max_id']))]
            if _summary(prefix)!=before[field]:raise BindingMismatch('Confirmed immutable history prefix changed')


def refresh_binding(runtime, probe, expected, *, clock=None, closed_after=None):
    # Full semantic validation covers every observational suffix; then prefix
    # equality retains exactly what the operator saw. The resulting H1 binding
    # is frozen under coordinator ownership for all hot input checks.
    with runtime.manual_sessions.db() as db:
        records=capture(runtime.manual_sessions,db,probe,clock=clock,closed_after=closed_after)
        _prefix(db,expected,records)
        views=sorted((runtime.manual_sessions._view(db,record['row']) for record in records),key=lambda row:row['id'])
    return dict(records=records,digest=_digest(records),holds=views)


def check_binding(runtime, expected):
    """Hot-path equality: immutable-table high waters, never full snapshots.

    Caller holds the coordinator mutex. Append-only triggers prohibit updates
    and deletes; unchanged count/max/digest therefore proves no history append
    since the full streaming validation at preparation.
    """
    with runtime.manual_sessions.db() as db:
        _no_extra_holds(db)
        rows=[dict(row) for row in db.execute("SELECT * FROM manual_sessions WHERE phase NOT IN "
              "('completed','request_withdrawn','declined_verified','operator_overridden') ORDER BY id")]
        if rows!=[record['row'] for record in expected['records']]:
            raise BindingMismatch('Abort session rows changed')
        for record in expected['records']:
            row=record['row'];key=row['id'];visitor=json.loads(row['visitor_json'])
            requests=[dict(r) for r in db.execute('SELECT * FROM manual_requests WHERE session_id=? ORDER BY id',(key,))]
            if requests!=record['requests']:raise BindingMismatch('Abort request state changed')
            for table,field in (('manual_evidence','evidence'),('manual_audit','audit')):
                if _highwater(db,table,key)!={k:v for k,v in record[field].items() if k!='stream_sha256'}:
                    raise BindingMismatch('Abort session history was appended')
            if (db.execute('SELECT 1 FROM manual_declines WHERE session_id=?',(key,)).fetchone()
                    or db.execute('SELECT 1 FROM manual_decline_claims c JOIN manual_requests r ON r.id=c.request_id WHERE r.session_id=?',(key,)).fetchone()
                    or db.execute('SELECT 1 FROM visitor_permissions WHERE target_profile_id=? AND visitor_name=? AND visitor_server=? AND visitor_uid=? AND allowed=1',
                                  tuple(visitor[k] for k in ('target_profile_id','visitor_name','visitor_server','visitor_uid'))).fetchone()):
                raise BindingMismatch('Abort permission/decline authority changed')
    if sorted(runtime._manual_rows(),key=lambda row:row['id'])!=expected['holds']:
        raise BindingMismatch('Abort runtime holds changed')
    return expected


def disposition(runtime, probe, receipt, expected, *, confirmation_reference, operator, recheck, clock=None):
    """One transaction: install process-bound rebaseline, then override both."""
    store = runtime.manual_sessions
    with store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        now=_snapshot_time(db,clock)
        if (receipt.get('phase')!='cancel_verified'
                or not _number(receipt.get('prepared_at'),'Abort preparation time')
                       <=_number(receipt.get('submitted_at'),'Abort submission time')
                       <=_number(receipt.get('verified_at'),'Abort verification time')<=now):
            raise BindingMismatch('A verified chronological abort receipt is required')
        recheck()  # Includes exact sidecar/main restoration proof in the bridge.
        already=[]
        for record in expected['records']:
            row=store._row(db,record['row']['id'])
            if row['phase'] not in TERMINAL_PHASES:continue
            terminal=json.loads(row['terminal_json'] or '{}')
            baseline=db.execute('SELECT * FROM manual_rebaseline WHERE source_session_id=?',(row['id'],)).fetchone()
            if (row['phase']!='operator_overridden' or terminal.get('confirmation_reference')!=confirmation_reference
                    or terminal.get('operator')!=operator or not terminal.get('prepared_history_digest')
                    or terminal.get('session_history_digest')!=expected['digest']
                    or terminal.get('abort_receipt_digest')!=_digest(receipt) or baseline is None
                    or baseline['target_profile_id']!=row['target_profile_id']):
                raise BindingMismatch('A different terminal disposition already exists')
            already.append(row['id'])
        if already:
            if len(already)!=len(expected['records']):raise BindingMismatch('Partial terminal disposition is not retryable')
            recheck()
            return already
        # Verification may be delayed by Stop/read failure. Once its exact
        # receipt is revalidated, observations since durable submission are
        # eligible; pre-submission closed history is never excused.
        records = capture(store, db, probe, clock=lambda:now,closed_after=receipt['submitted_at'])
        _prefix(db,expected,records)
        recheck()
        results = []
        for record in records:
            row = record['row']; role = 'farmer' if row['target_profile_id'] == probe['farmer_profile_id'] else 'merchant'
            current = canonical_ownership(receipt['after'][role])
            # Pin process identity now, not on a later first poll after restart.
            db.execute("INSERT INTO manual_rebaseline(id,target_profile_id,source_session_id,phase,created_at,updated_at,process_json,character_json,reason) VALUES(?,?,?,'settlement_observed',?,?,?,?,?)",
                       ('rebaseline:'+row['id'], row['target_profile_id'], row['id'], row['created_at'], now,
                        json.dumps(current['identity'],sort_keys=True),
                        json.dumps({k:current[k] for k in ('character','character_uid','server')},sort_keys=True),
                        'Two fresh stable closed-window observations required after probe abort'))
        for record in records:
            row = record['row']
            terminal = dict(confirmation_reference=confirmation_reference, operator=operator,
                original_phase=row['phase'], at=now, historical_outcome='unknown', sales_receipt=False,
                delivery_receipt=False, transfer_confirm_input=False, abort_receipt_digest=_digest(receipt),
                session_history_digest=expected['digest'], reason='Operator disposition after verified probe abort')
            terminal['prepared_history_digest']=_digest(records)
            db.execute("UPDATE manual_sessions SET phase='operator_overridden',terminal_json=?,updated_at=? WHERE id=?",
                       (_json(terminal),now,row['id']))
            store._audit(db,row['id'],'operator_overridden',terminal,now)
            results.append(row['id'])
        recheck()
        return results
