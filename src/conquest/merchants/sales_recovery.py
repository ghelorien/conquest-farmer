"""Explicit, audited recovery of legacy receipts from saved memory balance anchors.

This is a repair tool, not the continuous detector. Recovered batches retain their
aggregate observed proceeds; fractional silver is never invented per item.
"""
import hashlib
import json
from conquest.merchants.journal import character_name
from conquest.merchants.sales import net_bounds


def reconcile(journal, character, before, after, *, sources, apply=False):
    character = character_name(character)
    start, end = before['timestamp'], after['timestamp']
    if (start >= end or before['identity'] != after['identity']
            or any(s.get('character',character) != character for s in (before,after))
            or any(s.get('trade') or s.get('request') for s in (before,after))):
        raise ValueError('Invalid historical observation anchors')
    with journal.db() as db:
        db.execute('BEGIN IMMEDIATE')
        rows = list(db.execute('SELECT * FROM sales WHERE character=? AND observed_at>? AND observed_at<=? ORDER BY id', (character,start,end)))
        if not rows:
            raise ValueError('No recorded stock departures in this interval')
        key = hashlib.sha256(json.dumps([character,[r['id'] for r in rows]]).encode()).hexdigest()
        existing = db.execute('SELECT evidence FROM sales_reconciliations WHERE id=?',(key,)).fetchone()
        if existing:
            evidence=json.loads(existing[0])
            if evidence['before'] != before or evidence['after'] != after:
                raise ValueError('Receipt already reconciled with different evidence')
            return dict(evidence, already_reconciled=True)
        if any(r['phase'] != 'unconfirmed' for r in rows):
            raise ValueError('Receipt is already accounted for or still pending')
        baseline = db.execute('SELECT started_at FROM sales_baseline WHERE character=?',(character,)).fetchone()
        if not baseline or any(r['observed_at'] < baseline[0] for r in rows):
            raise ValueError('Departures predate tracking')
        tx = list(db.execute('SELECT * FROM transactions WHERE character=? AND created<=? AND updated>=?',(character,end,start)))
        if any(t['kind']!='listing' or t['phase'] not in ('verified','aborted','operator_overridden') for t in tx):
            raise ValueError('Trade or unresolved transaction overlaps this interval')
        listed = {(i['uid'],i['price']) for i in before['booth']}
        # An anchor may have been captured immediately before a verified refill.
        for t in tx:
            if t['phase']=='verified':
                intent=json.loads(t['before_json'])
                listed.add((intent['uid'],intent['price']))
        items = [i for r in rows for i in json.loads(r['items'])]
        present = {i['uid'] for i in after['booth']+after['inventory']}
        if (len({i['uid'] for i in items}) != len(items)
                or any((i['uid'],i['price']) not in listed or i['uid'] in present for i in items)):
            raise ValueError('Departures do not match known listings and final stock')
        gain = after['silver']-before['silver']
        low,high = net_bounds(items)
        if not low <= gain <= high:
            raise ValueError('Balance gain does not reconcile with net listing proceeds')
        evidence = dict(character=character,sale_ids=[r['id'] for r in rows],
            before=before,after=after,sources=sources,net_bounds=[low,high],silver=gain,
            gross=sum(i['price'] for i in items),items=sum(i['quantity'] for i in items))
        if apply:
            db.execute('INSERT INTO sales_reconciliations VALUES(?,?,?,?,?,?,?)',
                (key,character,min(r['observed_at'] for r in rows),max(r['observed_at'] for r in rows),
                 gain,evidence['items'],json.dumps(evidence)))
            for r in rows:
                db.execute("UPDATE sales SET phase='reconciled',note=? WHERE id=?",('Net proceeds in historical reconciliation '+key,r['id']))
            db.execute('INSERT INTO events(character,event,payload,timestamp) VALUES(?,?,?,?)',
                (character,'sales_history_reconciled',json.dumps({'receipt':key,'silver':gain,'sale_ids':evidence['sale_ids']}),end))
        return evidence
