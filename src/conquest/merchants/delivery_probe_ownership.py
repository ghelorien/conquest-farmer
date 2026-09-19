"""Read-only routing proof for a supervised probe, never delivery trust/input."""
import math

from conquest.merchants.delivery import exact_items, validate_snapshot
from conquest.merchants.manual_sessions import canonical_ownership
from conquest.recovery_override import evidence_digest


REQUEST_PHASES = {'request_submitted', 'request_verified', 'accept_submitted', 'cancel_submitted'}
TRADE_PHASES = {'accept_submitted', 'trade_open_verified', 'placement_submitted',
                'offer_verified', 'farmer_confirm_submitted', 'farmer_confirm_verified',
                'merchant_confirm_submitted', 'cancel_submitted'}


def ownership(state, character, target_profile_id, farmer_profile_id, farmer, merchant, *, now):
    """Require the durable exact incident plus fresh bilateral memory.

    Age alone cannot expire an unresolved input incident. Process rollover,
    identity/recipient changes, invalid chronology and stale *live* evidence do.
    A profile-scoped journal must explicitly bind the profile; legacy journals
    are usable only with the legacy character/``Farmer`` target keys.
    """
    if (not isinstance(state, dict) or state.get('phase') not in REQUEST_PHASES | TRADE_PHASES
            or state.get('character') != str(character)
            or state.get('target_profile_id', state.get('character')) != target_profile_id
            or state.get('farmer_profile_id', 'Farmer') != farmer_profile_id):
        raise ValueError('Probe is not bound to this active target and farmer profile')
    started = state.get('started_at')
    if type(started) not in (int, float) or not math.isfinite(started) or not 0 <= started <= now:
        raise ValueError('Probe start chronology is unavailable')
    for field in ('updated_at', 'finished_at', 'accepted_at'):
        value = state.get(field, started)
        if type(value) not in (int, float) or not math.isfinite(value) or not started <= value <= now:
            raise ValueError('Probe chronology changed')
    intent = state['intent']
    original = {}
    current = {}
    for role, snapshot in (('farmer', farmer), ('merchant', merchant)):
        before = intent[role]
        original[role] = canonical_ownership(before)
        if not 0 <= started - before['timestamp'] <= 5 or before.get('map_id') != 1036:
            raise ValueError('Probe was not prepared from fresh Market participants')
        validate_snapshot(snapshot, before['character'], now)
        current[role] = canonical_ownership(snapshot, require_closed=False)
        if (any(current[role][field] != original[role][field] for field in
                ('identity', 'character', 'character_uid', 'server'))
                or snapshot.get('position') != before.get('position')):
            raise ValueError('Probe participants or positions changed')
        if state['phase'] != 'request_submitted':
            saved = state[role + '_after']
            saved_proof = canonical_ownership(saved, require_closed=False)
            if (any(saved_proof[field] != original[role][field] for field in
                    ('identity', 'character', 'character_uid', 'server'))
                    or not started <= saved['timestamp'] <= now
                    or saved.get('position') != before.get('position') or saved.get('map_id') != 1036):
                raise ValueError('Saved probe observation identity or chronology changed')
    if (merchant['character'] != str(character) or farmer['server'] != merchant['server']
            or farmer['identity'] == merchant['identity']):
        raise ValueError('Probe participant identities are not distinct exact peers')
    recipient = state['recipient']
    if (type(recipient.get('uid')) is not int
            or not isinstance(recipient.get('position'), list)
            or len(recipient['position']) != 2
            or any(type(value) is not int or value < 0 for value in recipient['position'])
            or any(recipient.get(field) != merchant[key] for field, key in
                   (('uid', 'character_uid'), ('name', 'character'), ('position', 'position')))):
        raise ValueError('Probe recipient differs from the current merchant')
    selected = state['selected_uids']
    items = exact_items(intent['items'])
    if (not isinstance(selected, list) or not selected
            or any(type(uid) is not int or uid <= 0 for uid in selected)
            or len(set(selected)) != len(selected) or set(selected) != set(items)
            or any(exact_items(intent['farmer']['inventory']).get(uid) != item for uid, item in items.items())):
        raise ValueError('Probe selected items differ from its original inventory')
    if merchant.get('request') is not None:
        if state['phase'] not in REQUEST_PHASES:
            raise ValueError('Probe phase cannot own an incoming request')
        from conquest.merchants.delivery_request_reconciliation import ownership as request_ownership
        request_ownership(intent, farmer, merchant, now=now)
        if state['phase'] != 'request_submitted':
            saved_farmer, saved_merchant = state['farmer_after'], state['merchant_after']
            request_ownership(intent, saved_farmer, saved_merchant,
                              now=max(saved_farmer['timestamp'], saved_merchant['timestamp']))
            if any(canonical_ownership(saved, require_closed=False) != current[role]
                   for role, saved in (('farmer', saved_farmer), ('merchant', saved_merchant))):
                raise ValueError('Saved verified request evidence changed')
            if any(not started <= saved['timestamp'] <= now for saved in (saved_farmer, saved_merchant)):
                raise ValueError('Verified request chronology changed')
        modal = 'request'
    else:
        if state['phase'] not in TRADE_PHASES:
            raise ValueError('Probe phase cannot own an open trade')
        for snapshot, other in ((farmer, merchant), (merchant, farmer)):
            trade = snapshot.get('trade')
            if (snapshot.get('request') is not None or not isinstance(trade, dict)
                    or trade.get('participant') != other['character']
                    or type(trade.get('participant_uid')) is not int
                    or trade.get('participant_uid') != other['character_uid']
                    or any(type(trade.get(field)) is not int for field in ('own_silver', 'other_silver'))
                    or any(type(trade.get(field)) is not bool for field in ('accepted', 'other_accepted'))
                    or trade.get('server', snapshot['server']) != other['server']):
                raise ValueError('Probe open trade participants changed')
        from conquest.merchants.farmer_trade import partial_offer
        offered = exact_items(partial_offer(intent, farmer, merchant))
        if state['phase'] == 'accept_submitted' and offered:
            raise ValueError('Probe has unexpected items before placement')
        if state['phase'] in ('offer_verified', 'farmer_confirm_submitted',
                              'farmer_confirm_verified', 'merchant_confirm_submitted') and offered != items:
            raise ValueError('Probe requires its complete verified offer')
        for role in ('farmer', 'merchant'):
            if any(current[role][field] != original[role][field]
                   for field in ('booth', 'booth_open', 'own_booth_uid', 'capacity')):
                raise ValueError('Probe booth ownership or capacity changed')
        modal = 'trade'
    return {'probe_digest': evidence_digest(state), 'modal': modal,
            'target_profile_id': target_profile_id, 'farmer_profile_id': farmer_profile_id,
            'evidence_digest': evidence_digest(current)}
