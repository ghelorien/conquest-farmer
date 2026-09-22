"""Read-only 1078 refill queue from verified stock and durable price history.

This preview never creates a PriceHistory instance (its constructor writes a
database) and never obtains a merchant driver or input lease.
"""
import json
import math
import sqlite3
import time
from fractions import Fraction
from pathlib import Path

from conquest.character_context import resolve_merchant, state_path
from conquest.merchants.observe_1078 import observe
from conquest.merchants.pricing import ItemKey, quote_item, validate_booth_price
from conquest.merchants.refill import HistoricalComparisons
from conquest.merchants.restoration_preview_1078 import _journal_image, _preview
from conquest.valuables import require_marketable


def _saved_prices(path):
    """Read catalog and comparable quotes in one non-mutating SQLite image."""
    uri = Path(path).resolve().as_uri() + '?mode=ro'
    empty = {'rows': [], 'equipment_categories': {},
             'ambiguous_equipment_types': []}
    try:
        with sqlite3.connect(uri, uri=True, timeout=2) as db:
            db.execute('PRAGMA query_only=ON')
            db.execute('BEGIN')
            row = db.execute('SELECT data FROM catalog WHERE id=1').fetchone()
            try:
                catalog = json.loads(row[0]) if row else empty
            except (TypeError, ValueError):
                catalog = empty
            if (not isinstance(catalog, dict)
                    or not isinstance(catalog.get('rows'), list)
                    or not isinstance(catalog.get('equipment_categories'), dict)
                    or not isinstance(catalog.get('ambiguous_equipment_types'), list)):
                catalog = empty
            quotes = {}
            for encoded, raw_price, observed_at in db.execute(
                    'SELECT key,unit_price,observed_at FROM quotes'):
                try:
                    fields = json.loads(encoded)
                    fields['sockets'] = tuple(fields['sockets'])
                    key = ItemKey(**fields)
                    unit_price = Fraction(raw_price)
                    if (unit_price <= 0 or not isinstance(observed_at, (int, float))
                            or not math.isfinite(observed_at)
                            or not 0 <= observed_at <= time.time()):
                        continue
                except (KeyError, TypeError, ValueError, ZeroDivisionError):
                    continue  # An unreliable quote cannot set any listing price.
                quotes[key] = {'unit_price': str(unit_price), 'observed_at': observed_at}
            db.execute('ROLLBACK')
        return catalog, quotes
    except sqlite3.OperationalError:
        # A missing/unreadable history yields deferred items, never guessed prices.
        return empty, {}


def _same_stock(first, second):
    fields = ('character', 'character_uid', 'identity', 'server', 'map_id',
              'position', 'hp', 'silver', 'capacity', 'inventory', 'booth',
              'own_booth_uid', 'booth_open', 'closed_modal', 'profile_id',
              'profile_uid_verified', 'client_sha256')
    return all(first[key] == second[key] for key in fields)


def _queue(snapshot, catalog, quotes, restoration):
    market = HistoricalComparisons(catalog)
    prior = {row['uid']: row for row in restoration['restore_prior_listings']} if restoration else {}
    rows = []
    for item in snapshot['inventory']:
        uid = item['uid']
        row = {'uid': uid, 'name': item['name'], 'quantity': item['quantity'],
               'total_listing_price': None, 'source': None,
               'source_observed_at': None, 'reason': None}
        try:
            require_marketable(item)
            if item['type_id'] == 1088001:
                raise ValueError('Loose Meteors require verified scroll consolidation')
            if item['bound']:
                raise ValueError('Bound item')
            if uid in prior:
                # The old booth held this exact UID, quantity and attributes.
                # Its saved price is already the total listing price.
                price = prior[uid]['prior_total_price']
                if type(price) is not int or not 1 <= price <= 2_147_483_647:
                    raise ValueError('Prior booth price is outside verified memory range')
                row.update(total_listing_price=price,
                           source='verified_prior_booth_listing',
                           reason='Restore verified prior total price')
            else:
                key = market.key_for(item)
                decision = quote_item(key, (), quantity=item['quantity'], history=quotes,
                    allow_plus_conversion=100000 <= item['type_id'] < 600000)
                if decision.price is None:
                    raise ValueError(decision.reason)
                validate_booth_price(decision.price)
                row.update(total_listing_price=decision.price,
                           source='saved_comparable_price',
                           source_observed_at=decision.source_observed_at,
                           reason=decision.reason)
        except (KeyError, TypeError, ValueError) as error:
            row['reason'] = str(error)
        rows.append(row)
    rows.sort(key=lambda row: (row['total_listing_price'] is None,
                              -(row['total_listing_price'] or 0), row['uid']))
    return rows


def preview(runtime, character, *, history_path=None):
    """Authenticated bridge caller; observation and journal remain unchanged."""
    target = resolve_merchant(character)
    profile_id = getattr(target, 'profile_id', None)
    if not profile_id:
        raise ValueError('1078 refill preview requires a managed merchant profile')
    state = _journal_image(runtime.journal.path, profile_id)
    catalog, quotes = _saved_prices(history_path or state_path(
        'reports/merchants/price-history.sqlite3'))
    snapshot = observe(runtime, target)
    if not snapshot['profile_uid_verified'] or not snapshot['closed_modal']:
        raise ValueError('Configured merchant identity and closed trade/request are required')
    if snapshot['map_id'] != 1036 or snapshot['hp'] <= 0 or not snapshot['own_booth_uid']:
        raise ValueError('Merchant must be alive at an owned Market booth')
    incident = state.get('shop_return') or {}
    restoration = (_preview(snapshot, state)
                   if incident.get('phase') not in (None, 'complete', 'operator_overridden')
                   else None)
    rows = _queue(snapshot, catalog, quotes, restoration)
    fresh = observe(runtime, target)
    if not _same_stock(snapshot, fresh):
        raise ValueError('Merchant ownership changed during 1078 refill preview')
    if _journal_image(runtime.journal.path, profile_id) != state:
        raise ValueError('Merchant journal changed during 1078 refill preview')
    if _saved_prices(history_path or state_path(
            'reports/merchants/price-history.sqlite3')) != (catalog, quotes):
        raise ValueError('Saved comparable price history changed during 1078 refill preview')
    free = 32 - len(snapshot['booth'])
    known = [row for row in rows if row['total_listing_price'] is not None]
    blockers = []
    if not snapshot['booth_open']:
        blockers.append('owned_booth_panel_closed')
    if free <= 0:
        blockers.append('booth_full')
    if restoration:
        blockers.extend(restoration['blockers'])
    if not quotes:
        blockers.append('saved_comparable_prices_unavailable')
    blockers.append('1078_listing_input_not_qualified')
    return {'character': snapshot['character'], 'profile_id': profile_id,
            'identity': snapshot['identity'], 'character_uid': snapshot['character_uid'],
            'client_sha256': snapshot['client_sha256'],
            'observed_at': fresh['timestamp'], 'own_booth_uid': snapshot['own_booth_uid'],
            'booth_open': snapshot['booth_open'], 'capacity_kind': 'booth_listing_slots',
            'booth_capacity': 32, 'booth_used': len(snapshot['booth']),
            'booth_free_slots': free, 'queue': rows,
            'next_slots_if_qualified': known[:free],
            'deferred': [row for row in rows if row['total_listing_price'] is None],
            'restoration_incident_phase': incident.get('phase'),
            'blockers': list(dict.fromkeys(blockers)),
            'read_only': True, 'input_qualified': False,
            'execution_authority': False, 'journal_unchanged': True}
