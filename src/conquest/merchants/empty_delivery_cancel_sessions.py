"""Bind an empty bot-trade cleanup without certifying incomplete old history."""
import json
import time

from conquest.merchants.manual_sessions import (
    BindingMismatch, canonical_ownership, _digest, _json, _fresh, _holdings,
    SETTLEMENT_SECONDS, VisitorKey,
)
from conquest.merchants.delivery_abort_sessions import (
    _audit_chain, _no_extra_holds, _summary, _prefix, check_binding,
)


def _observed(probe, role, snapshot, *, closed_after=None):
    """Historical missing acceptance stays unknown; never provides input proof."""
    original=probe['intent'][role]
    peer=probe['intent']['merchant' if role=='farmer' else 'farmer']
    proof=canonical_ownership(snapshot,require_closed=False)
    if (_holdings(proof)!=canonical_ownership(original)
            or any(snapshot.get(k)!=original.get(k) for k in ('map_id','position'))
            or snapshot.get('request') is not None):
        raise BindingMismatch('Empty-bot cleanup history changed ownership or participant location')
    trade=snapshot.get('trade')
    if trade is None:
        if closed_after is None or snapshot['timestamp']<closed_after:
            raise BindingMismatch('Trade closed before this cleanup submission')
        return proof
    if (trade.get('participant')!=peer['character'] or trade.get('participant_uid')!=peer['character_uid']
            or trade.get('server',snapshot['server'])!=peer['server']
            or trade.get('own_items')!=[] or trade.get('items')!=[]
            or trade.get('accepted') is not False):
        raise BindingMismatch('Empty-bot cleanup history contains offers or approval')
    if snapshot.get('reader_build')=='1078-read-only-candidate':
        if (set(trade)!={'participant','participant_uid','own_items','items','own_silver_text',
                        'other_silver_text','accepted'}
                or trade['own_silver_text']!='0' or trade['other_silver_text']!='0'):
            raise BindingMismatch('Legacy empty trade observation is not the exact known reader shape')
    elif (trade.get('other_accepted') is not False
            or any(type(trade.get(k)) is not int or trade[k]!=0 for k in ('own_silver','other_silver'))):
        raise BindingMismatch('Empty-bot cleanup history contains currency or peer approval')
    return proof


def binding(runtime, probe):
    """Freeze pristine holds and their append-only evidence, without clearing them."""
    store=runtime.manual_sessions;records=[]
    with store.db() as db:
        db.execute('BEGIN');now=time.time();_no_extra_holds(db);_audit_chain(db)
        rows=list(db.execute("SELECT * FROM manual_sessions WHERE phase NOT IN "
            "('completed','request_withdrawn','declined_verified','operator_overridden') ORDER BY id"))
        allowed={probe['farmer_profile_id']:'farmer',probe['target_profile_id']:'merchant'}
        if any(row['target_profile_id'] not in allowed for row in rows):
            raise BindingMismatch('An unrelated manual session blocks empty-bot cleanup')
        for row in rows:
            role=allowed[row['target_profile_id']];peer=probe['intent']['merchant' if role=='farmer' else 'farmer']
            original=canonical_ownership(probe['intent'][role])
            visitor={'target_profile_id':row['target_profile_id'],'visitor_name':peer['character'],
                     'visitor_server':peer['server'],'visitor_uid':peer['character_uid']}
            if (row['phase']!='needs_attention' or row['ever_approved'] or row['current_request_id'] is not None
                    or row['terminal_json'] is not None or row['stable_digest'] is not None
                    or not probe['accepted_at']<=row['created_at']<=now
                    or json.loads(row['visitor_json'])!=visitor
                    or json.loads(row['process_json'])!=original['identity']
                    or json.loads(row['character_json'])!={k:original[k] for k in ('character','character_uid','server')}):
                raise BindingMismatch('Manual hold is not a pristine post-accept empty bot interval')
            for table in ('manual_requests','manual_declines'):
                if db.execute(f'SELECT 1 FROM {table} WHERE session_id=?',(row['id'],)).fetchone():
                    raise BindingMismatch('Manual request or decline history blocks cleanup')
            if store._allowed(db,VisitorKey(**visitor)):
                raise BindingMismatch('Approved visitor permission blocks bot cleanup')
            evidence=[dict(e) for e in db.execute('SELECT * FROM manual_evidence WHERE session_id=? ORDER BY id',(row['id'],))]
            audits=[dict(a) for a in db.execute('SELECT * FROM manual_audit WHERE session_id=? ORDER BY id',(row['id'],))]
            if not evidence or any(a['event'] not in ('session_started','needs_attention','windows_observed','observation_ignored') for a in audits):
                raise BindingMismatch('Manual input or operator history blocks cleanup')
            previous=probe['accepted_at']
            for index,e in enumerate(evidence):
                snapshot=json.loads(e['snapshot_json'])
                if e['digest']!=_digest(snapshot) or not previous<=e['recorded_at']<=now:
                    raise BindingMismatch('Manual evidence chronology or digest changed')
                previous=e['recorded_at']
                if e['error']:
                    expected={'farmer':'Farmer has no attached memory observer',
                              'merchant':'Attached memory reader is unavailable'}[role]
                    if (index==0 or snapshot!={'reader_error':expected}
                            or e['error']!='Request/trade window evidence is unavailable'
                            or e['ownership_digest'] is not None or e['observed_at'] is not None):
                        raise BindingMismatch('Unrecognized manual history gap blocks cleanup')
                    continue
                proof=_observed(probe,role,snapshot)
                _fresh(snapshot,e['recorded_at'])
                if e['observed_at']!=snapshot['timestamp'] or e['ownership_digest']!=_digest(proof):
                    raise BindingMismatch('Historical ownership digest changed')
            records.append({'row':dict(row),'requests':[],'declines':[],'claims':[],
                            'evidence':_summary(evidence),'audit':_summary(audits)})
        holds=sorted((store._view(db,r['row']) for r in records),key=lambda r:r['id'])
    return {'records':records,'digest':_digest(records),'holds':holds}


def refresh_binding(runtime,probe,expected):
    fresh=binding(runtime,probe)
    with runtime.manual_sessions.db() as db:
        _prefix(db,expected,fresh['records'])
    return fresh


def settle(runtime, receipt, farmer, merchant):
    """Same five-second closed ownership rule, with an exact cancel baseline."""
    store=runtime.manual_sessions;probe=receipt['probe'];done=True;now=time.time()
    with store.db() as db:
        db.execute('BEGIN IMMEDIATE');_audit_chain(db);_no_extra_holds(db)
        bound_ids={record['row']['id'] for record in receipt['sessions']['records']}
        active=list(db.execute("SELECT id FROM manual_sessions WHERE phase NOT IN "
            "('completed','request_withdrawn','declined_verified','operator_overridden')"))
        if any(row['id'] not in bound_ids for row in active):
            raise BindingMismatch('An unrelated active manual session blocks cleanup settlement')
        for record in receipt['sessions']['records']:
            row=store._row(db,record['row']['id']);role=('farmer' if row['target_profile_id']==probe['farmer_profile_id'] else 'merchant')
            snapshot=farmer if role=='farmer' else merchant
            if snapshot is None:
                done=False
                continue
            current=_fresh(snapshot,now);expected=canonical_ownership(probe['intent'][role])
            if current!=expected or snapshot['timestamp']<receipt['submitted_at']:
                raise BindingMismatch('Closed cleanup ownership changed during settlement')
            terminal=json.loads(row['terminal_json'] or '{}')
            if row['phase']=='request_withdrawn' and terminal.get('empty_cancel_digest')==receipt['receipt_digest']:
                continue
            ignored={'phase','reason','updated_at','last_observed_at','stable_digest','stable_since','stable_evidence_id'}
            if ({k:v for k,v in dict(row).items() if k not in ignored}
                    !={k:v for k,v in record['row'].items() if k not in ignored}
                    or row['phase'] not in ('needs_attention','settlement_observed')):
                raise BindingMismatch('Bound manual session changed before cleanup settlement')
            # The complete pre-input history is immutable; later observations
            # must also keep exact ownership. No missing old flag is supplied.
            for table,key in (('manual_evidence','evidence'),('manual_audit','audit')):
                prefix=[dict(r) for r in db.execute(f'SELECT * FROM {table} WHERE session_id=? AND id<=? ORDER BY id',
                    (row['id'],record[key]['max_id']))]
                if _summary(prefix)!=record[key]:raise BindingMismatch('Bound cleanup history changed')
            for e in db.execute('SELECT * FROM manual_evidence WHERE session_id=? AND id>? ORDER BY id',
                                (row['id'],record['evidence']['max_id'])):
                prior=json.loads(e['snapshot_json'])
                if e['digest']!=_digest(prior):raise BindingMismatch('Cleanup observation digest changed')
                if e['error']:
                    expected_error={'farmer':'Farmer has no attached memory observer',
                                    'merchant':'Attached memory reader is unavailable'}[role]
                    if (prior!={'reader_error':expected_error}
                            or e['error']!='Request/trade window evidence is unavailable'
                            or e['ownership_digest'] is not None or e['observed_at'] is not None):
                        raise BindingMismatch('Unrecognized cleanup reader gap remains held')
                    # A gap cannot count as a stable interval. Current memory
                    # is still checked; no previous settlement timer survives.
                    if row['stable_since'] is not None and e['recorded_at']>=row['stable_since']:
                        db.execute('UPDATE manual_sessions SET stable_digest=NULL,stable_since=NULL,stable_evidence_id=NULL WHERE id=?',(row['id'],))
                        row=store._row(db,row['id'])
                    continue
                _observed(probe,role,prior,closed_after=receipt['submitted_at'])
            evidence_id=store._evidence(db,row['id'],snapshot,now,current)
            if row['last_observed_at'] is not None and snapshot['timestamp']<=row['last_observed_at']:
                done=False
                store._audit(db,row['id'],'observation_ignored',{'reason':'non_increasing_time','evidence_id':evidence_id},now)
                continue
            digest=_digest(current)
            same=row['stable_digest']==digest and row['stable_since'] is not None
            if same and snapshot['timestamp']-row['stable_since']>=SETTLEMENT_SECONDS:
                result={'phase':'request_withdrawn','empty_cancel_digest':receipt['receipt_digest'],
                        'ownership_digest':digest,'first_evidence_id':row['stable_evidence_id'],
                        'final_evidence_id':evidence_id,'stable_since':row['stable_since'],
                        'settled_at':snapshot['timestamp'],'historical_outcome':'unknown',
                        'sales_receipt':False,'delivery_receipt':False}
                db.execute("UPDATE manual_sessions SET phase='request_withdrawn',terminal_json=?,reason=NULL,last_observed_at=?,updated_at=? WHERE id=?",
                    (_json(result),snapshot['timestamp'],now,row['id']))
                store._audit(db,row['id'],'session_terminal',result,now)
                if store.on_settlement is not None:
                    store.on_settlement(db,store._view(db,store._row(db,row['id'])),snapshot,result)
            else:
                done=False
                db.execute("UPDATE manual_sessions SET phase='settlement_observed',stable_digest=?,stable_since=?,stable_evidence_id=?,last_observed_at=?,updated_at=? WHERE id=?",
                    (digest,row['stable_since'] if same else snapshot['timestamp'],
                     row['stable_evidence_id'] if same else evidence_id,snapshot['timestamp'],now,row['id']))
                store._audit(db,row['id'],'settlement_observed',{'evidence_id':evidence_id,
                    'ownership_digest':digest,'empty_cancel_digest':receipt['receipt_digest']},now)
    runtime._sync_manual_fence()
    return done


def observe(runtime,character,snapshot):
    """Let native manual polls use this receipt's exact closed baseline."""
    from conquest.merchants.empty_delivery_cancel import read,digest
    from conquest.merchants.delivery_probe import read_probe
    receipt=read()
    if not receipt or receipt.get('phase')!='cancel_verified':return None
    target=runtime.manual_target(character)
    matches=[r for r in receipt.get('sessions',{}).get('records',[]) if r['row']['target_profile_id']==target]
    if not matches:return None
    row=runtime.manual_sessions.get(matches[0]['row']['id'])
    if not row['holds_automation']:return None
    active=runtime.manual_sessions.active(target)
    if active is None or active['id']!=row['id']:return None
    current=read_probe()
    if (digest({k:v for k,v in receipt.items() if k!='receipt_digest'})!=receipt.get('receipt_digest')
            or not current or current.get('empty_cancel_digest')!=receipt['receipt_digest']):
        raise BindingMismatch('Empty cleanup settlement receipt changed')
    farmer=target==receipt['probe']['farmer_profile_id']
    settle(runtime,receipt,snapshot if farmer else None,None if farmer else snapshot)
    return runtime.manual_sessions.get(matches[0]['row']['id'])
