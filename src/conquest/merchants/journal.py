"""Durable merchant state, transaction receipts and a notification outbox."""
from conquest.character_context import (state_path, MerchantNames, resolve_merchant,
    ProfileName, database_character)
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import time

CHARACTERS = MerchantNames()
sqlite3.register_adapter(ProfileName, lambda name: name.profile_id)

TERMINAL_PHASES = ('verified', 'aborted')
DELIVERY_ITEM_FIELDS = ('uid','type_id','plus','gem1','gem2','quantity','bound')
LEGAL_TRANSITIONS = {
    'prepared': frozenset(('submitted', 'uncertain', 'verified', 'aborted')),
    'submitted': frozenset(('uncertain', 'verified')),
    'uncertain': frozenset(('verified', 'aborted')),
    'verified': frozenset(('verified',)),
    'aborted': frozenset(('aborted',)),
}


def canonical_delivery_items(items):
    """Strip observational fields while retaining every ownership attribute."""
    from conquest.merchants.delivery import exact_items
    exact_items(items)
    return sorted(({name:item.get(name) for name in DELIVERY_ITEM_FIELDS} for item in items),
                  key=lambda item:item['uid'])


def profile_row(cursor, values):
    values = tuple(database_character(value) if column[0]=='character' else value
                   for column,value in zip(cursor.description,values))
    return sqlite3.Row(cursor,values)


def character_name(value):
    return resolve_merchant(value)


class Journal:
    def __init__(self, path=state_path('reports/merchants/journal.sqlite3')):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS state(character TEXT, name TEXT, value TEXT NOT NULL,
                    PRIMARY KEY(character,name));
                CREATE TABLE IF NOT EXISTS transactions(id TEXT PRIMARY KEY, character TEXT NOT NULL,
                    kind TEXT NOT NULL, phase TEXT NOT NULL, before_json TEXT NOT NULL,
                    result_json TEXT, created REAL NOT NULL, updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS transaction_steps(id INTEGER PRIMARY KEY,
                    transaction_id TEXT NOT NULL, stage TEXT NOT NULL, status TEXT NOT NULL,
                    payload TEXT NOT NULL, timestamp REAL NOT NULL,
                    FOREIGN KEY(transaction_id) REFERENCES transactions(id));
                CREATE INDEX IF NOT EXISTS transaction_steps_by_transaction
                    ON transaction_steps(transaction_id,id);
                CREATE TABLE IF NOT EXISTS delivery_admissions(request_id TEXT PRIMARY KEY,
                    character TEXT NOT NULL, uids_json TEXT NOT NULL, items_json TEXT,
                    origin_json TEXT NOT NULL, phase TEXT NOT NULL, reason TEXT,
                    created REAL NOT NULL, updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, character TEXT NOT NULL,
                    event TEXT NOT NULL, payload TEXT NOT NULL, timestamp REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS scan_requests(character TEXT, request_id TEXT, state TEXT NOT NULL,
                    PRIMARY KEY(character,request_id));
                CREATE TABLE IF NOT EXISTS delivery_reservations(character TEXT, request_id TEXT, state TEXT NOT NULL,
                    PRIMARY KEY(character,request_id));
                CREATE TABLE IF NOT EXISTS sales_baseline(character TEXT PRIMARY KEY, snapshot TEXT NOT NULL,
                    started_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS sales(id INTEGER PRIMARY KEY, character TEXT NOT NULL,
                    observed_at REAL NOT NULL, phase TEXT NOT NULL, items TEXT NOT NULL,
                    silver INTEGER NOT NULL, note TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sales_reports(id TEXT PRIMARY KEY, created REAL NOT NULL,
                    phase TEXT NOT NULL, content TEXT NOT NULL, message_id TEXT);
                CREATE TABLE IF NOT EXISTS sales_reconciliations(id TEXT PRIMARY KEY, character TEXT NOT NULL,
                    first_sale_at REAL NOT NULL, until_at REAL NOT NULL, silver INTEGER NOT NULL,
                    items INTEGER NOT NULL, evidence TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sales_schedule(id INTEGER PRIMARY KEY, next_due REAL NOT NULL,
                    retry_at REAL NOT NULL, status TEXT NOT NULL);
            ''')

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = profile_row
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('PRAGMA synchronous=FULL')
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, character, name, default=None):
        character = character_name(character)
        with self.db() as db:
            row = db.execute('SELECT value FROM state WHERE character=? AND name=?', (character, name)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, character, name, value):
        character = character_name(character)
        with self.db() as db:
            db.execute('INSERT OR REPLACE INTO state VALUES(?,?,?)', (character, name, json.dumps(value)))

    def event(self, character, event, **payload):
        character = character_name(character)
        with self.db() as db:
            db.execute('INSERT INTO events(character,event,payload,timestamp) VALUES(?,?,?,?)',
                (character, event, json.dumps(payload), time.time()))

    def begin(self, key, character, kind, before, *, admission=False):
        character = character_name(character)
        encoded = json.dumps(before, sort_keys=True)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT * FROM transactions WHERE id=?', (key,)).fetchone()
            if old:
                if (old['character'],old['kind'],old['before_json']) != (character,kind,encoded):
                    raise ValueError('Transaction ID reused for different work')
                return False
            pending = db.execute("SELECT 1 FROM transactions WHERE character=? AND phase NOT IN ('verified','aborted')", (character,)).fetchone()
            if pending:
                raise ValueError('Reconcile the previous transaction first')
            now = time.time()
            db.execute('INSERT INTO transactions VALUES(?,?,?,?,?,NULL,?,?)', (key,character,kind,'prepared',encoded,now,now))
            db.execute('INSERT INTO transaction_steps(transaction_id,stage,status,payload,timestamp) VALUES(?,?,?,?,?)',
                (key,'transaction','prepared','{}',now))
            if admission:
                row=db.execute('SELECT * FROM delivery_admissions WHERE request_id=?',(key,)).fetchone()
                items=json.dumps(canonical_delivery_items(before['items']),sort_keys=True)
                if (not row or row['character']!=character
                        or json.loads(row['uids_json'])!=sorted(item['uid'] for item in before['items'])
                        or row['items_json'] not in (None,items)):
                    raise ValueError('Delivery admission changed before transaction persistence')
                db.execute("UPDATE delivery_admissions SET phase='transaction_started',items_json=?,reason=NULL,updated=? WHERE request_id=? AND phase='admitted'",
                           (items,now,key))
                if db.execute('SELECT changes()').fetchone()[0]!=1:
                    raise ValueError('Delivery admission is no longer open')
            return True

    def transition(self, key, phase, result=None, *, expected=None):
        if phase not in LEGAL_TRANSITIONS:
            raise ValueError('Unknown transaction phase')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM transactions WHERE id=?', (key,)).fetchone()
            if not row:
                raise ValueError('Transaction not found')
            current = row['phase']
            if expected is not None and current != expected:
                raise ValueError('Transaction phase changed before transition')
            if current in TERMINAL_PHASES:
                if row['phase'] != phase:
                    raise ValueError('Completed transaction is immutable')
                return
            if phase not in LEGAL_TRANSITIONS.get(current,()):
                raise ValueError(f'Illegal transaction transition: {current} -> {phase}')
            now=time.time();encoded=json.dumps(result)
            changed=db.execute('UPDATE transactions SET phase=?,result_json=?,updated=? WHERE id=? AND phase=?',
                (phase,encoded,now,key,current)).rowcount
            if changed != 1:
                raise ValueError('Transaction phase changed before transition')
            db.execute('INSERT INTO transaction_steps(transaction_id,stage,status,payload,timestamp) VALUES(?,?,?,?,?)',
                (key,'transaction',phase,encoded,now))
            if phase == 'verified':
                db.execute('INSERT INTO events(character,event,payload,timestamp) VALUES(?,?,?,?)',
                    (row['character'],row['kind']+'_verified',json.dumps({'transaction_id':key,'result':result}),now))

    def step(self, key, stage, status, payload=None, *, terminal=False):
        if (not isinstance(stage,str) or not 1<=len(stage)<=100 or
                not isinstance(status,str) or not 1<=len(status)<=40):
            raise ValueError('Invalid transaction action stage')
        encoded=json.dumps(payload or {},sort_keys=True)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT phase FROM transactions WHERE id=?',(key,)).fetchone()
            if not row:raise ValueError('Transaction not found')
            if row['phase'] in TERMINAL_PHASES:
                if not terminal or stage not in ('cleanup','cleanup_trade','receiver_receipt'):
                    raise ValueError('Completed transaction is immutable')
            db.execute('INSERT INTO transaction_steps(transaction_id,stage,status,payload,timestamp) VALUES(?,?,?,?,?)',
                (key,stage,status,encoded,time.time()))

    def trace(self, key):
        with self.db() as db:
            return [dict(row) for row in db.execute(
                'SELECT id,stage,status,payload,timestamp FROM transaction_steps WHERE transaction_id=? ORDER BY id',(key,))]

    def admit_delivery(self,key,character,uids,origin,items=None):
        encoded_uids=json.dumps(sorted(uids));encoded_origin=json.dumps(origin,sort_keys=True)
        encoded_items=(json.dumps(canonical_delivery_items(items),sort_keys=True)
                        if items is not None else None)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM delivery_admissions WHERE request_id=?',(key,)).fetchone()
            if row:
                if (row['character']!=character or row['uids_json']!=encoded_uids
                        or row['origin_json']!=encoded_origin
                        or encoded_items is not None and row['items_json'] not in (None,encoded_items)):
                    raise ValueError('Delivery admission ID reused for another request')
                return dict(row)
            now=time.time()
            db.execute('INSERT INTO delivery_admissions VALUES(?,?,?,?,?,?,?,?,?)',
                       (key,character,encoded_uids,encoded_items,encoded_origin,'admitted',None,now,now))

    def update_delivery_admission(self,key,phase,*,items=None,reason=None):
        if phase not in ('admitted','transaction_started','rejected'):
            raise ValueError('Unknown delivery admission phase')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM delivery_admissions WHERE request_id=?',(key,)).fetchone()
            if not row:raise ValueError('Delivery admission not found')
            if row['phase']=='transaction_started' and phase!='transaction_started':
                raise ValueError('Started delivery admission is immutable')
            encoded=(json.dumps(canonical_delivery_items(items),sort_keys=True)
                      if items is not None else row['items_json'])
            if row['items_json'] is not None and encoded!=row['items_json']:
                raise ValueError('Delivery admission item fingerprints changed')
            db.execute('UPDATE delivery_admissions SET phase=?,items_json=?,reason=?,updated=? WHERE request_id=?',
                       (phase,encoded,reason,time.time(),key))

    def delivery_admission(self,key):
        with self.db() as db:
            row=db.execute('SELECT * FROM delivery_admissions WHERE request_id=?',(key,)).fetchone()
        return dict(row) if row else None

    def pending(self, character):
        with self.db() as db:
            rows = db.execute("SELECT * FROM transactions WHERE character=? AND phase NOT IN ('verified','aborted') ORDER BY created", (character_name(character),)).fetchall()
        return [dict(row) for row in rows]

    def events(self, after=0, limit=100):
        with self.db() as db:
            return [dict(row) for row in db.execute('SELECT * FROM events WHERE id>? ORDER BY id LIMIT ?', (after,limit))]

    def request_scan(self, character, request_id, now=None):
        character = character_name(character)
        now = time.time() if now is None else now
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 100:
            raise ValueError('Invalid scan request ID')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            seen = db.execute('SELECT state FROM scan_requests WHERE character=? AND request_id=?',
                (character,request_id)).fetchone()
            if seen:
                return json.loads(seen[0])
            old = db.execute("SELECT value FROM state WHERE character=? AND name='scan'", (character,)).fetchone()
            state = json.loads(old[0]) if old else {}
            if state.get('request_id') == request_id or state.get('pending'):
                db.execute('INSERT OR IGNORE INTO scan_requests VALUES(?,?,?)',(character,request_id,json.dumps(state)))
                return state
            state = {'request_id':request_id,'pending':True,'requested_at':now,'next_scan':state.get('next_scan')}
            db.execute('INSERT OR REPLACE INTO state VALUES(?,?,?)', (character,'scan',json.dumps(state)))
            db.execute('INSERT INTO scan_requests VALUES(?,?,?)',(character,request_id,json.dumps(state)))
        return state

    def complete_scan(self, character, request_id, *, changed, deferred, now=None):
        now = time.time() if now is None else now
        character = character_name(character)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT value FROM state WHERE character=? AND name='scan'",(character,)).fetchone()
            state = json.loads(row[0]) if row else {}
            if state.get('request_id') != request_id or not state.get('pending'):
                return False
            state.update(pending=False,completed_at=now,changed=changed,deferred=deferred,
                         next_scan=None if state.get('one_time') else now+43200)
            encoded = json.dumps(state)
            db.execute('INSERT OR REPLACE INTO state VALUES(?,?,?)',(character,'scan',encoded))
            if state.get('one_time'):
                # Completion and pause are one durable commit. Restarting
                # between them cannot accidentally enable ongoing trading.
                db.execute('INSERT OR REPLACE INTO state VALUES(?,?,?)',(character,'enabled','false'))
            for request in db.execute('SELECT request_id,state FROM scan_requests WHERE character=?',(character,)).fetchall():
                if json.loads(request['state']).get('request_id')==request_id:
                    db.execute('UPDATE scan_requests SET state=? WHERE character=? AND request_id=?',
                        (encoded,character,request['request_id']))
            db.execute('INSERT INTO events(character,event,payload,timestamp) VALUES(?,?,?,?)',
                (character,'scan_completed',json.dumps({'request_id':request_id,'changed':changed,'deferred':deferred}),now))
            return True

    def request_once(self, character, request_id, now=None):
        character = character_name(character)
        now = time.time() if now is None else now
        if not isinstance(request_id,str) or not 1<=len(request_id)<=100:
            raise ValueError('Invalid one-time request ID')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            seen = db.execute('SELECT state FROM scan_requests WHERE character=? AND request_id=?',(character,request_id)).fetchone()
            if seen:
                state = json.loads(seen[0])
                if not state.get('one_time'):
                    raise ValueError('Request ID belongs to a different operation')
                return state,False
            old = db.execute("SELECT value FROM state WHERE character=? AND name='scan'",(character,)).fetchone()
            if old and json.loads(old[0]).get('pending'):
                raise ValueError('Finish or pause the existing scan before requesting a different one-time batch')
            if db.execute("SELECT 1 FROM transactions WHERE character=? AND phase NOT IN ('verified','aborted')",(character,)).fetchone():
                raise ValueError('Reconcile the unfinished transaction before listing once')
            state = {'request_id':request_id,'pending':True,'one_time':True,'requested_at':now}
            encoded = json.dumps(state)
            for name,value in (('scan',encoded),('enabled','true')):
                db.execute('INSERT OR REPLACE INTO state VALUES(?,?,?)',(character,name,value))
            db.execute('INSERT INTO scan_requests VALUES(?,?,?)',(character,request_id,encoded))
            db.execute('INSERT INTO events(character,event,payload,timestamp) VALUES(?,?,?,?)',
                (character,'one_time_requested',json.dumps({'request_id':request_id}),now))
            return state,True

    def resume_batch(self, character):
        """An explicit UI resume continues the queued scan as listing-only work."""
        character=character_name(character)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute("SELECT value FROM state WHERE character=? AND name='scan'",(character,)).fetchone()
            state=json.loads(row[0]) if row else {}
            if not state.get('pending'):raise ValueError('No paused batch to resume')
            if db.execute("SELECT 1 FROM transactions WHERE character=? AND phase NOT IN ('verified','aborted')",(character,)).fetchone():
                raise ValueError('Reconcile the interrupted transaction before resuming')
            state['one_time']=True
            db.execute('INSERT OR REPLACE INTO state VALUES(?,?,?)',(character,'scan',json.dumps(state)))
            db.execute('INSERT OR REPLACE INTO state VALUES(?,?,?)',(character,'enabled','true'))
        return state
