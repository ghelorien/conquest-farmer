"""Read-only, exact-1078 settlement of an unexpected-loss Market arrival.

This deliberately is not a recovery driver.  It only turns a still-active
``recovery_safety`` incident into ``market_arrived`` after two independent
native-memory reads agree.  It neither obtains input nor changes any of the
merchant's saved operating intentions.
"""
import hashlib
import json
import time

from conquest.character_context import resolve_merchant
from conquest.memory_build_layout import CLIENT_SHA256_1078
from conquest.merchants.journal import character_name
from conquest.merchants.observe_1078 import observe
from conquest.merchants.recovery_safety import KEY


def _blocker(reason):
    """Keep a failed qualification explicit and side-effect free."""
    return {'settled': False, 'phase': 'blocked', 'blocker': reason,
            'read_only': True, 'input_qualified': False}


def _manual_fenced(runtime, character):
    coordinator = getattr(runtime, 'coordinator', None)
    if (coordinator and hasattr(coordinator, 'manual_session_blocked')
            and coordinator.manual_session_blocked(character)):
        return True
    # A global operator handoff fences every participant, including one whose
    # local coordinator has not yet refreshed its per-character session row.
    status = getattr(runtime, 'manual_handoff_status', None)
    return bool(status and status())


def _identity(value):
    if not isinstance(value, dict):
        return None
    required = ('pid', 'creation_time_100ns', 'path')
    if (type(value.get('pid')) is not int or value['pid'] <= 0
            or type(value.get('creation_time_100ns')) is not int
            or value['creation_time_100ns'] <= 0
            or not isinstance(value.get('path'), str) or not value['path']):
        return None
    return {key: value[key] for key in required}


def _ownership(snapshot):
    """Canonical ownership evidence, independent of reader list ordering."""
    inventory = snapshot.get('inventory')
    booth = snapshot.get('booth')
    if not isinstance(inventory, list) or not isinstance(booth, list):
        return None
    try:
        rows = {
            'inventory': sorted(inventory, key=lambda item: item['uid']),
            'booth': sorted(booth, key=lambda item: item['uid']),
            'capacity': snapshot['capacity'],
            'own_booth_uid': snapshot['own_booth_uid'],
        }
        encoded = json.dumps(rows, sort_keys=True, separators=(',', ':'))
    except (KeyError, TypeError, ValueError):
        return None
    return hashlib.sha256(encoded.encode()).hexdigest()


def _qualified(snapshot, profile_id):
    identity = _identity(snapshot.get('identity'))
    if snapshot.get('client_sha256') != CLIENT_SHA256_1078:
        return None, 'not_exact_1078'
    if snapshot.get('profile_id') != profile_id or not snapshot.get('profile_uid_verified'):
        return None, 'configured_merchant_identity_not_verified'
    if snapshot.get('source') != 'read_only_memory' or not snapshot.get('read_only'):
        return None, 'observation_is_not_read_only_native_memory'
    if identity is None:
        return None, 'merchant_process_identity_unavailable'
    if snapshot.get('map_id') != 1036 or type(snapshot.get('hp')) is not int or snapshot['hp'] <= 0:
        return None, 'merchant_not_living_in_market_1036'
    if not snapshot.get('closed_modal') or snapshot.get('trade_open') or snapshot.get('request_open'):
        return None, 'merchant_trade_or_request_is_open'
    ownership = _ownership(snapshot)
    if ownership is None:
        return None, 'merchant_ownership_is_unreadable'
    return (identity, ownership), None


def _incident(runtime, character):
    """Read the exact state image used by the final compare-and-swap."""
    with runtime.journal.db() as db:
        row = db.execute('SELECT value FROM state WHERE character=? AND name=?',
                         (character, KEY)).fetchone()
        pending = db.execute(
            "SELECT 1 FROM transactions WHERE character=? "
            "AND phase NOT IN ('verified','aborted','operator_overridden') LIMIT 1",
            (character,)).fetchone()
    if not row:
        return None, None, 'recovery_safety_not_active'
    try:
        state = json.loads(row[0])
    except (TypeError, ValueError):
        return None, None, 'recovery_safety_state_unreadable'
    if not (isinstance(state, dict) and state.get('active') is True
            and state.get('source') == 'unexpected_connection_loss'):
        return None, None, 'unexpected_connection_loss_watchdog_not_active'
    if pending:
        return None, None, 'unresolved_merchant_transaction'
    return state, row[0], None


def settle(runtime, character, *, now=None):
    """Settle only an authenticated, stable exact-1078 Market arrival.

    Every non-success result is a blocker and leaves the journal untouched.
    The sole write is a CAS transition of ``recovery_safety``; in particular
    this command never clears ``connect_hold`` or changes shop-return,
    enable/refill, transaction, or manual-fence state.
    """
    try:
        target = resolve_merchant(character)
        profile_id = target.profile_id
        journal_character = character_name(target)
    except (AttributeError, TypeError, ValueError) as error:
        return _blocker('merchant_profile_unavailable: ' + str(error))
    if _manual_fenced(runtime, target):
        return _blocker('manual_handoff_or_session_fence_active')
    state, encoded_before, reason = _incident(runtime, journal_character)
    if reason:
        return _blocker(reason)
    try:
        first = observe(runtime, target)
        first_evidence, reason = _qualified(first, profile_id)
        if reason:
            return _blocker(reason)
        if _manual_fenced(runtime, target):
            return _blocker('manual_handoff_or_session_fence_active')
        second = observe(runtime, target)
        second_evidence, reason = _qualified(second, profile_id)
        if reason:
            return _blocker(reason)
    except (OSError, ValueError, KeyError, TypeError) as error:
        return _blocker('native_memory_observation_failed: ' + str(error))
    if first_evidence != second_evidence:
        return _blocker('native_memory_observations_do_not_match')
    if _manual_fenced(runtime, target):
        return _blocker('manual_handoff_or_session_fence_active')

    arrived_at = time.time() if now is None else now
    identity, ownership_digest = second_evidence
    state = dict(state)
    state.update(active=False, phase='market_arrived', arrived_at=arrived_at,
                 market_arrival_1078={
                     'client_sha256': CLIENT_SHA256_1078,
                     'identity': identity,
                     'character_uid': second['character_uid'],
                     'ownership_digest': ownership_digest,
                     'observations': 2,
                 })
    encoded_after = json.dumps(state, sort_keys=True)
    with runtime.journal.db() as db:
        db.execute('BEGIN IMMEDIATE')
        pending = db.execute(
            "SELECT 1 FROM transactions WHERE character=? "
            "AND phase NOT IN ('verified','aborted','operator_overridden') LIMIT 1",
            (journal_character,)).fetchone()
        if pending:
            return _blocker('unresolved_merchant_transaction')
        changed = db.execute('UPDATE state SET value=? WHERE character=? AND name=? AND value=?',
                             (encoded_after, journal_character, KEY, encoded_before)).rowcount
        if changed != 1:
            return _blocker('recovery_safety_changed_before_settlement')
        db.execute('INSERT INTO events(character,event,payload,timestamp) VALUES(?,?,?,?)',
                   (journal_character, 'recovery_safety_market_arrived_1078',
                    json.dumps({'identity': identity, 'ownership_digest': ownership_digest,
                                'observations': 2}, sort_keys=True), arrived_at))
    return {'settled': True, 'phase': 'market_arrived', 'read_only': True,
            'input_qualified': False, 'observations': 2,
            'ownership_digest': ownership_digest}
