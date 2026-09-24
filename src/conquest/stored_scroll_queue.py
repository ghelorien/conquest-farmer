"""Durable delivery intent from completed native Market storage receipts.

This journal grants no withdrawal authority. Fresh native warehouse and exact
item ownership checks remain in the existing protected scroll operation.
"""
from contextlib import contextmanager
import hashlib
import json
import math
from pathlib import Path
import sqlite3


def path(journal):
    return Path(journal).with_name('stored-meteor-scrolls.sqlite3')


@contextmanager
def database(journal):
    destination=path(journal);destination.parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(destination,timeout=5);db.row_factory=sqlite3.Row
    try:
        db.execute('PRAGMA synchronous=FULL')
        db.execute('CREATE TABLE IF NOT EXISTS scrolls(uid INTEGER PRIMARY KEY, stored_at REAL NOT NULL, '
                   'evidence TEXT NOT NULL, digest TEXT NOT NULL, phase TEXT NOT NULL, '
                   'deferred_at REAL, disposition TEXT)')
        with db:yield db
    finally:db.close()


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def explicit_disposition(state,uid):
    for key in ('user_confirmed_scroll_consumption','user_confirmed_scroll_transfer'):
        record=state.get(key)
        if not record:continue
        if (not isinstance(record,dict) or record.get('uid')!=uid or record.get('confirmed') is not True
                or record.get('source')!='explicit user confirmation'
                or key.endswith('transfer') and (record.get('type_id')!=720027
                    or record.get('destination')!='another_character')):
            raise ValueError('Stored scroll explicit disposition is not exact')
        return {'kind':key,'record':record}
    return None


def capture(journal,state):
    uid=state.get('scroll_uid');stamp=state.get('market_verified_at')
    if (state.get('phase')!='completed' or state.get('exchange_verified') is not True
            or type(uid) is not int or uid<=0 or type(stamp) not in (int,float)
            or not math.isfinite(stamp) or stamp<=0):return False
    receipts=[r for r in state.get('receipts',[]) if r.get('type_id')==720027
        and r.get('verified_in_warehouse') is True and r.get('stored',r.get('uid'))==uid]
    if len(receipts)!=1:return False
    disposition=explicit_disposition(state,uid)
    # Cooldown and explicit terminal annotations may be added later; the
    # completed operation and all ownership receipts themselves are immutable.
    evidence={k:v for k,v in state.items() if k not in (
        'delivery_deferred','user_confirmed_scroll_consumption','user_confirmed_scroll_transfer')}
    encoded=json.dumps(evidence,sort_keys=True);checksum=digest(evidence)
    deferred=state.get('delivery_deferred') or {}
    deferred_at=deferred.get('at') if deferred.get('uid')==uid else None
    if deferred_at is not None and (type(deferred_at) not in (int,float) or not math.isfinite(deferred_at)):
        raise ValueError('Stored scroll cooldown is invalid')
    with database(journal) as db:
        db.execute('BEGIN IMMEDIATE')
        old=db.execute('SELECT * FROM scrolls WHERE uid=?',(uid,)).fetchone()
        if old and old['digest']!=checksum:raise ValueError('Stored scroll provenance changed')
        if not old:
            db.execute('INSERT INTO scrolls VALUES(?,?,?,?,?,?,?)',
                (uid,stamp,encoded,checksum,'pending',deferred_at,None))
        if disposition:
            db.execute("UPDATE scrolls SET phase='disposed',disposition=? WHERE uid=? AND phase='pending'",
                       (json.dumps(disposition,sort_keys=True),uid))
    return True


def pending(journal):
    if not path(journal).exists():return []
    with database(journal) as db:
        rows=[dict(r) for r in db.execute("SELECT * FROM scrolls WHERE phase='pending' ORDER BY stored_at,uid")]
    for row in rows:
        row['evidence']=json.loads(row['evidence'])
        if (digest(row['evidence'])!=row['digest'] or row['uid']!=row['evidence'].get('scroll_uid')
                or row['stored_at']!=row['evidence'].get('market_verified_at')):
            raise ValueError('Stored scroll queue evidence changed')
    return rows


def complete(journal,uid,receipt):
    with database(journal) as db:
        db.execute("UPDATE scrolls SET phase='delivered',disposition=? WHERE uid=? AND phase='pending'",
                   (json.dumps(receipt,sort_keys=True),uid))


def defer(journal,uid,now):
    with database(journal) as db:
        row=db.execute('SELECT phase FROM scrolls WHERE uid=?',(uid,)).fetchone()
        if not row or row['phase']!='pending':raise ValueError('Deferred scroll has no outstanding storage receipt')
        db.execute('UPDATE scrolls SET deferred_at=? WHERE uid=?',(now,uid))
