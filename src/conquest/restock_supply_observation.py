"""Observe one unexplained normal-arrow addition after fully settled storage.

This records current ownership, never an acquisition, purchase or operator receipt.
It cannot explain a missing asset or authorize replay of any town transaction.
"""
from copy import deepcopy
import json
import time


def _owned(bag):
    from conquest.restock_town_recovery import _ownership
    result = _ownership(bag)
    # The ordinary ownership helper excludes durability limits. Here even those
    # must be unchanged; only compacted bag slots and read times may differ.
    result['limits'] = sorted((i['uid'], i.get('limit')) for i in bag['items'])
    return json.loads(json.dumps(result, sort_keys=True))


def _addition(expected, current, forbidden):
    from conquest.arrow_upgrades import NORMAL_ARROWS, ARROW_REFILL_AMOUNTS, MAX_ARROW_PACKS
    old = {i['uid'] for i in expected['items']}
    extra = [i for i in current['items'] if i['uid'] not in old]
    without = dict(current, items=[i for i in current['items'] if i['uid'] in old])
    if len(extra) != 1 or _owned(without) != _owned(expected):
        raise ValueError('Supply observation cannot explain changed or missing original ownership')
    item = extra[0]
    kind = item.get('type_id'); amount = item.get('amount'); limit = item.get('limit')
    if (item['uid'] in forbidden or kind not in NORMAL_ARROWS or item.get('plus') != 0
            or type(amount) is not int or type(limit) is not int
            or not 0 < amount <= limit
            or limit != ARROW_REFILL_AMOUNTS[kind] // MAX_ARROW_PACKS):
        raise ValueError('Unexpected addition is not one exact normal arrow pack')
    return item


def _safe(loop, target):
    from conquest.restock_town_recovery import _native_tail_safe
    from conquest.merchants.bridge import request
    _native_tail_safe(loop, target, loop.route.restock_map_id)
    health = loop.health(); controls = health.get('embedded_controls') or {}
    control = controls.get('control') or {}
    status = request({'action': 'status'})
    manual = request({'action': 'manual-status'})
    farmer = manual.get('farmer') or {}
    if (health.get('target') != target or control.get('enabled') is not False
            or type(control.get('revision')) is not int or control.get('paused')
            or controls.get('manual_input_fence') or controls.get('manual_mouse')
            or controls.get('external_execution') is not False
            or status.get('input_owner') is not None
            or status.get('handoff_granted') is not False
            or farmer.get('session') or farmer.get('input_fenced')
            or manual.get('sessions') or manual.get('handoff') is not None):
        raise ValueError('Unexpected supply observation lacks exclusive stopped ownership')
    return control['revision']


def observe_addition(loop, row, claim, meteor, expected):
    """Return an augmented expectation only after durable, read-only proof."""
    from conquest.character_context import farmer_name
    from conquest.memory_health import HealthWorkerSession
    from conquest.merchants.reader_1078 import CLIENT_SHA256_1078
    from conquest.merchants.trade_reader_1078 import manual_ownership
    from conquest.merchants.manual_sessions import canonical_ownership
    from conquest.restock_town_recovery import _save_tail

    prior = claim.get('unexpected_supply_addition')
    before = meteor.get('return_before')
    submitted = meteor.get('return_submitted_at')
    completed = meteor.get('completed_at')
    if (claim.get('phase') != 'captured' or meteor.get('phase') != 'completed'
            or not isinstance(before, dict) or type(submitted) not in (int, float)
            or type(completed) not in (int, float)
            or not claim['captured_at'] <= meteor['market_verified_at'] <= submitted <= completed):
        raise ValueError('Unexpected supply lacks its durable completed return boundary')
    forbidden = {i['uid'] for i in claim['bag']['items']}
    forbidden.update(i['uid'] for i in meteor['after']['items'])
    forbidden.update(i['uid'] for i in (meteor.get('before') or {}).get('items', []))
    forbidden.update(uid for proof in claim.get('delivery_proofs', []) for uid in proof['uids'])
    ammo = expected.get('equipped_ammo')
    if ammo:
        forbidden.add(ammo['uid'])
    # return_before precedes the fare; compare at that boundary's wallet value.
    pre_fare = dict(expected, silver=claim['bag']['silver'])
    addition = _addition(pre_fare, before, forbidden)
    augmented = deepcopy(expected)
    augmented['items'].append(deepcopy(addition))
    if prior and (prior.get('kind') != 'unexpected_supply_addition'
                  or prior.get('origin') != 'unknown'
                  or prior.get('town_visit_id') != row['town_visit_id']
                  or prior.get('target') != claim['target']
                  or prior.get('return_submitted_at') != submitted
                  or prior.get('expected') != _owned(augmented)):
        raise ValueError('Recorded unexpected supply observation changed')
    session = HealthWorkerSession(loop.info, CLIENT_SHA256_1078)
    observations = []
    revision = _safe(loop, claim['target'])
    for _ in range(2):
        began = time.time()
        bag = loop.town('supplies')
        rich = manual_ownership(session, farmer_name())
        canonical = canonical_ownership(rich)
        if (_safe(loop, claim['target']) != revision or began < completed
                or not began <= rich['timestamp'] <= time.time()
                or not 0 <= time.time() - rich['timestamp'] <= 3
                or rich['identity'] != claim['target'] or rich['character'] != farmer_name()
                or rich['map_id'] != loop.route.restock_map_id or rich.get('hp', 0) <= 0
                or rich.get('canonical_manual_ownership') is not True
                or rich['booth'] or rich['booth_open'] or rich['own_booth_uid']
                or _owned(bag) != _owned(augmented)
                or rich['silver'] != bag['silver'] or rich['capacity'] != bag['capacity']):
            raise ValueError('Unexpected supply ownership is not freshly stable and closed')
        basic = {i['uid']: i for i in bag['items']}
        detailed = {i['uid']: i for i in rich['inventory']}
        if set(basic) != set(detailed):
            raise ValueError('Independent inventory readers disagree on supply ownership')
        for uid, item in basic.items():
            peer = detailed[uid]
            if (any(item[k] != peer[k] for k in ('uid', 'type_id', 'plus'))
                    or peer['quantity'] != item['amount']
                    or any(k in item and item[k] != peer[k] for k in ('gem1', 'gem2', 'bound'))):
                raise ValueError('Independent supply attributes disagree')
        peer = detailed[addition['uid']]
        if peer['gem1'] != 0 or peer['gem2'] != 0 or peer['bound'] is not False:
            raise ValueError('Unexpected arrow has special or unknown attributes')
        observations.append({'observed_at': began, 'bag': bag, 'ownership': rich,
                             'canonical': canonical})
    first, second = observations
    if (first['canonical'] != second['canonical']
            or first['ownership']['position'] != second['ownership']['position']
            or not first['ownership']['timestamp'] < second['ownership']['timestamp']
            or prior and prior.get('canonical') != second['canonical']):
        raise ValueError('Unexpected supply changed between independent observations')
    if not prior:
        claim['unexpected_supply_addition'] = {
            'kind': 'unexpected_supply_addition', 'origin': 'unknown',
            'is_transaction_receipt': False, 'town_visit_id': row['town_visit_id'],
            'target': claim['target'], 'return_submitted_at': submitted,
            'return_before': deepcopy(before), 'item': deepcopy(addition),
            'expected': _owned(augmented), 'canonical': second['canonical'],
            'observations': observations, 'observed_at': time.time(),
        }
        _save_tail(loop.town_visit, row)
    return augmented
