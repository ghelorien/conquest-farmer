"""Receipt-backed qualification for one narrowly bounded native listing path.

This never qualifies focus, embedding, booth opening, trade or recovery. A
verified live submission in the same process and owned booth is required.
Static renderer pins, observations and legacy qualification JSON grant nothing.
"""
import hashlib
import json
import time

from conquest.memory_build_layout import CLIENT_SHA256_1078

CAPABILITY = 'foreground_open_booth_listing_1078'
STATE = 'listing_capability_1078'
ENGINE_REVISION = 1


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _proof(before, steps, first, second):
    from conquest.merchants.booth_listing_once_1078 import _listed, OWNERSHIP_FIELDS
    from conquest.merchants.listing_preflight_1078 import _MODAL_RENDER_SHA256
    request = before['request']
    if (before.get('client_sha256') != CLIENT_SHA256_1078
            or before.get('listing_engine_revision') != ENGINE_REVISION
            or before.get('control', {}).get('enabled') is not False
            or before.get('control', {}).get('paused')
            or not before.get('profile_id') or not before.get('farmer_target')
            or before['snapshot']['identity'] != request['expected_identity']
            or before['snapshot']['character_uid'] != request['expected_character_uid']
            or before['snapshot']['own_booth_uid'] != request['expected_own_booth_uid']
            or any(first[key] != second[key] for key in OWNERSHIP_FIELDS)
            or not first['timestamp'] <= second['timestamp']
            or not _listed(before['snapshot'], second, request)):
        raise ValueError('Exact live listing receipt does not qualify this input path')
    required = [('baseline', 'verified'), ('drag_press', 'before_action'),
                ('native_dialog', 'verified'), ('amount_press', 'before_action'),
                ('price', 'verified'), ('confirm_press', 'before_mouse_down')]
    positions = []
    for stage, status in required:
        found = [(n, row) for n, row in enumerate(steps)
                 if row['stage'] == stage and row['status'] == status]
        if len(found) != 1:
            raise ValueError('Listing input evidence is incomplete or repeated')
        positions.append(found[0][0])
    if positions != sorted(positions):
        raise ValueError('Listing input evidence is out of order')
    dialog = json.loads(steps[positions[2]]['payload'])
    if (dialog.get('renderer_sha256') != _MODAL_RENDER_SHA256
            or dialog.get('uid') != request['item_uid']):
        raise ValueError('Native dialog semantics were not verified for this item')
    confirm = json.loads(steps[positions[-1]]['payload'])
    if confirm != {'uid': request['item_uid'], 'price': request['price'],
                   'owned_booth_uid': request['expected_own_booth_uid']}:
        raise ValueError('Irreversible confirmation does not match the exact request')
    return {'capability': CAPABILITY, 'engine_revision': ENGINE_REVISION,
            'client_sha256': CLIENT_SHA256_1078, 'profile_id': before['profile_id'],
            'identity': request['expected_identity'],
            'character_uid': request['expected_character_uid'],
            'own_booth_uid': request['expected_own_booth_uid'],
            'first': first, 'second': second}


def settle(journal, request_id, first, second):
    """Atomically persist exact receipt and its restricted capability evidence."""
    from conquest.merchants.booth_listing_once_1078 import KIND
    with journal.db() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM transactions WHERE id=?', (request_id,)).fetchone()
        if not row or row['kind'] != KIND or row['phase'] not in ('prepared', 'submitted', 'uncertain'):
            raise ValueError('Listing transaction is no longer awaiting settlement')
        before = json.loads(row['before_json'])
        steps = [dict(step) for step in db.execute(
            'SELECT stage,status,payload FROM transaction_steps WHERE transaction_id=? ORDER BY id',
            (request_id,))]
        now = time.time()
        if not all(0 <= now - sample['timestamp'] <= 3 for sample in (first, second)):
            raise ValueError('Listing settlement observations expired')
        proof = _proof(before, steps, first, second)
        request = before['request']
        result = {'uid': request['item_uid'], 'price': request['price'],
                  'owned_booth_uid': request['expected_own_booth_uid'],
                  'confirmation_attempted': True, 'exact_memory_listing_verified': True,
                  'listing_capability_evidence': proof}
        encoded = json.dumps(result, sort_keys=True)
        db.execute("UPDATE transactions SET phase='verified',result_json=?,updated=? WHERE id=?",
                   (encoded, now, request_id))
        db.execute('INSERT INTO transaction_steps(transaction_id,stage,status,payload,timestamp) VALUES(?,?,?,?,?)',
                   (request_id, 'transaction', 'verified', encoded, now))
        capability = {'request_id': request_id, 'proof_digest': _digest(proof),
                      'qualified_at': now, 'capability': CAPABILITY}
        db.execute('INSERT OR REPLACE INTO state VALUES(?,?,?)',
                   (row['character'], STATE, json.dumps(capability)))
        db.execute('INSERT INTO events(character,event,payload,timestamp) VALUES(?,?,?,?)',
                   (row['character'], KIND+'_verified',
                    json.dumps({'transaction_id': request_id, 'result': result}), now))
    return capability


def require(journal, character, snapshot):
    """Revalidate the durable source receipt, never a saved boolean flag."""
    from conquest.merchants.booth_listing_once_1078 import KIND
    from conquest.merchants.journal import character_name
    character = character_name(character)
    with journal.db() as db:
        saved = db.execute('SELECT value FROM state WHERE character=? AND name=?',
                           (character, STATE)).fetchone()
        if not saved:
            raise ValueError('listing_live_receipt_missing')
        saved = json.loads(saved[0])
        row = db.execute('SELECT * FROM transactions WHERE id=?', (saved['request_id'],)).fetchone()
        if not row or row['kind'] != KIND or row['phase'] != 'verified' or row['character'] != character:
            raise ValueError('listing_live_receipt_invalid')
        result = json.loads(row['result_json'])
        proof = result.get('listing_capability_evidence') or {}
        steps = [dict(step) for step in db.execute(
            'SELECT stage,status,payload FROM transaction_steps WHERE transaction_id=? ORDER BY id',
            (saved['request_id'],))]
        validated = _proof(json.loads(row['before_json']), steps, proof['first'], proof['second'])
        if proof != validated or saved['proof_digest'] != _digest(proof):
            raise ValueError('listing_live_receipt_changed')
    if (snapshot['identity'] != proof['identity']
            or snapshot['character_uid'] != proof['character_uid']
            or snapshot['own_booth_uid'] != proof['own_booth_uid']
            or snapshot['character'] != proof['second']['character']
            or snapshot['server'] != 'America'):
        raise ValueError('listing_process_or_owned_booth_changed')
    return saved


def status(journal, character, snapshot):
    result = {CAPABILITY: False, 'booth_input': False, 'trade': False,
              'trade_request': False, 'login': False, 'market_return': False,
              'booth_setup': False, 'booth_panel': False,
              'automatic_focus': False, 'automatic_farmer_handoff': False}
    try:
        if snapshot is None:
            raise ValueError('merchant_observation_unavailable')
        result['evidence'] = require(journal, character, snapshot)
        result[CAPABILITY] = True
        result['blocker'] = None
    except (ValueError, OSError, KeyError, TypeError) as error:
        result['blocker'] = str(error)
    return result
