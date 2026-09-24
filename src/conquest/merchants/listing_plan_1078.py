"""Shared read-only price queue for supervised and recurring 1078 listings."""
from conquest.merchants.refill_preview_1078 import (
    _owned_profiles, _same_stock, _saved_prices, _queue,
)
from conquest.merchants.observe_1078 import observe
from conquest.merchants.restoration_preview_1078 import _preview
from conquest.memory_build_layout import CLIENT_SHA256_1078


class OwnedPeerUnavailable(ValueError):
    """A named owned peer could not supply the fresh price-floor proof."""

    def __init__(self, character, error):
        self.character = character
        super().__init__(f'{character} owned booth observation unavailable: {error}')


def _peer(runtime, profile):
    try:
        return observe(runtime, profile.id)
    except (ValueError, OSError, KeyError, TypeError) as error:
        raise OwnedPeerUnavailable(profile.name, error) from error


def plan(runtime, character, snapshot):
    from conquest.merchants.booth_listing_once_1078 import _profile, _item_fingerprint
    profile = _profile(character)
    if snapshot['character_uid'] != profile.character_uid or snapshot['character'] != profile.name:
        raise ValueError('Owned pricing requires the exact configured merchant')
    snapshot = {**snapshot, 'profile_id': profile.id, 'profile_uid_verified': True,
                'client_sha256': CLIENT_SHA256_1078,
                'closed_modal': snapshot.get('closed_modal',
                    snapshot.get('trade') is None and snapshot.get('request') is None)}
    profiles = _owned_profiles()
    if profile.id not in {other.id for other in profiles}:
        raise ValueError('Selected merchant is not a configured local owned profile')
    peers = [_peer(runtime, other) for other in profiles if other.id != profile.id]
    if len({source['character_uid'] for source in (snapshot, *peers)}) != len(profiles):
        raise ValueError('Owned merchant identity attribution is ambiguous')
    path = runtime.market_path.with_name('price-history.sqlite3')
    catalog, quotes = _saved_prices(path)
    names = ('shop_return', 'recovery_safety', 'connect_hold', 'refill')
    state = {key: runtime.journal.get(character, key) for key in names}
    from conquest.merchants.restoration_preview_1078 import listing_receipts, sale_receipts
    state['verified_listing_receipts_1078'] = listing_receipts(runtime.journal.path, profile.id)
    incident = state['shop_return'] or {}
    since = incident.get('started_at') or 0
    state['verified_sale_receipts'] = sale_receipts(runtime.journal.path, profile.id, since)
    restoration = (_preview(snapshot, state)
                   if incident.get('phase') not in (None, 'complete', 'operator_overridden') else None)
    if (state['connect_hold'] or (state['recovery_safety'] or {}).get('active')
            or restoration and 'prior_listing_price_changed' in restoration['blockers']):
        raise ValueError('Shop restoration has an unresolved ownership or recovery hold')
    rows = _queue(snapshot, catalog, quotes, restoration, owned_snapshots=peers)
    from conquest.valuables import DRAGONBALL_TYPES
    protected = {item['uid'] for item in snapshot['inventory'] if item['type_id'] in DRAGONBALL_TYPES}
    for row in rows:
        if row['uid'] in protected:
            row.update(total_listing_price=None, source=None,
                       reason='Protected Dragonball stock requires storage')
    rows.sort(key=lambda row: (row['total_listing_price'] is None,
                              -(row['total_listing_price'] or 0), row['uid']))
    for peer in peers:
        peer_profile = next(other for other in profiles if other.id == peer['profile_id'])
        if not _same_stock(peer, _peer(runtime, peer_profile)):
            raise ValueError('Owned peer booth changed while computing listing prices')
    if (_owned_profiles() != profiles or _saved_prices(path) != (catalog, quotes)
            or any(runtime.journal.get(character, key) != state[key] for key in names)
            or listing_receipts(runtime.journal.path, profile.id) != state['verified_listing_receipts_1078']
            or sale_receipts(runtime.journal.path, profile.id, since) != state['verified_sale_receipts']):
        raise ValueError('Owned profiles, saved prices, or restoration intent changed')
    items = {item['uid']: item for item in snapshot['inventory']}
    return [{**row, 'price': row['total_listing_price'],
             'attributes': _item_fingerprint(items[row['uid']]),
             'reference': row['source']} for row in rows]
