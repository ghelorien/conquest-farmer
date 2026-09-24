"""Read-only, exact-build preview of listings held by an old shop return.

This is evidence for a later operator/runtime decision, never an authorization
to restore a booth.  It deliberately does not use Journal.db(), which can
change SQLite journal mode, or any observer/controller with an input API.
"""
import json
import math
import sqlite3
from pathlib import Path

from conquest.character_context import resolve_merchant
from conquest.merchants.observe_1078 import observe


_ITEM_FIELDS = ('type_id', 'name', 'plus', 'gem1', 'gem2', 'bound', 'quantity')
_TERMINAL_RESERVATIONS = frozenset((
    'verified', 'cancelled_before_input', 'no_transfer_reconciled',
    'partial_aborted_reconciled', 'operator_overridden',
))


def _items(rows, source):
    if not isinstance(rows, list):
        raise ValueError(f'{source} stock is unavailable')
    result = {}
    for item in rows:
        if not isinstance(item, dict):
            raise ValueError(f'{source} stock contains an invalid item')
        uid = item.get('uid')
        if type(uid) is not int or uid <= 0 or uid in result:
            raise ValueError(f'{source} stock has ambiguous item identities')
        if (type(item.get('quantity')) is not int or item['quantity'] <= 0
                or type(item.get('bound')) is not bool
                or any(key not in item for key in _ITEM_FIELDS)):
            raise ValueError(f'{source} stock has incomplete ownership attributes')
        result[uid] = item
    return result


def _verified_listing_receipts(db, profile_id):
    """Attribute new booth stock only to complete native listing evidence."""
    from conquest.merchants.listing_capability_1078 import _proof
    receipts = []
    for request_id, before_json, result_json in db.execute(
            "SELECT id,before_json,result_json FROM transactions WHERE character=? "
            "AND kind='booth_listing_1078_once' AND phase='verified' ORDER BY created,id",
            (profile_id,)):
        before, result = json.loads(before_json), json.loads(result_json or '{}')
        proof = result.get('listing_capability_evidence')
        if not proof or result.get('exact_memory_listing_verified') is not True:
            continue
        steps = [dict(zip(('stage', 'status', 'payload'), row)) for row in db.execute(
            'SELECT stage,status,payload FROM transaction_steps WHERE transaction_id=? ORDER BY id',
            (request_id,))]
        if before.get('profile_id') != profile_id or proof != _proof(before, steps, proof['first'], proof['second']):
            raise ValueError('New booth stock listing evidence changed')
        request = before['request']
        item = next(item for item in proof['second']['booth'] if item['uid'] == request['item_uid'])
        receipts.append({'request_id': request_id, 'profile_id': profile_id,
                         'identity': proof['identity'], 'character_uid': proof['character_uid'],
                         'own_booth_uid': proof['own_booth_uid'], 'item': item,
                         'observed_at': proof['second']['timestamp']})
    return receipts


def listing_receipts(path, profile_id):
    """Read-only input to the shared live listing planner."""
    uri = Path(path).resolve().as_uri() + '?mode=ro'
    with sqlite3.connect(uri, uri=True, timeout=2) as db:
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        return _verified_listing_receipts(db, profile_id)


def _verified_sale_receipts(db, profile_id, since):
    """Read existing exact sales and their atomically recorded silver evidence."""
    from conquest.merchants.sales import net_bounds
    receipts = []
    for sale_id, at, encoded, silver in db.execute(
            "SELECT id,observed_at,items,silver FROM sales WHERE character=? "
            "AND phase='verified' AND observed_at>=? ORDER BY observed_at,id",
            (profile_id, since)):
        try:
            items = json.loads(encoded)
            _items(items, 'Verified sale')
            if (not items or any(type(i.get('price')) is not int or not 1 <= i['price'] <= 2_147_483_647
                                 for i in items)
                    or type(silver) is not int or not math.isfinite(at)):
                continue
            low, high = net_bounds(items)
            if not low <= silver <= high:
                continue
            evidence = []
            for event_id, payload in db.execute(
                    "SELECT id,payload FROM events WHERE character=? "
                    "AND event='sale_verified' AND timestamp=?", (profile_id, at)):
                event = json.loads(payload)
                started = event.get('from')
                if (event.get('items') == items and event.get('silver') == silver
                        and type(event.get('before_silver')) is int
                        and type(event.get('after_silver')) is int
                        and event['after_silver']-event['before_silver'] == silver
                        and event.get('net_bounds') == [low, high]
                        and event.get('gross') == sum(i['price'] for i in items)
                        and event.get('deduction') == event['gross']-silver
                        and type(started) in (int, float) and math.isfinite(started)
                        and since <= started < at and at-started <= 15):
                    evidence.append({'event_id': event_id, 'from': started,
                                     'before_silver': event['before_silver'],
                                     'after_silver': event['after_silver']})
            if len(evidence) == 1:
                receipts.append({'sale_id': sale_id, 'profile_id': profile_id,
                                 'observed_at': at, 'items': items, 'silver': silver,
                                 **evidence[0]})
        except (ValueError, TypeError, KeyError):
            continue  # An incomplete receipt never releases missing ownership.
    return receipts


def sale_receipts(path, profile_id, since):
    uri = Path(path).resolve().as_uri() + '?mode=ro'
    with sqlite3.connect(uri, uri=True, timeout=2) as db:
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        return _verified_sale_receipts(db, profile_id, since)


def _journal_image(path, profile_id):
    """One consistent, non-mutating SQLite image of recovery and bot work."""
    uri = Path(path).resolve().as_uri() + '?mode=ro'
    with sqlite3.connect(uri, uri=True, timeout=2) as db:
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        names = ('shop_return', 'recovery_safety', 'connect_hold',
                 'delivery_reservation', 'refill', 'held_stock_refill')
        values = {name: json.loads(value) for name, value in db.execute(
            'SELECT name,value FROM state WHERE character=? AND name IN ('
            + ','.join('?' for _ in names) + ')', (profile_id, *names))}
        values['verified_listing_receipts_1078'] = _verified_listing_receipts(db, profile_id)
        values['verified_sale_receipts'] = _verified_sale_receipts(
            db, profile_id, (values.get('shop_return') or {}).get('started_at') or 0)
        transactions = list(db.execute(
            "SELECT id,kind,phase FROM transactions WHERE character=? "
            "AND phase NOT IN ('verified','aborted','operator_overridden')",
            (profile_id,)))
        admissions = list(db.execute(
            "SELECT request_id,phase FROM delivery_admissions WHERE character=? "
            "AND phase IN ('admitted','transaction_started')", (profile_id,)))
        reservations = [(key, json.loads(state).get('phase')) for key, state in db.execute(
            'SELECT request_id,state FROM delivery_reservations WHERE character=?',
            (profile_id,))]
        db.execute('ROLLBACK')
    if transactions or admissions or any(phase not in _TERMINAL_RESERVATIONS
                                       for _, phase in reservations):
        raise ValueError('An unresolved merchant transaction or delivery reservation needs reconciliation')
    reservation = values.get('delivery_reservation') or {}
    if reservation and reservation.get('phase') not in _TERMINAL_RESERVATIONS:
        raise ValueError('An unresolved merchant delivery reservation needs reconciliation')
    return values


def _preview(snapshot, state):
    incident = state.get('shop_return') or {}
    if incident.get('phase') in (None, 'complete', 'operator_overridden'):
        raise ValueError('No unresolved shop restoration incident is available')
    before = incident.get('before') or {}
    old_identity = before.get('identity')
    if (not isinstance(old_identity, dict)
            or type(old_identity.get('pid')) is not int or old_identity['pid'] <= 0
            or type(old_identity.get('creation_time_100ns')) is not int
            or old_identity['creation_time_100ns'] <= 0
            or not isinstance(old_identity.get('path'), str) or not old_identity['path']):
        raise ValueError('Pre-disconnect merchant process identity is unavailable')
    if before.get('trade') or before.get('request'):
        raise ValueError('Pre-disconnect stock was captured with an open trade/request')
    if not snapshot['closed_modal']:
        raise ValueError('Close the live merchant trade/request before restoration preview')
    if snapshot['map_id'] != 1036 or snapshot['hp'] <= 0 or not snapshot['own_booth_uid']:
        raise ValueError('Current merchant is not alive at an owned Market booth')
    old_booth = _items(before.get('booth'), 'Pre-disconnect booth')
    old_inventory = _items(before.get('inventory'), 'Pre-disconnect inventory')
    if set(old_booth) & set(old_inventory):
        raise ValueError('Pre-disconnect stock has duplicate ownership')
    current_inventory = _items(snapshot['inventory'], 'Current inventory')
    current_booth = _items(snapshot['booth'], 'Current booth')
    if set(current_inventory) & set(current_booth):
        raise ValueError('Current stock has duplicate ownership')
    historical = {**old_inventory, **old_booth}
    current = {**current_inventory, **current_booth}
    missing = set(historical) - set(current)
    changed = {uid for uid in set(historical) & set(current)
               if any(historical[uid][key] != current[uid][key] for key in _ITEM_FIELDS)}
    sold = []
    for uid in sorted(missing):
        matches = []
        for receipt in state.get('verified_sale_receipts', []):
            if (receipt.get('profile_id') != snapshot['profile_id']
                    or not max(before.get('timestamp') or 0, incident.get('started_at') or 0)
                           <= receipt['from'] < receipt['observed_at'] <= snapshot['timestamp']):
                continue
            items = [item for item in receipt['items'] if item['uid'] == uid
                     and all(item[key] == historical[uid][key] for key in _ITEM_FIELDS)]
            if len(items) != 1:
                continue
            if uid in old_booth:
                if items[0]['price'] == old_booth[uid].get('price'):
                    matches.append({'sale': receipt})
                continue
            # Prior inventory has no historical listing price. It may leave
            # ownership only through a proved native listing followed by this
            # exact sale; an unexplained inventory loss remains a hard hold.
            listings = [listing for listing in state.get('verified_listing_receipts_1078', [])
                        if listing['profile_id'] == snapshot['profile_id']
                        and listing['identity'] == snapshot['identity']
                        and listing['character_uid'] == snapshot['character_uid']
                        and listing['own_booth_uid'] == snapshot['own_booth_uid']
                        and listing['item']['uid'] == uid
                        and max(before.get('timestamp') or 0, incident.get('started_at') or 0)
                            <= listing['observed_at'] <= receipt['from']
                        and all(listing['item'][key] == items[0][key]
                                for key in (*_ITEM_FIELDS, 'price'))]
            if listings:
                matches.append({'sale': receipt, 'listing': max(listings, key=lambda r:r['observed_at'])})
        if len(matches) == 1:
            sold.append({'uid': uid, 'item': historical[uid],
                         'origin': 'prior_booth' if uid in old_booth else 'prior_inventory',
                         'receipt': matches[0]['sale'],
                         **({'listing_receipt': matches[0]['listing']} if 'listing' in matches[0] else {})})
    if missing - {row['uid'] for row in sold} or changed:
        raise ValueError('Pre-disconnect merchant ownership differs from current stock; reconcile before restoring')
    capacity = snapshot['capacity']
    if type(capacity) is not int or len(current) > capacity or len(current_booth) > 32:
        raise ValueError('Current merchant ownership exceeds qualified capacity')
    wanted = []
    for uid, item in old_booth.items():
        if uid not in current:
            continue  # Exact durable sale evidence above already accounts for this UID.
        price = item.get('price')
        if type(price) is not int or not 1 <= price <= 2_147_483_647:
            raise ValueError('A prior booth item lacks a verified listing price')
        actual = current[uid]
        location = 'booth' if uid in current_booth else 'inventory'
        current_price = actual.get('price')
        wanted.append({'uid': uid, 'name': item['name'], 'quantity': item['quantity'],
                       'attributes': {key: item[key] for key in _ITEM_FIELDS},
                       'prior_listing_price': price, 'prior_total_price': price,
                       'current_location': location, 'current_price': current_price,
                       'already_restored': location == 'booth' and current_price == price})
    wanted.sort(key=lambda item: (-item['prior_total_price'], item['uid']))
    new_uids = set(current) - set(historical)
    new_inventory = [{'uid': uid, 'name': current_inventory[uid]['name'],
                      'quantity': current_inventory[uid]['quantity'],
                      'price': None, 'origin': 'new_or_unattributed_inventory'}
                     for uid in sorted(new_uids & set(current_inventory))]
    new_booth = sorted(new_uids & set(current_booth))
    attributed_booth = []
    for uid in new_booth:
        actual = current_booth[uid]
        matches = [receipt for receipt in state.get('verified_listing_receipts_1078', [])
                   if receipt['profile_id'] == snapshot['profile_id']
                   and receipt['identity'] == snapshot['identity']
                   and receipt['character_uid'] == snapshot['character_uid']
                   and receipt['own_booth_uid'] == snapshot['own_booth_uid']
                   and receipt['item']['uid'] == uid
                   and receipt['observed_at'] >= (incident.get('started_at') or 0)
                   and all(receipt['item'][key] == actual[key] for key in (*_ITEM_FIELDS, 'price'))]
        if not matches:
            raise ValueError('Unattributed newly listed booth stock requires reconciliation: UID '+str(uid))
        attributed_booth.append({'uid': uid, 'price': actual['price'],
                                 'listing_request_id': matches[-1]['request_id']})
    prior_inventory_now_listed = [
        {'uid': uid, 'name': old_inventory[uid]['name'],
         'current_price': current_booth[uid]['price']}
        for uid in sorted(set(old_inventory) & set(current_booth))]
    refill = state.get('refill') or {}
    cursor = refill.get('cursor') or []
    if (not isinstance(cursor, list) or any(type(uid) is not int for uid in cursor)
            or len(set(cursor)) != len(cursor)):
        raise ValueError('Refill cursor is not a trustworthy item list')
    cursor_rows = [{'uid': uid, 'origin': ('prior_booth' if uid in old_booth else
                   'prior_inventory' if uid in old_inventory else
                   'new_inventory' if uid in new_uids else 'not_currently_owned')}
                   for uid in cursor]
    safety = state.get('recovery_safety') or {}
    blockers = []
    if not snapshot['booth_open']:
        blockers.append('owned_booth_panel_closed')
    if safety.get('active') or state.get('connect_hold'):
        blockers.append('recovery_input_hold')
    if refill.get('pending'):
        blockers.append('interrupted_refill_cursor_requires_separate_reconciliation')
    if any(item['current_location'] == 'booth' and not item['already_restored']
           for item in wanted):
        blockers.append('prior_listing_price_changed')
    if prior_inventory_now_listed:
        blockers.append('previous_inventory_now_listed_separately')
    if any(item['origin'] == 'not_currently_owned' for item in cursor_rows):
        blockers.append('stale_refill_cursor')
    blockers.append('1078_listing_input_not_qualified')
    return {'character': snapshot['character'], 'profile_id': snapshot['profile_id'],
            'character_uid': snapshot['character_uid'],
            'client_sha256': snapshot['client_sha256'],
            'current_identity': snapshot['identity'],
            'previous_identity': old_identity, 'observed_at': snapshot['timestamp'],
            'map_id': snapshot['map_id'], 'own_booth_uid': snapshot['own_booth_uid'],
            'booth_open': snapshot['booth_open'], 'closed_modal': True,
            'incident_phase': incident['phase'], 'incident_started_at': incident.get('started_at'),
            'recovery_safety_phase': safety.get('phase'),
            'restore_prior_listings': wanted, 'new_inventory_without_prior_listing': new_inventory,
            'prior_stock_sold_with_verified_receipts': sold,
            'new_booth_attributed_to_verified_listings': attributed_booth,
            'prior_inventory_now_listed_separately': prior_inventory_now_listed,
            'unchanged_prior_inventory_count': (len(old_inventory) - len(prior_inventory_now_listed)
                                               - sum(row['origin']=='prior_inventory' for row in sold)),
            'refill': {'pending': bool(refill.get('pending')), 'status': refill.get('status'),
                       'last_checked': refill.get('last_checked'), 'cursor': cursor_rows,
                       'source_delivery_operation_id': refill.get('source_delivery_operation_id')},
            'blockers': blockers, 'read_only': True, 'input_qualified': False,
            'journal_unchanged': True, 'execution_authority': False}


def preview(runtime, character):
    """Authenticated bridge caller; two matching journal reads bracket memory."""
    target = resolve_merchant(character)
    profile_id = getattr(target, 'profile_id', None)
    if not profile_id:
        raise ValueError('Restoration preview requires a managed merchant profile')
    first = _journal_image(runtime.journal.path, profile_id)
    snapshot = observe(runtime, target)
    if snapshot['profile_id'] != profile_id or not snapshot['profile_uid_verified']:
        raise ValueError('Current merchant identity is not the configured profile identity')
    result = _preview(snapshot, first)
    if _journal_image(runtime.journal.path, profile_id) != first:
        raise ValueError('Merchant recovery journal changed during read-only preview')
    return result
