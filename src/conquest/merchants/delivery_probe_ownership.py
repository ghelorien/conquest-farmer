"""Read-only routing proof for a supervised probe, never delivery trust/input."""
import math

from conquest.merchants.delivery import exact_items, validate_snapshot
from conquest.merchants.manual_sessions import canonical_ownership
from conquest.recovery_override import evidence_digest


REQUEST_PHASES = {'request_submitted', 'request_verified', 'accept_submitted', 'cancel_submitted'}
TRADE_PHASES = {'accept_submitted', 'trade_open_verified', 'placement_submitted',
                'offer_verified', 'farmer_confirm_submitted', 'farmer_confirm_verified',
                'merchant_confirm_submitted', 'cancel_submitted'}


def _context(state, character, target_profile_id, farmer_profile_id, *, now):
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
    for role in ('farmer', 'merchant'):
        before = intent[role]
        original[role] = canonical_ownership(before)
        if not 0 <= started - before['timestamp'] <= 5 or before.get('map_id') != 1036:
            raise ValueError('Probe was not prepared from fresh Market participants')
        if state['phase'] != 'request_submitted':
            saved = state[role + '_after']
            saved_proof = canonical_ownership(saved, require_closed=False)
            if (any(saved_proof[field] != original[role][field] for field in
                    ('identity', 'character', 'character_uid', 'server'))
                    or not started <= saved['timestamp'] <= now
                    or saved.get('position') != before.get('position') or saved.get('map_id') != 1036):
                raise ValueError('Saved probe observation identity or chronology changed')
    farmer, merchant = intent['farmer'], intent['merchant']
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
    return intent, original, items


def _participant(snapshot, before, original, now):
    validate_snapshot(snapshot, before['character'], now)
    current = canonical_ownership(snapshot, require_closed=False)
    if (any(current[field] != original[field] for field in
            ('identity', 'character', 'character_uid', 'server'))
            or snapshot.get('position') != before.get('position')):
        raise ValueError('Probe participants or positions changed')
    return current


def _allowed_offers(state, items):
    if state['phase'] in ('accept_submitted', 'cancel_submitted'):
        return (set(),)
    if state['phase'] in ('trade_open_verified', 'placement_submitted'):
        prior = state.get('offered_uids', [])
        if (not isinstance(prior, list) or any(type(uid) is not int or uid <= 0 for uid in prior)
                or len(set(prior)) != len(prior) or not set(prior) <= set(items)):
            raise ValueError('Durable probe offered set is invalid')
        allowed = (set(prior),)
        if state['phase'] == 'placement_submitted':
            placing = state.get('placing_uid')
            if type(placing) is not int or placing not in items or placing in prior:
                raise ValueError('Durable probe placement boundary is invalid')
            allowed += (set(prior) | {placing},)
        return allowed
    return (set(items),)


def _acceptance(phase, role, trade):
    # The pinned client publishes its own accepted flag synchronously. The
    # peer acknowledgement may lag on either client; it is never authority to
    # submit an input or to claim that the other account confirmed.
    allowed = {
        ('farmer_confirm_submitted', 'farmer'): ((False, False), (True, False)),
        ('farmer_confirm_submitted', 'merchant'): ((False, False), (False, True)),
        ('farmer_confirm_verified', 'farmer'): ((True, False),),
        ('farmer_confirm_verified', 'merchant'): ((False, False), (False, True)),
        ('merchant_confirm_submitted', 'farmer'): ((True, False), (True, True)),
        ('merchant_confirm_submitted', 'merchant'): ((False, False), (False, True), (True, False), (True, True)),
    }.get((phase, role), ((False, False),))
    if (trade['accepted'], trade['other_accepted']) not in allowed:
        raise ValueError('Probe acceptance differs from its durable phase')


def _local_trade(state, intent, original, items, role, snapshot, *, now):
    current = _participant(snapshot, intent[role], original[role], now)
    other = intent['merchant' if role == 'farmer' else 'farmer']
    trade = snapshot.get('trade')
    if (snapshot.get('request') is not None or not isinstance(trade, dict)
            or trade.get('participant') != other['character']
            or type(trade.get('participant_uid')) is not int
            or trade.get('participant_uid') != other['character_uid']
            or any(type(trade.get(field)) is not int or trade[field] != 0
                   for field in ('own_silver', 'other_silver'))
            or any(type(trade.get(field)) is not bool for field in ('accepted', 'other_accepted'))
            or trade.get('server', snapshot['server']) != other['server']):
        raise ValueError('Probe open trade participants or currency changed')
    _acceptance(state['phase'], role, trade)
    own, received = exact_items(trade['own_items']), exact_items(trade['items'])
    offered, reverse = (own, received) if role == 'farmer' else (received, own)
    if (reverse or any(items.get(uid) != item for uid, item in offered.items())
            or set(offered) not in _allowed_offers(state, items)):
        raise ValueError('Live probe offer differs from its exact durable phase')
    if any(current[field] != original[role][field]
           for field in ('silver', 'booth', 'booth_open', 'own_booth_uid', 'capacity')):
        raise ValueError('Probe currency, booth ownership or capacity changed')
    inventory, before = exact_items(snapshot['inventory']), exact_items(intent[role]['inventory'])
    if role == 'farmer':
        if (set(inventory) - set(before)
                or any(before.get(uid) != item for uid, item in inventory.items())
                or set(before) - set(inventory) - set(offered)):
            raise ValueError('Farmer surrounding inventory changed')
    elif inventory != before:
        raise ValueError('Merchant surrounding inventory changed')
    if role == 'merchant':
        from conquest.merchants.capacity import available_slots
        if len(offered) > available_slots(snapshot):
            raise ValueError('Merchant lacks capacity for the durable offer')
    return current


def _saved_boundary(state, intent, original, items):
    """Validate a saved bilateral receipt at its own observation time."""
    farmer, merchant = state['farmer_after'], state['merchant_after']
    at = max(farmer['timestamp'], merchant['timestamp'])
    updated = state.get('updated_at')
    if (type(updated) not in (int, float) or not math.isfinite(updated)
            or any(not state['started_at'] <= receipt['timestamp'] <= updated
                   for receipt in (farmer, merchant))):
        raise ValueError('Saved bilateral evidence exceeds its durable write')
    if merchant.get('request') is not None:
        from conquest.merchants.delivery_request_reconciliation import ownership as request_ownership
        request_ownership(intent, farmer, merchant, now=at)
        if state['phase'] not in ('request_verified', 'accept_submitted', 'cancel_submitted'):
            raise ValueError('Open-trade phase lacks a durable bilateral trade boundary')
        return
    offered = exact_items(farmer['trade']['own_items'])
    if not set(offered) <= set().union(*_allowed_offers(state, items)):
        raise ValueError('Saved offer exceeds the durable phase')
    if state['phase'] in ('offer_verified', 'farmer_confirm_submitted', 'farmer_confirm_verified',
                          'merchant_confirm_submitted') and set(offered) != set(items):
        raise ValueError('Confirmed offer lacks its complete bilateral boundary')
    # The last complete pair may precede the current placement/confirmation.
    # Its own terms must be exact and no further advanced than the phase.
    historical = {**state, 'phase': 'trade_open_verified', 'offered_uids': list(offered)}
    accepted = farmer['trade'].get('accepted')
    if accepted:
        historical['phase'] = 'farmer_confirm_verified'
        if state['phase'] not in ('farmer_confirm_verified', 'merchant_confirm_submitted'):
            raise ValueError('Saved confirmation exceeds the durable phase')
    elif state['phase'] in ('farmer_confirm_verified', 'merchant_confirm_submitted'):
        raise ValueError('Verified confirmation lacks its bilateral boundary')
    for role, snapshot in (('farmer', farmer), ('merchant', merchant)):
        _local_trade(historical, intent, original, items, role, snapshot, now=at)
    if exact_items(merchant['trade']['items']) != offered:
        raise ValueError('Saved bilateral offered items disagree')
    boundary = state.get('accepted_at')
    if (type(boundary) not in (int, float) or not math.isfinite(boundary)
            or not state['started_at'] <= boundary <= updated):
        raise ValueError('Durable open-trade boundary is unavailable')


def local_ownership(state, character, target_profile_id, farmer_profile_id, role, snapshot, *, now):
    """Observation-only deferral while the peer cannot be read; never input proof."""
    if role not in ('farmer', 'merchant') or state.get('phase') not in TRADE_PHASES:
        raise ValueError('Probe cannot defer this local trade')
    intent, original, items = _context(state, character, target_profile_id, farmer_profile_id, now=now)
    _saved_boundary(state, intent, original, items)
    if role == 'merchant' and snapshot.get('request') is not None:
        current = _request_before_trade(state, intent, original, snapshot, now=now)
    else:
        current = _local_trade(state, intent, original, items, role, snapshot, now=now)
    return {'probe_digest': evidence_digest(state), 'modal': 'trade', 'observation_only': True,
            'target_profile_id': target_profile_id, 'farmer_profile_id': farmer_profile_id,
            'role': role, 'evidence_digest': evidence_digest(current)}


def _request_before_trade(state, intent, original, snapshot, *, now):
    """A fresh request read may precede the durable accept write it races."""
    from conquest.merchants.manual_sessions import request_fingerprint
    current = _participant(snapshot, intent['merchant'], original['merchant'], now)
    request_fingerprint(snapshot)
    request, farmer = snapshot['request'], intent['farmer']
    accepted_at = state.get('accepted_at')
    if (type(accepted_at) not in (int, float) or not math.isfinite(accepted_at)
            or not state['started_at'] <= snapshot['timestamp'] <= accepted_at <= now
            or request.get('participant') != farmer['character']
            or request.get('participant_uid') != farmer['character_uid']
            or {**current, 'request': None} != original['merchant']):
        raise ValueError('Stale request does not precede the exact bot trade')
    if any({item['uid']: item.get('slot') for item in snapshot[field]} !=
           {item['uid']: item.get('slot') for item in intent['merchant'][field]}
           for field in ('inventory', 'booth')):
        raise ValueError('Stale request inventory locations changed')
    return current


def historical_local_trade(state, character, target_profile_id, farmer_profile_id, role, snapshot):
    """Validate an admission's immutable evidence, never use it as live input."""
    at = snapshot['timestamp']
    # The current journal may have advanced since admission. Only exact selected
    # subsets and legal earlier confirmation states can describe that history.
    intent, original, items = _context(state, character, target_profile_id, farmer_profile_id,
                                       now=max(state.get('updated_at', at), state.get('accepted_at', at),
                                               state.get('finished_at', at), at))
    if snapshot.get('request') is not None:
        if role != 'merchant':raise ValueError('Farmer request is not bot-owned')
        return _request_before_trade(state, intent, original, snapshot,
                                     now=max(at, state['accepted_at']))
    if at < state['accepted_at']:
        raise ValueError('Open trade observation predates durable acceptance')
    trade = snapshot['trade']
    offered = exact_items(trade['own_items'] if role == 'farmer' else trade['items'])
    if not set(offered) <= set().union(*_allowed_offers(state, items)):
        raise ValueError('Historical offer exceeds the durable phase')
    historical = {**state, 'phase': 'trade_open_verified', 'offered_uids': list(offered)}
    farmer_accepted, merchant_accepted = ((trade.get('accepted'), trade.get('other_accepted'))
        if role == 'farmer' else (trade.get('other_accepted'), trade.get('accepted')))
    if farmer_accepted or merchant_accepted:
        if state['phase'] not in ('farmer_confirm_submitted', 'farmer_confirm_verified', 'merchant_confirm_submitted'):
            raise ValueError('Historical acceptance exceeds current phase')
        historical['phase'] = state['phase']
    return _local_trade(historical, intent, original, items, role, snapshot, now=at)


def ownership(state, character, target_profile_id, farmer_profile_id, farmer, merchant, *, now):
    """Require the durable exact incident plus fresh bilateral memory."""
    intent, original, items = _context(state, character, target_profile_id, farmer_profile_id, now=now)
    current = {role: _participant(snapshot, intent[role], original[role], now)
               for role, snapshot in (('farmer', farmer), ('merchant', merchant))}
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
            if any(not state['started_at'] <= saved['timestamp'] <= now for saved in (saved_farmer, saved_merchant)):
                raise ValueError('Verified request chronology changed')
        modal = 'request'
    else:
        if state['phase'] not in TRADE_PHASES:
            raise ValueError('Probe phase cannot own an open trade')
        _saved_boundary(state, intent, original, items)
        for role, snapshot in (('farmer', farmer), ('merchant', merchant)):
            _local_trade(state, intent, original, items, role, snapshot, now=now)
        if exact_items(farmer['trade']['own_items']) != exact_items(merchant['trade']['items']):
            raise ValueError('Bilateral probe offer differs')
        if (state['phase'] not in ('farmer_confirm_submitted', 'farmer_confirm_verified', 'merchant_confirm_submitted')
                and (farmer['trade']['accepted'] != merchant['trade']['other_accepted']
                     or farmer['trade']['other_accepted'] != merchant['trade']['accepted'])):
            raise ValueError('Bilateral probe acceptance differs')
        modal = 'trade'
    return {'probe_digest': evidence_digest(state), 'modal': modal,
            'target_profile_id': target_profile_id, 'farmer_profile_id': farmer_profile_id,
            'evidence_digest': evidence_digest(current)}
