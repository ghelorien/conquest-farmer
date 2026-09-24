"""Incident-only sale reconciliation during an untouched empty bot trade.

Ordinary sales observation remains strict. This exceptional proof additionally
requires an explicit operator statement covering the complete incident interval.
It never cancels, approves, offers, confirms, or changes saved controls.
"""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import time


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def _open_pair(intent, pair, now):
    from conquest.merchants.delivery import exact_items, exact_listings, validate_snapshot
    from conquest.merchants.manual_sessions import canonical_ownership
    for role, other in (('farmer','merchant'),('merchant','farmer')):
        old,current=intent[role],pair[role]
        validate_snapshot(current,old['character'],now)
        canonical_ownership(current,require_closed=False)
        if (any(current.get(k)!=old.get(k) for k in
                ('identity','character','character_uid','server','map_id','position','capacity','own_booth_uid','booth_open'))
                or exact_items(current['inventory'])!=exact_items(old['inventory'])
                or current.get('request') is not None):
            raise ValueError('Empty-delivery sale participants or inventory changed')
        trade=current.get('trade')
        if (not isinstance(trade,dict)
                or trade.get('participant')!=intent[other]['character']
                or trade.get('participant_uid')!=intent[other]['character_uid']
                or trade.get('own_items')!=[] or trade.get('items')!=[]
                or trade.get('own_silver')!=0 or trade.get('other_silver')!=0
                or trade.get('accepted') is not False or trade.get('other_accepted') is not False):
            raise ValueError('Both exact bot trade windows must remain empty and unaccepted')
        if role=='farmer' and (current['silver']!=old['silver']
                or exact_listings(current['booth'])!=exact_listings(old['booth'])):
            raise ValueError('Farmer ownership changed during empty delivery')


def preview(source_path, merchant_path, request_id, sale_ids, pairs, *, confirmation, now=None):
    """Validate supplied fresh native pairs and durable journals without writes."""
    from conquest.merchants.delivery import exact_listings
    from conquest.merchants.manual_sessions import canonical_ownership
    from conquest.merchants.sales import net_bounds
    from conquest.settled_delivery_town_recovery import _same_intent
    now=time.time() if now is None else now
    if (not isinstance(confirmation,dict) or confirmation.get('no_manual_changes') is not True
            or not isinstance(confirmation.get('reference'),str) or not confirmation['reference'].strip()
            or not isinstance(confirmation.get('statement'),str) or not confirmation['statement'].strip()
            or confirmation.get('request_id')!=request_id
            or type(confirmation.get('confirmed_at')) not in (int,float)
            or not 0<confirmation['confirmed_at']<=now
            or not isinstance(sale_ids,list) or len(sale_ids)!=2
            or any(type(i) is not int or i<=0 for i in sale_ids) or len(set(sale_ids))!=2
            or not isinstance(pairs,list) or len(pairs)!=2):
        raise ValueError('Exact incident and explicit no-manual-changes statement are required')
    with closing(sqlite3.connect(Path(source_path).resolve().as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row
        found=db.execute('SELECT * FROM transactions WHERE id=?',(request_id,)).fetchone()
        if not found:raise ValueError('Original delivery transaction is unavailable')
        tx=dict(found);intent=json.loads(tx['before_json']);started=tx['created']
        trace=[dict(r) for r in db.execute('SELECT * FROM transaction_steps WHERE transaction_id=? ORDER BY id',(request_id,))]
        for step in trace:step['payload']=json.loads(step['payload'])
        admissions=list(db.execute('SELECT * FROM delivery_admissions WHERE request_id=?',(request_id,)))
        if (tx['kind']!='farmer_delivery' or tx['phase']!='uncertain'
                or len(admissions)!=1 or admissions[0]['phase']!='transaction_started'
                or admissions[0]['character']!=tx['character']
                or intent.get('operation_id')!=request_id or not started<=confirmation['confirmed_at']
                or not any(s['stage']=='action_trace' and s['status']=='initialized' for s in trace)
                or not any(s['stage']=='trade_request' and s['status']=='observed'
                           and s['payload'].get('trade_open') is True for s in trace)
                or any(s['status']=='before_action' and s['stage'] not in
                       ('trade_target_mode','trade_request') for s in trace)
                or any(s['stage'].startswith('offer_item') or 'confirm' in s['stage'] for s in trace)
                or db.execute('SELECT 1 FROM transactions WHERE id!=? AND created<=? AND updated>=? LIMIT 1',
                              (request_id,now,started)).fetchone()):
            raise ValueError('Sale reconciliation requires one untouched uncertain bot trade')
    for pair in pairs:_open_pair(intent,pair,now)
    for role in ('farmer','merchant'):
        if (canonical_ownership(pairs[0][role],require_closed=False)!=canonical_ownership(pairs[1][role],require_closed=False)
                or not pairs[0][role]['timestamp']<pairs[1][role]['timestamp']):
            raise ValueError('Empty delivery ownership is not stable across fresh native reads')
    current=pairs[-1]['merchant'];character=tx['character']
    old=exact_listings(intent['merchant']['booth']);present=exact_listings(current['booth'])
    if any(old.get(uid)!=details for uid,details in present.items()):
        raise ValueError('Current booth contains changed or added listings')
    missing=set(old)-set(present);sale_rows=[];event_rows=[];balance=intent['merchant']['silver']
    with closing(sqlite3.connect(Path(merchant_path).resolve().as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row
        reservations=list(db.execute('SELECT state FROM delivery_reservations WHERE request_id=?',(request_id,)))
        reservation=json.loads(reservations[0]['state']) if len(reservations)==1 else {}
        if (reservation.get('phase') not in ('reserved','offer_ready')
                or not _same_intent(reservation.get('intent') or {},intent)):
            raise ValueError('Exact receiver reservation no longer identifies this delivery')
        targets=(intent['farmer_profile_id'],character)
        for target in targets:
            if (db.execute('SELECT 1 FROM manual_sessions WHERE target_profile_id=? AND created_at<=? AND updated_at>=? LIMIT 1',
                           (target,now,started)).fetchone()
                    or db.execute("SELECT 1 FROM manual_sessions WHERE target_profile_id=? AND phase NOT IN ('completed','request_withdrawn','declined_verified','operator_overridden') LIMIT 1",(target,)).fetchone()
                    or db.execute('SELECT 1 FROM manual_handoffs h JOIN manual_handoff_participants p ON p.session_id=h.id WHERE p.target_profile_id=? AND h.created_at<=? AND COALESCE(h.completed_at,?)>=? LIMIT 1',
                                  (target,now,now,started)).fetchone()
                    or db.execute("SELECT 1 FROM state WHERE character=? AND name='manual_reader_hold' AND value!='null' LIMIT 1",(target,)).fetchone()):
                raise ValueError('A manual ownership interval overlaps the delivery incident')
        if db.execute('SELECT 1 FROM transactions WHERE character=? AND created<=? AND updated>=? LIMIT 1',
                      (character,now,started)).fetchone():
            raise ValueError('Other merchant transaction overlaps sale reconciliation')
        events=[dict(e) for e in db.execute('SELECT * FROM events WHERE character=? AND timestamp>=? AND timestamp<=? ORDER BY timestamp,id',
                                          (character,started,now))]
        allowed={'delivery_reserved','delivery_needs_reconciliation','sale_unconfirmed'}
        for event in events:
            payload=json.loads(event['payload'])
            if event['event'] not in allowed or (event['event']!='sale_unconfirmed' and payload.get('request_id')!=request_id):
                raise ValueError('Observation gap or other merchant work overlaps sale evidence')
        found=[dict(r) for r in db.execute('SELECT * FROM sales WHERE character=? AND observed_at>=? AND observed_at<=? ORDER BY observed_at,id',
                                         (character,started,now))]
        if {r['id'] for r in found}!=set(sale_ids) or len(found)!=2:
            raise ValueError('Incident sale history differs from the exact selected rows')
        all_sold=set()
        for row in found:
            items=json.loads(row['items']);listed=exact_listings(items)
            matches=[e for e in events if e['event']=='sale_unconfirmed' and e['timestamp']==row['observed_at']]
            if len(matches)!=1:raise ValueError('Sale row lacks one exact historical observation event')
            event=matches[0];payload=json.loads(event['payload'])
            lower,upper=net_bounds(items);gain=payload.get('after_silver',-1)-payload.get('before_silver',-1)
            if (row['phase']!='unconfirmed' or row['silver']!=0 or not listed
                    or set(listed)&all_sold or any(old.get(uid)!=value for uid,value in listed.items())
                    or exact_listings(payload.get('items',[]))!=listed
                    or not started<=payload.get('from',0)<row['observed_at']<=confirmation['confirmed_at']<=current['timestamp']
                    or not 0<row['observed_at']-payload['from']<=5
                    or payload.get('before_silver')!=balance or not lower<=gain<=upper
                    or payload.get('net_bounds')!=[lower,upper]
                    or payload.get('gross')!=sum(i['price'] for i in items)
                    or payload.get('foreign_request_observed') is not False):
                raise ValueError('Sale event does not prove the exact adjacent booth and silver delta')
            balance=payload['after_silver'];all_sold.update(listed)
            sale_rows.append(dict(row,verified_silver=gain));event_rows.append(event)
        if all_sold!=missing or balance!=current['silver']:
            raise ValueError('Selected sales do not exactly explain current booth and silver')
    proof={'kind':'empty_delivery_sales_reconciliation','request_id':request_id,'character':character,
           'sale_ids':sorted(sale_ids),'confirmation':confirmation,'source':tx,'source_trace':trace,
           'sales_before':sale_rows,'events':event_rows,'native_pairs':pairs,'observed_at':now,
           'ownership_only':True,'delivery_receipt':False}
    proof['proof_digest']=digest(proof)
    return proof


def promote(ui, request_id, sale_ids, *, confirmation):
    """Explicit operator action; atomically promote only fully reviewed rows."""
    from conquest.merchants import delivery_operation
    from conquest.merchants.delivery_bridge import pair
    journal=ui.runtime.journal;name='empty_delivery_sales:'+request_id
    # Returning a durable result is idempotent; it does not reobserve a later
    # closed trade as if it were the original open incident.
    with journal.db() as db:
        old=db.execute('SELECT value FROM state WHERE name=?',(name,)).fetchall()
        if old:
            if len(old)!=1:raise ValueError('Ambiguous incident sale audit')
            saved=json.loads(old[0][0])
            if saved['sale_ids']!=sorted(sale_ids) or saved['confirmation']!=confirmation:
                raise ValueError('Incident sale confirmation cannot be replaced')
            for sale in saved['sales_before']:
                row=db.execute('SELECT phase,silver FROM sales WHERE id=?',(sale['id'],)).fetchone()
                if not row or tuple(row)!=('verified',sale['verified_silver']):
                    raise ValueError('Promoted sale no longer matches its audit')
            return saved
    with ui.coordinator.lock:
        def safe():
            control=ui.app.control.snapshot()
            if (ui.closed or ui.app.closing or control['enabled'] or control.get('paused')
                    or ui.coordinator.stopped or ui.coordinator.owner is not None
                    or ui.coordinator.manual_active() or ui.runtime.manual_handoff_status() is not None
                    or any(w.is_alive() for w in getattr(ui,'delivery_workers',{}).values())):
                raise ValueError('Sale reconciliation requires idle stopped exclusive ownership')
            return control['revision']
        revision=safe()
        with closing(sqlite3.connect(Path(delivery_operation.JOURNAL).resolve().as_uri()+'?mode=ro',uri=True)) as db:
            row=db.execute('SELECT before_json FROM transactions WHERE id=?',(request_id,)).fetchone()
        if not row:raise ValueError('Original delivery is unavailable')
        character=json.loads(row[0])['merchant']['character']
        observations=[]
        for _ in range(2):
            farmer,merchant=pair(ui,character)
            observations.append({'farmer':farmer,'merchant':merchant})
            if safe()!=revision:raise ValueError('Control changed during sale observation')
        evidence=preview(delivery_operation.JOURNAL,journal.path,request_id,sale_ids,observations,
                         confirmation=confirmation)
        if safe()!=revision:raise ValueError('Control changed before sale publication')
        with journal.db() as db:
            db.execute('BEGIN IMMEDIATE')
            # Recheck all journal evidence under the writer reservation. This
            # still performs no input and does not alter sales baselines.
            repeated=preview(delivery_operation.JOURNAL,journal.path,request_id,sale_ids,observations,
                             confirmation=confirmation,now=evidence['observed_at'])
            if repeated!=evidence:raise ValueError('Incident evidence changed before sale publication')
            for row in evidence['sales_before']:
                changed=db.execute("UPDATE sales SET phase='verified',silver=?,note=? WHERE id=? AND character=? AND phase='unconfirmed' AND silver=0 AND items=? AND observed_at=?",
                    (row['verified_silver'],'Incident-bound empty delivery reconciliation: '+evidence['proof_digest'],
                     row['id'],row['character'],row['items'],row['observed_at'])).rowcount
                if changed!=1:raise ValueError('Sale row changed before atomic promotion')
            db.execute('INSERT INTO state(character,name,value) VALUES(?,?,?)',
                       (evidence['character'],name,json.dumps(evidence,sort_keys=True)))
            db.execute('INSERT INTO events(character,event,payload,timestamp) VALUES(?,?,?,?)',
                (evidence['character'],'empty_delivery_sales_reconciled',json.dumps({
                    'request_id':request_id,'sale_ids':evidence['sale_ids'],
                    'proof_digest':evidence['proof_digest'],'operator_reference':confirmation['reference']}),time.time()))
        return evidence
