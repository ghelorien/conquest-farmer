"""Machine-local visitor permissions and memory-observed manual trade sessions.

This domain never sends game input, grants automated delivery trust, or records
sales. Callers supply fresh, qualified read-only memory snapshots. A completed
session proves settled ownership, not that any particular trade succeeded.
"""
from contextlib import contextmanager
from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import time
import uuid

from conquest.character_context import state_path


TERMINAL_PHASES = ('completed', 'request_withdrawn', 'declined_verified', 'operator_overridden')
PHASES = ('approval_pending', 'manual_active', 'settlement_observed', 'needs_attention', *TERMINAL_PHASES)
SETTLEMENT_SECONDS = 5
MAX_EVIDENCE_AGE = 2
ITEM_FIELDS = ('uid', 'type_id', 'plus', 'gem1', 'gem2', 'quantity', 'bound')


class ManualSessionError(ValueError):
    """Invalid domain command; no input authority has been issued."""


class BindingMismatch(ManualSessionError):
    """The displayed approval no longer describes this live request."""


def _text(value, name):
    if not isinstance(value, str) or not value or value != value.strip():
        raise ManualSessionError(f'{name} must be a non-empty exact string')
    return value


def _integer(value, name, minimum=0, maximum=None):
    if type(value) is not int or value < minimum or maximum is not None and value > maximum:
        raise ManualSessionError(f'{name} is unavailable or invalid')
    return value


def _number(value, name):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ManualSessionError(f'{name} is unavailable or invalid')
    return value


def _json(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ManualSessionError('Evidence must be finite JSON data') from exc


def _digest(value):
    return hashlib.sha256(_json(value).encode('utf-8')).hexdigest()


def _now(now):
    # SQLite stores REAL times as floats; hash the same type that is read back.
    return float(_number(time.time() if now is None else now, 'Current time'))


@dataclass(frozen=True)
class VisitorKey:
    target_profile_id: str
    visitor_name: str
    visitor_server: str
    visitor_uid: int

    def __post_init__(self):
        for field in ('target_profile_id', 'visitor_name', 'visitor_server'):
            _text(getattr(self, field), field)
        _integer(self.visitor_uid, 'visitor_uid', 1)


def _key(value):
    return value if isinstance(value, VisitorKey) else VisitorKey(**value)


def _process(snapshot):
    identity = snapshot.get('identity')
    if not isinstance(identity, dict):
        raise ManualSessionError('Game-process identity is unavailable')
    _integer(identity.get('pid'), 'Process PID', 1)
    _integer(identity.get('creation_time_100ns'), 'Process creation time', 1)
    _text(identity.get('path'), 'Process image path')
    # Preserve the complete process identity, including architecture/build fields
    # when supplied. Never authorize by PID alone.
    return json.loads(_json(identity))


def _items(rows, *, listed=False):
    if not isinstance(rows, list):
        raise ManualSessionError('Exact owned item list is unavailable')
    result = []
    for row in rows:
        if not isinstance(row, dict) or any(field not in row for field in ITEM_FIELDS):
            raise ManualSessionError('Exact owned item attributes are unavailable')
        item = {field: row[field] for field in ITEM_FIELDS}
        for field in ('uid', 'type_id', 'quantity'):
            _integer(item[field], f'Item {field}', 1)
        _integer(item['plus'], 'Item plus', 0, 12)
        for field in ('gem1', 'gem2'):
            _integer(item[field], f'Item {field}', 0)
        if type(item['bound']) is not bool:
            raise ManualSessionError('Item binding is unavailable')
        if listed:
            item['price'] = _integer(row.get('price'), 'Booth price', 1)
        result.append(item)
    if len({item['uid'] for item in result}) != len(result):
        raise ManualSessionError('Duplicate owned item UID')
    return sorted(result, key=lambda item: item['uid'])


def canonical_ownership(snapshot, *, require_closed=True):
    """Canonical exact holdings, excluding time/HP/position and GUI geometry.

    Request/trade fields must explicitly be None for settlement. Nonsettlement
    callers may set require_closed=False; those fields are retained in the
    digest. Volatile fields remain in the separately persisted full evidence.
    """
    if not isinstance(snapshot, dict):
        raise ManualSessionError('Ownership evidence is unavailable')
    if any(field not in snapshot for field in ('request', 'trade')):
        raise ManualSessionError('Request/trade window evidence is unavailable')
    if require_closed and (snapshot['request'] is not None or snapshot['trade'] is not None):
        raise ManualSessionError('Request and trade windows must be absent')
    for field in ('request', 'trade'):
        if snapshot[field] is not None and (not isinstance(snapshot[field], dict) or not snapshot[field]):
            raise ManualSessionError(f'{field} window evidence is invalid')
    _number(snapshot.get('timestamp'), 'Observation time')
    inventory = _items(snapshot.get('inventory'))
    booth = _items(snapshot.get('booth'), listed=True)
    uids = [item['uid'] for item in inventory + booth]
    if len(set(uids)) != len(uids):
        raise ManualSessionError('Item appears in inventory and booth')
    capacity = _integer(snapshot.get('capacity'), 'Inventory capacity', 0, 40)
    if len(inventory) > capacity:
        raise ManualSessionError('Inventory stock exceeds observed capacity')
    if type(snapshot.get('booth_open')) is not bool:
        raise ManualSessionError('Owned booth state is unavailable')
    return {
        'identity': _process(snapshot),
        'character': _text(snapshot.get('character'), 'Target character'),
        'character_uid': _integer(snapshot.get('character_uid'), 'Target character UID', 1),
        'server': _text(snapshot.get('server'), 'Target server'),
        'inventory': inventory, 'booth': booth,
        'silver': _integer(snapshot.get('silver'), 'Owned silver'),
        'capacity': capacity,
        'booth_open': snapshot['booth_open'],
        'own_booth_uid': _integer(snapshot.get('own_booth_uid'), 'Owned booth UID'),
        'request': snapshot['request'], 'trade': snapshot['trade'],
    }


def ownership_digest(snapshot):
    """Digest of complete ownership with explicitly absent request/trade windows."""
    return _digest(canonical_ownership(snapshot))


def request_fingerprint(snapshot):
    request = snapshot.get('request')
    if not isinstance(request, dict) or not request:
        raise ManualSessionError('An exact incoming request is required')
    name = _text(request.get('participant'), 'Request participant')
    _integer(request.get('participant_uid'), 'Request participant UID', 1)
    if request.get('message') != f'{name} wishes to trade with you.':
        raise ManualSessionError('Request participant and message disagree')
    if request.get('server', snapshot.get('server')) != snapshot.get('server'):
        raise ManualSessionError('Request server disagrees with target server')
    return _digest(request)


def _fresh(snapshot, now):
    proof = canonical_ownership(snapshot, require_closed=False)
    if not 0 <= now - snapshot['timestamp'] <= MAX_EVIDENCE_AGE:
        raise ManualSessionError('Fresh memory evidence is required')
    return proof


def _holdings(proof):
    return {**proof, 'request': None, 'trade': None}


SCHEMA = '''
CREATE TABLE IF NOT EXISTS manual_schema(version INTEGER PRIMARY KEY CHECK(version=1));
INSERT OR IGNORE INTO manual_schema VALUES(1);
CREATE TABLE IF NOT EXISTS visitor_permissions(
 target_profile_id TEXT NOT NULL, visitor_name TEXT NOT NULL COLLATE BINARY,
 visitor_server TEXT NOT NULL COLLATE BINARY, visitor_uid INTEGER NOT NULL CHECK(visitor_uid>0),
 allowed INTEGER NOT NULL CHECK(allowed IN (0,1)), updated_at REAL NOT NULL,
 PRIMARY KEY(target_profile_id,visitor_name,visitor_server,visitor_uid));
CREATE TABLE IF NOT EXISTS manual_sessions(
 id TEXT PRIMARY KEY, target_profile_id TEXT NOT NULL, visitor_json TEXT NOT NULL,
 process_json TEXT NOT NULL, character_json TEXT NOT NULL, phase TEXT NOT NULL
 CHECK(phase IN ('approval_pending','manual_active','settlement_observed','needs_attention',
 'completed','request_withdrawn','declined_verified','operator_overridden')),
 current_request_id TEXT, ever_approved INTEGER NOT NULL DEFAULT 0,
 created_at REAL NOT NULL, updated_at REAL NOT NULL, last_observed_at REAL,
 stable_digest TEXT, stable_since REAL, stable_evidence_id INTEGER,
 reason TEXT, terminal_json TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS manual_one_open_per_target ON manual_sessions(target_profile_id)
 WHERE phase NOT IN ('completed','request_withdrawn','declined_verified','operator_overridden');
CREATE TABLE IF NOT EXISTS manual_requests(
 id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES manual_sessions(id),
 fingerprint TEXT NOT NULL, binding_json TEXT NOT NULL, before_json TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('pending','approved','closed','decline_pending','decline_claimed')),
 created_at REAL NOT NULL, expires_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS manual_declines(
 request_id TEXT PRIMARY KEY REFERENCES manual_requests(id), session_id TEXT NOT NULL REFERENCES manual_sessions(id),
 reason TEXT NOT NULL, created_at REAL NOT NULL, binding_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS manual_decline_claims(
 request_id TEXT PRIMARY KEY REFERENCES manual_declines(request_id),
 claimed_at REAL NOT NULL, evidence_id INTEGER NOT NULL REFERENCES manual_evidence(id));
CREATE TABLE IF NOT EXISTS manual_evidence(
 id INTEGER PRIMARY KEY, session_id TEXT NOT NULL REFERENCES manual_sessions(id),
 observed_at REAL, recorded_at REAL NOT NULL, digest TEXT NOT NULL,
 ownership_digest TEXT, snapshot_json TEXT NOT NULL, error TEXT);
CREATE TABLE IF NOT EXISTS manual_audit(
 id INTEGER PRIMARY KEY, session_id TEXT REFERENCES manual_sessions(id),
 event TEXT NOT NULL, at REAL NOT NULL, payload_json TEXT NOT NULL,
 previous_digest TEXT NOT NULL, digest TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS manual_audit_session ON manual_audit(session_id,id);
CREATE INDEX IF NOT EXISTS manual_evidence_session ON manual_evidence(session_id,id);
CREATE TRIGGER IF NOT EXISTS manual_terminal_immutable BEFORE UPDATE ON manual_sessions
 WHEN OLD.phase IN ('completed','request_withdrawn','declined_verified','operator_overridden')
 BEGIN SELECT RAISE(ABORT,'Terminal manual session is immutable'); END;
CREATE TRIGGER IF NOT EXISTS manual_session_no_delete BEFORE DELETE ON manual_sessions
 BEGIN SELECT RAISE(ABORT,'Manual sessions are durable'); END;
CREATE TRIGGER IF NOT EXISTS manual_session_identity_immutable BEFORE UPDATE ON manual_sessions
 WHEN NEW.id IS NOT OLD.id OR NEW.target_profile_id IS NOT OLD.target_profile_id
 OR NEW.visitor_json IS NOT OLD.visitor_json OR NEW.process_json IS NOT OLD.process_json
 OR NEW.character_json IS NOT OLD.character_json OR NEW.created_at IS NOT OLD.created_at
 BEGIN SELECT RAISE(ABORT,'Manual session identity is immutable'); END;
CREATE TRIGGER IF NOT EXISTS manual_request_binding_immutable BEFORE UPDATE ON manual_requests
 WHEN NEW.id IS NOT OLD.id OR NEW.session_id IS NOT OLD.session_id OR NEW.fingerprint IS NOT OLD.fingerprint
 OR NEW.binding_json IS NOT OLD.binding_json OR NEW.before_json IS NOT OLD.before_json
 OR NEW.created_at IS NOT OLD.created_at OR NEW.expires_at IS NOT OLD.expires_at
 BEGIN SELECT RAISE(ABORT,'Approval binding is immutable'); END;
CREATE TRIGGER IF NOT EXISTS manual_request_no_delete BEFORE DELETE ON manual_requests
 BEGIN SELECT RAISE(ABORT,'Manual request is durable'); END;
'''


class ManualSessionStore:
    """Serialized SQLite commands; explicit paths are useful for tests/integration.

    Permissions are local to this DB and never read from or written to portable
    profiles/trusted_sources. Returned dictionaries are detached JSON values.
    """
    def __init__(self, path=None, *, on_settlement=None):
        self.path = Path(path if path is not None else state_path('reports/merchants/journal.sqlite3'))
        self.on_settlement = on_settlement
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript(SCHEMA)
            for table in ('manual_audit', 'manual_evidence', 'manual_declines', 'manual_decline_claims'):
                for operation in ('UPDATE', 'DELETE'):
                    db.execute(f'CREATE TRIGGER IF NOT EXISTS {table}_no_{operation.lower()} '
                               f'BEFORE {operation} ON {table} BEGIN '
                               "SELECT RAISE(ABORT,'Manual audit evidence is immutable'); END")

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('PRAGMA synchronous=FULL')
        try:
            with db:
                yield db
        finally:
            db.close()

    def _audit(self, db, session_id, event, payload, now):
        previous = db.execute('SELECT digest FROM manual_audit ORDER BY id DESC LIMIT 1').fetchone()
        previous = previous[0] if previous else ''
        digest = _digest({'session_id': session_id, 'event': event, 'at': now,
                          'payload': payload, 'previous_digest': previous})
        db.execute('INSERT INTO manual_audit(session_id,event,at,payload_json,previous_digest,digest) VALUES(?,?,?,?,?,?)',
                   (session_id, event, now, _json(payload), previous, digest))

    def _evidence(self, db, session_id, snapshot, now, proof=None, error=None):
        # Even invalid/missing evidence is retained; never infer empty holdings.
        observed_at = snapshot.get('timestamp') if isinstance(snapshot, dict) else None
        if type(observed_at) not in (int, float) or not math.isfinite(observed_at):
            observed_at = None
        try:
            encoded = _json(snapshot)
        except ManualSessionError:
            # A malformed reader result must still persist needs_attention.
            # This representation is evidence of failure, never ownership proof.
            snapshot = {'invalid_evidence_repr': repr(snapshot)}
            encoded = _json(snapshot)
            error = error or 'Evidence is not finite JSON data'
        cursor = db.execute('INSERT INTO manual_evidence(session_id,observed_at,recorded_at,digest,ownership_digest,snapshot_json,error) VALUES(?,?,?,?,?,?,?)',
                            (session_id, observed_at, now, _digest(snapshot),
                             _digest(proof) if proof is not None else None, encoded, error))
        return cursor.lastrowid

    def _row(self, db, session_id):
        row = db.execute('SELECT * FROM manual_sessions WHERE id=?', (session_id,)).fetchone()
        if row is None:
            raise ManualSessionError('Manual session not found')
        return row

    def _view(self, db, row):
        result = dict(row)
        for field in ('visitor', 'process', 'character', 'terminal'):
            result[field] = json.loads(result.pop(field + '_json') or 'null')
        result['ever_approved'] = bool(result['ever_approved'])
        request = db.execute('SELECT * FROM manual_requests WHERE id=?', (row['current_request_id'],)).fetchone()
        result['approval_binding'] = json.loads(request['binding_json']) if request else None
        result['request_state'] = request['state'] if request else None
        result['expires_at'] = request['expires_at'] if request else None
        result['holds_automation'] = result['phase'] not in TERMINAL_PHASES
        return result

    def get(self, session_id):
        with self.db() as db:
            return self._view(db, self._row(db, session_id))

    def active(self, target_profile_id):
        _text(target_profile_id, 'Target profile ID')
        with self.db() as db:
            row = db.execute("SELECT * FROM manual_sessions WHERE target_profile_id=? AND phase NOT IN ('completed','request_withdrawn','declined_verified','operator_overridden')", (target_profile_id,)).fetchone()
            return self._view(db, row) if row else None

    def permissions(self, target_profile_id=None):
        with self.db() as db:
            return [dict(row) for row in db.execute('SELECT * FROM visitor_permissions' +
                    (' WHERE target_profile_id=?' if target_profile_id is not None else '') +
                    ' ORDER BY target_profile_id,visitor_name,visitor_server,visitor_uid',
                    (target_profile_id,) if target_profile_id is not None else ())]

    def allowed(self, visitor):
        visitor = _key(visitor)
        with self.db() as db:
            return self._allowed(db, visitor)

    def _allowed(self, db, visitor):
        row = db.execute('SELECT allowed FROM visitor_permissions WHERE target_profile_id=? AND visitor_name=? AND visitor_server=? AND visitor_uid=?',
                         tuple(asdict(visitor).values())).fetchone()
        return bool(row and row[0])

    def revoke(self, visitor, *, operator, now=None):
        visitor = _key(visitor); _text(operator, 'Operator'); now = _now(now)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT INTO visitor_permissions VALUES(?,?,?,?,0,?) ON CONFLICT(target_profile_id,visitor_name,visitor_server,visitor_uid) DO UPDATE SET allowed=0,updated_at=excluded.updated_at',
                       (*asdict(visitor).values(), now))
            self._audit(db, None, 'visitor_revoked', {'visitor': asdict(visitor), 'operator': operator}, now)

    def _new_session(self, db, visitor, proof, now, phase='approval_pending', *, target_profile_id=None):
        session_id = uuid.uuid4().hex
        target_profile_id = visitor.target_profile_id if visitor is not None else _text(target_profile_id, 'Target profile ID')
        visitor_data = asdict(visitor) if visitor is not None else None
        character = {field: proof[field] for field in ('character', 'character_uid', 'server')}
        db.execute('INSERT INTO manual_sessions(id,target_profile_id,visitor_json,process_json,character_json,phase,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',
                   (session_id, target_profile_id, _json(visitor_data), _json(proof['identity']),
                    _json(character), phase, now, now))
        self._audit(db, session_id, 'session_started', {'visitor': visitor_data, 'phase': phase}, now)
        return self._row(db, session_id)

    def begin_request(self, target_profile_id, snapshot, *, session_id=None, timeout_seconds=30, now=None):
        """Persist approval for one request; a new request clears stabilization.

        A repeated still-visible request returns its existing binding. To bind a
        second identical request, first observe the earlier request's absence.
        """
        now = _now(now); proof = _fresh(snapshot, now)
        fingerprint = request_fingerprint(snapshot)
        if snapshot['trade'] is not None:
            raise ManualSessionError('Cannot approve a request over an open trade')
        _number(timeout_seconds, 'Approval timeout')
        if timeout_seconds <= 0:
            raise ManualSessionError('Approval timeout must be positive')
        visitor = VisitorKey(target_profile_id, snapshot['request']['participant'],
                             snapshot['server'], snapshot['request']['participant_uid'])
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT * FROM manual_sessions WHERE target_profile_id=? AND phase NOT IN ('completed','request_withdrawn','declined_verified','operator_overridden')", (target_profile_id,)).fetchone()
            if session_id is not None and (row is None or row['id'] != session_id):
                raise BindingMismatch('Session identity changed')
            if row:
                self._same_owner(row, proof)
                if row['visitor_json'] != _json(asdict(visitor)) or row['phase'] == 'needs_attention':
                    raise BindingMismatch('Reconcile the existing manual session first')
                prior = db.execute('SELECT * FROM manual_requests WHERE id=?', (row['current_request_id'],)).fetchone()
                if prior and prior['state'] != 'closed':
                    if prior['fingerprint'] != fingerprint:
                        raise BindingMismatch('Existing request changed without a closed-window observation')
                    return self._view(db, row)
            else:
                row = self._new_session(db, visitor, proof, now)
            request_id = uuid.uuid4().hex
            evidence_id = self._evidence(db, row['id'], snapshot, now, proof)
            binding = {'version': 1, 'session_id': row['id'], 'request_id': request_id,
                       'target_profile_id': target_profile_id, 'visitor': asdict(visitor),
                       'game_process_identity': proof['identity'],
                       'character_identity': json.loads(row['character_json']),
                       'request_fingerprint': fingerprint, 'evidence_digest': _digest(snapshot),
                       'ownership_digest': _digest(proof)}
            db.execute('INSERT INTO manual_requests VALUES(?,?,?,?,?,?,?,?)',
                       (request_id, row['id'], fingerprint, _json(binding), _json(proof), 'pending', now, now + timeout_seconds))
            db.execute("UPDATE manual_sessions SET current_request_id=?,phase='approval_pending',stable_digest=NULL,stable_since=NULL,stable_evidence_id=NULL,reason=NULL,updated_at=?,last_observed_at=? WHERE id=?",
                       (request_id, now, snapshot['timestamp'], row['id']))
            self._audit(db, row['id'], 'approval_pending', {'binding': binding, 'evidence_id': evidence_id}, now)
            return self._view(db, self._row(db, row['id']))

    def _same_owner(self, row, proof):
        if row['process_json'] != _json(proof['identity']):
            raise BindingMismatch('Game process rolled over')
        character = {field: proof[field] for field in ('character', 'character_uid', 'server')}
        if row['character_json'] != _json(character):
            raise BindingMismatch('Target character identity changed')

    def _bound_request(self, db, binding, snapshot, now):
        if not isinstance(binding, dict):
            raise BindingMismatch('Approval binding is required')
        row = self._row(db, binding.get('session_id'))
        request = db.execute('SELECT * FROM manual_requests WHERE id=?', (row['current_request_id'],)).fetchone()
        if request is None or request['binding_json'] != _json(binding):
            raise BindingMismatch('Approval binding changed')
        proof = _fresh(snapshot, now)
        self._same_owner(row, proof)
        if snapshot['trade'] is not None or request_fingerprint(snapshot) != request['fingerprint']:
            raise BindingMismatch('Live request changed')
        if _digest(proof) != binding['ownership_digest']:
            raise BindingMismatch('Live ownership evidence changed')
        if row['last_observed_at'] is not None and snapshot['timestamp'] < row['last_observed_at']:
            raise BindingMismatch('Live evidence predates the latest observation')
        return row, request, proof

    def allow_and_activate(self, binding, live_snapshot, *, operator, now=None):
        """Atomically allow exactly this visitor and activate exactly this request."""
        _text(operator, 'Operator')
        return self._activate(binding, live_snapshot, operator=operator, grant=True, now=now)

    def activate_allowed(self, binding, live_snapshot, *, now=None):
        """Activate a live bound request using an existing exact local permission."""
        return self._activate(binding, live_snapshot, operator=None, grant=False, now=now)

    def _activate(self, binding, snapshot, *, operator, grant, now):
        now = _now(now)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row, request, proof = self._bound_request(db, binding, snapshot, now)
            if row['phase'] == 'manual_active' and request['state'] == 'approved':
                return self._view(db, row)
            if row['phase'] != 'approval_pending' or request['state'] != 'pending':
                raise BindingMismatch('Approval is no longer pending')
            if now >= request['expires_at']:
                self._decline(db, row, request, 'timeout', now)
                return self._view(db, self._row(db, row['id']))
            visitor = _key(json.loads(row['visitor_json']))
            if grant:
                db.execute('INSERT INTO visitor_permissions VALUES(?,?,?,?,1,?) ON CONFLICT(target_profile_id,visitor_name,visitor_server,visitor_uid) DO UPDATE SET allowed=1,updated_at=excluded.updated_at',
                           (*asdict(visitor).values(), now))
            elif not self._allowed(db, visitor):
                raise ManualSessionError('Exact visitor permission is absent')
            evidence_id = self._evidence(db, row['id'], snapshot, now, proof)
            db.execute("UPDATE manual_requests SET state='approved' WHERE id=?", (request['id'],))
            db.execute("UPDATE manual_sessions SET phase='manual_active',ever_approved=1,stable_digest=NULL,stable_since=NULL,stable_evidence_id=NULL,updated_at=?,last_observed_at=? WHERE id=?",
                       (now, snapshot['timestamp'], row['id']))
            self._audit(db, row['id'], 'request_approved', {'binding': binding, 'permission_granted': grant,
                        'operator': operator, 'evidence_id': evidence_id}, now)
            return self._view(db, self._row(db, row['id']))

    def _decline(self, db, row, request, reason, now):
        if request['state'] != 'pending' or row['phase'] != 'approval_pending':
            return False
        db.execute('INSERT INTO manual_declines VALUES(?,?,?,?,?)',
                   (request['id'], row['id'], reason, now, request['binding_json']))
        db.execute("UPDATE manual_requests SET state='decline_pending' WHERE id=?", (request['id'],))
        db.execute('UPDATE manual_sessions SET reason=?,updated_at=? WHERE id=?', (reason, now, row['id']))
        self._audit(db, row['id'], 'decline_requested', {'request_id': request['id'], 'reason': reason}, now)
        return True

    def reject(self, binding, *, operator, now=None):
        """Persist rejection once. This does not claim or dispatch decline input."""
        _text(operator, 'Operator'); now = _now(now)
        if not isinstance(binding, dict):
            raise BindingMismatch('Approval binding is required')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = self._row(db, binding.get('session_id'))
            request = db.execute('SELECT * FROM manual_requests WHERE id=?', (row['current_request_id'],)).fetchone()
            if request is None or request['binding_json'] != _json(binding):
                raise BindingMismatch('Approval binding changed')
            if self._decline(db, row, request, 'rejected', now):
                self._audit(db, row['id'], 'operator_rejected', {'operator': operator, 'request_id': request['id']}, now)
            return self._view(db, self._row(db, row['id']))

    def expire(self, session_id, *, now=None):
        """Persist a timeout once; serialized against approvals and rejection."""
        now = _now(now)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = self._row(db, session_id)
            request = db.execute('SELECT * FROM manual_requests WHERE id=?', (row['current_request_id'],)).fetchone()
            if request and now >= request['expires_at']:
                self._decline(db, row, request, 'timeout', now)
            return self._view(db, self._row(db, session_id))

    def claim_decline(self, session_id, live_snapshot, *, now=None):
        """Return a durable decline claim at most once, immediately before input.

        A crash after this call is an uncertain attempt. Restart must observe;
        it must never dispatch this claim again. Domain persistence cannot make
        an external game click transactional with SQLite.
        """
        now = _now(now)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = self._row(db, session_id)
            request = db.execute('SELECT * FROM manual_requests WHERE id=?', (row['current_request_id'],)).fetchone()
            if request is None or request['state'] != 'decline_pending' or row['phase'] != 'approval_pending':
                return None
            binding = json.loads(request['binding_json'])
            row, request, proof = self._bound_request(db, binding, live_snapshot, now)
            evidence_id = self._evidence(db, session_id, live_snapshot, now, proof)
            db.execute('INSERT INTO manual_decline_claims VALUES(?,?,?)', (request['id'], now, evidence_id))
            db.execute("UPDATE manual_requests SET state='decline_claimed' WHERE id=?", (request['id'],))
            self._audit(db, session_id, 'decline_claimed', {'request_id': request['id'], 'evidence_id': evidence_id}, now)
            return {'session_id': session_id, 'request_id': request['id'], 'binding': binding,
                    'claimed_at': now, 'evidence_id': evidence_id}

    def _attention(self, db, row, reason, now, evidence_id):
        db.execute("UPDATE manual_sessions SET phase='needs_attention',reason=?,updated_at=?,stable_digest=NULL,stable_since=NULL,stable_evidence_id=NULL WHERE id=?",
                   (reason, now, row['id']))
        self._audit(db, row['id'], 'needs_attention', {'reason': reason, 'evidence_id': evidence_id}, now)

    def observe(self, session_id, snapshot, *, now=None):
        """Observe and settle without input; missing/changed identity stays held."""
        now = _now(now)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = self._row(db, session_id)
            if row['phase'] in TERMINAL_PHASES:
                return self._view(db, row)
            try:
                proof = _fresh(snapshot, now)
                self._same_owner(row, proof)
            except (ManualSessionError, KeyError, TypeError) as exc:
                evidence_id = self._evidence(db, session_id, snapshot, now, error=str(exc))
                self._attention(db, row, str(exc), now, evidence_id)
                return self._view(db, self._row(db, session_id))
            evidence_id = self._evidence(db, session_id, snapshot, now, proof)
            if row['phase'] == 'needs_attention':
                # Observation cannot silently forgive a rollover or missing data.
                return self._view(db, row)
            if row['last_observed_at'] is not None and snapshot['timestamp'] <= row['last_observed_at']:
                self._audit(db, session_id, 'observation_ignored', {'reason': 'non_increasing_time', 'evidence_id': evidence_id}, now)
                return self._view(db, row)
            request = db.execute('SELECT * FROM manual_requests WHERE id=?', (row['current_request_id'],)).fetchone()
            trade, incoming = snapshot['trade'], snapshot['request']
            visitor = json.loads(row['visitor_json'])
            approved = bool(request and request['state'] == 'approved')
            problem = None
            if trade is not None:
                if (not approved or trade.get('participant') != visitor['visitor_name']
                        or trade.get('participant_uid') != visitor['visitor_uid']
                        or trade.get('server', snapshot['server']) != visitor['visitor_server']):
                    problem = 'Unapproved or mismatched open trade'
            if incoming is not None:
                try:
                    same_request = bool(request and request['state'] != 'closed'
                                        and request_fingerprint(snapshot) == request['fingerprint'])
                except ManualSessionError:
                    same_request = False
                if not same_request:
                    problem = 'New request requires a fresh approval binding'
            if problem:
                self._attention(db, row, problem, now, evidence_id)
            elif trade is not None or incoming is not None:
                db.execute('UPDATE manual_sessions SET phase=?,stable_digest=NULL,stable_since=NULL,stable_evidence_id=NULL,last_observed_at=?,updated_at=? WHERE id=?',
                           ('manual_active' if approved else 'approval_pending', snapshot['timestamp'], now, session_id))
                self._audit(db, session_id, 'windows_observed', {'evidence_id': evidence_id}, now)
            else:
                digest = _digest(proof)
                claimed = bool(request and db.execute('SELECT 1 FROM manual_decline_claims WHERE request_id=?', (request['id'],)).fetchone())
                if not row['ever_approved'] and request and _digest(_holdings(json.loads(request['before_json']))) != digest:
                    self._attention(db, row, 'Unapproved ownership changed', now, evidence_id)
                elif row['stable_digest'] == digest and snapshot['timestamp'] - row['stable_since'] >= SETTLEMENT_SECONDS:
                    phase = 'completed' if row['ever_approved'] else 'declined_verified' if claimed else 'request_withdrawn'
                    receipt = {'phase': phase, 'ownership_digest': digest, 'first_evidence_id': row['stable_evidence_id'],
                               'final_evidence_id': evidence_id, 'stable_since': row['stable_since'],
                               'settled_at': snapshot['timestamp'], 'sales_receipt': False}
                    db.execute('UPDATE manual_sessions SET phase=?,terminal_json=?,last_observed_at=?,updated_at=? WHERE id=?',
                               (phase, _json(receipt), snapshot['timestamp'], now, session_id))
                    self._audit(db, session_id, 'session_terminal', receipt, now)
                    if self.on_settlement is not None:
                        # Integration writes sales gap/baseline/new-stock using
                        # this exact connection. Any failure rolls back this
                        # entire observation, audit, and terminal transition.
                        self.on_settlement(db, self._view(db, self._row(db, session_id)), snapshot, receipt)
                else:
                    same = row['stable_digest'] == digest
                    db.execute("UPDATE manual_sessions SET phase='settlement_observed',stable_digest=?,stable_since=?,stable_evidence_id=?,last_observed_at=?,updated_at=? WHERE id=?",
                               (digest, row['stable_since'] if same else snapshot['timestamp'],
                                row['stable_evidence_id'] if same else evidence_id, snapshot['timestamp'], now, session_id))
                    self._audit(db, session_id, 'settlement_observed', {'evidence_id': evidence_id, 'ownership_digest': digest}, now)
                if request:
                    db.execute("UPDATE manual_requests SET state='closed' WHERE id=?", (request['id'],))
            return self._view(db, self._row(db, session_id))

    def resume(self, session_id, snapshot, *, now=None):
        """Same-process restart is observation only; no input claim is replayed."""
        return self.observe(session_id, snapshot, now=now)

    def observe_target(self, target_profile_id, snapshot, *, now=None):
        """Observe a held session or quarantine an unapproved already-open trade.

        With no session and no open trade returns None. Incoming requests should
        instead go through begin_request, which binds the exact approval.
        """
        existing = self.active(target_profile_id)
        if existing:
            return self.observe(existing['id'], snapshot, now=now)
        if not isinstance(snapshot, dict) or snapshot.get('trade') is None:
            return None
        now = _now(now)
        # A quarantine needs a known target, never a guessed visitor or stock.
        # Missing visitor/ownership data is retained as failure evidence and
        # cannot become a permission key or later settle automatically.
        proof = {'identity': _process(snapshot),
                 'character': _text(snapshot.get('character'), 'Target character'),
                 'character_uid': _integer(snapshot.get('character_uid'), 'Target character UID', 1),
                 'server': _text(snapshot.get('server'), 'Target server')}
        full_proof, visitor, error = None, None, None
        try:
            full_proof = _fresh(snapshot, now)
            trade = snapshot['trade']
            visitor = VisitorKey(target_profile_id, trade.get('participant'), snapshot['server'], trade.get('participant_uid'))
        except (ManualSessionError, AttributeError) as exc:
            error = str(exc)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            # Another worker may have acquired this target since active().
            row = db.execute("SELECT * FROM manual_sessions WHERE target_profile_id=? AND phase NOT IN ('completed','request_withdrawn','declined_verified','operator_overridden')", (target_profile_id,)).fetchone()
            if row is None:
                row = self._new_session(db, visitor, proof, now, 'needs_attention', target_profile_id=target_profile_id)
                evidence_id = self._evidence(db, row['id'], snapshot, now, full_proof, error)
                reason = 'Unapproved open trade without a manual session'
                self._attention(db, row, reason + (': ' + error if error else ''), now, evidence_id)
            return self._view(db, self._row(db, row['id']))

    def operator_override(self, session_id, *, confirmation_reference, operator, reason, now=None):
        """Explicit disposition only; never a successful trade or a sale receipt."""
        for name, value in (('Confirmation reference', confirmation_reference), ('Operator', operator), ('Reason', reason)):
            _text(value, name)
        now = _now(now)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = self._row(db, session_id)
            if row['phase'] in TERMINAL_PHASES:
                receipt = json.loads(row['terminal_json'] or '{}')
                if row['phase'] != 'operator_overridden' or receipt.get('confirmation_reference') != confirmation_reference:
                    raise ManualSessionError('Terminal disposition is immutable')
                return self._view(db, row)
            receipt = {'confirmation_reference': confirmation_reference, 'operator': operator, 'reason': reason,
                       'original_phase': row['phase'], 'at': now, 'sales_receipt': False}
            db.execute("UPDATE manual_sessions SET phase='operator_overridden',terminal_json=?,updated_at=? WHERE id=?", (_json(receipt), now, session_id))
            self._audit(db, session_id, 'operator_overridden', receipt, now)
            return self._view(db, self._row(db, session_id))

    def audit(self, session_id=None):
        with self.db() as db:
            rows = db.execute('SELECT * FROM manual_audit' + (' WHERE session_id=?' if session_id else '') + ' ORDER BY id',
                              (session_id,) if session_id else ())
            return [{**dict(row), 'payload': json.loads(row['payload_json'])} for row in rows]

    def evidence(self, session_id):
        with self.db() as db:
            return [{**dict(row), 'snapshot': json.loads(row['snapshot_json'])} for row in db.execute(
                'SELECT * FROM manual_evidence WHERE session_id=? ORDER BY id', (session_id,))]

    def verify_audit(self):
        """Check the append-only global hash chain, without mutating any records."""
        previous = ''
        for record in self.audit():
            expected = _digest({'session_id': record['session_id'], 'event': record['event'],
                                'at': record['at'], 'payload': record['payload'],
                                'previous_digest': previous})
            if record['previous_digest'] != previous or record['digest'] != expected:
                return False
            previous = record['digest']
        return True

    def overlaps(self, target_profile_id, start, end):
        """Manual intervals for sales exclusion, including unresolved sessions.

        Every phase, including pending approval, is conservatively excluded.
        A terminal session ends at updated_at; an open session has no end.
        """
        _number(start, 'Interval start'); _number(end, 'Interval end')
        if end < start:
            raise ManualSessionError('Interval end precedes start')
        with self.db() as db:
            rows = db.execute("SELECT * FROM manual_sessions WHERE target_profile_id=? AND created_at<=? AND (phase NOT IN ('completed','request_withdrawn','declined_verified','operator_overridden') OR updated_at>=?) ORDER BY created_at,id",
                              (target_profile_id, end, start))
            return [self._view(db, row) for row in rows]
