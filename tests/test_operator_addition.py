import copy
import json

import pytest

from conquest.merchants.delivery import (reconciliation_outcome, ownership_digest,
                                        ReconciliationBlocked)
from conquest.merchants.operator_addition import record_confirmation, STAGE
from conquest.merchants.journal import Journal


@pytest.fixture
def case(tmp_path):
    def item(uid, plus=1):
        return dict(uid=uid, type_id=130403, plus=plus, gem1=0, gem2=0,
                    quantity=1, bound=False, slot=0)
    def snapshot(name, uid, inventory):
        return dict(character=name, character_uid=uid, identity={'pid':uid},
                    timestamp=100, server='America', map_id=1036, hp=100,
                    silver=100, capacity=40, inventory=inventory, booth=[],
                    request=None, trade=None)
    f = snapshot('Parasite', 1, [item(10)])
    m = snapshot('Spiritual', 2, [item(20)])
    intent = dict(operation_id='test', farmer_profile_id='Parasite', farmer=copy.deepcopy(f),
                  merchant=copy.deepcopy(m), items=[item(10)])
    m['inventory'].append(item(30, 2))
    f['timestamp'] = m['timestamp'] = 105
    journal = Journal(tmp_path/'journal.sqlite3')
    journal.begin('test', 'Spiritual', 'farmer_delivery', intent)
    journal.step('test', 'action_trace', 'initialized')
    journal.step('test', 'trade_request', 'before_action')
    journal.transition('test', 'uncertain')
    return journal, intent, f, m


def trace(journal):
    return [{**s, 'payload':json.loads(s['payload'])} for s in journal.trace('test')]


def confirm(case, **kwargs):
    journal, _, f, m = case
    return record_confirmation(journal, 'test', f, m, [m['inventory'][-1]],
        confirmation_reference='User confirmed adding this +2 item',
        operator_confirmed=kwargs.get('operator_confirmed', True),
        sale_receipts=kwargs.get('sale_receipts', ()), now=105)


def test_manual_addition_keeps_batch_locked_until_stable_bilateral_settlement(case):
    journal, intent, f, m = case
    with pytest.raises(ReconciliationBlocked, match='surrounding inventory'):
        reconciliation_outcome(intent, f, m, trace=trace(journal), now=105)
    confirm(case)
    with pytest.raises(ReconciliationBlocked, match='terminal settlement'):
        reconciliation_outcome(intent, f, m, trace=trace(journal), now=105)
    digest = ownership_digest(intent, f, m)
    for stamp in (100, 105):
        journal.step('test', 'reconciliation_observation', 'observed',
                     dict(ownership_digest=digest, observed_at=stamp))
    result = reconciliation_outcome(intent, f, m, trace=trace(journal), now=105)
    assert result['outcome'] == 'no_transfer'
    assert [i['uid'] for i in result['remaining']] == [10]
    assert result['delivered'] == []
    assert result['operator_additions'][0]['items'][0]['uid'] == 30
    with journal.db() as db:
        assert db.execute('SELECT phase FROM transactions WHERE id=?', ('test',)).fetchone()[0] == 'uncertain'


def test_verified_sale_receipt_format_does_not_invalidate_settlement(case):
    from conquest.merchants.delivery import _sale_adjustment
    journal, intent, f, m = case
    sold = dict(m['inventory'][0], uid=50, price=100)
    intent['merchant']['booth'] = [sold]
    # This fixture models the booth at preparation; the current booth is empty.
    with journal.db() as db:
        db.execute('UPDATE transactions SET before_json=? WHERE id=?', (json.dumps(intent), 'test'))
    m['silver'] += 97
    receipts = [dict(id=1, phase='verified', observed_at=102, silver=97, items=[sold])]
    canonical = _sale_adjustment(intent, m, receipts)[2]
    digest = ownership_digest(intent, f, m, receipts)
    assert digest == ownership_digest(intent, f, m, canonical)
    confirm(case, sale_receipts=receipts)
    for stamp in (100, 105):
        journal.step('test', 'reconciliation_observation', 'observed',
                     dict(ownership_digest=digest, observed_at=stamp))
    assert reconciliation_outcome(intent, f, m, trace=trace(journal),
                                  sale_receipts=receipts, now=105)['outcome'] == 'no_transfer'


@pytest.mark.parametrize('fault', ['unconfirmed', 'offered', 'confirmed', 'missing_source',
    'currency', 'identity', 'extra_unknown', 'active_request', 'reserved_uid'])
def test_annotation_never_explains_other_changes_or_consequential_input(case, fault):
    journal, intent, f, m = case
    if fault == 'offered': journal.step('test', 'offer_item:10', 'before_action')
    if fault == 'confirmed': journal.step('test', 'farmer_confirm', 'before_action')
    if fault == 'missing_source': f['inventory'] = []
    if fault == 'currency': m['silver'] += 1
    if fault == 'identity': m['identity'] = {'pid':99}
    if fault == 'extra_unknown': m['inventory'].insert(0, {**m['inventory'][0], 'uid':40})
    if fault == 'active_request': m['request'] = {'participant':'Parasite'}
    if fault == 'reserved_uid': m['inventory'][-1]['uid'] = 10
    with pytest.raises(ValueError):
        confirm(case, operator_confirmed=fault != 'unconfirmed')
    assert not any(s['stage'] == STAGE for s in trace(journal))


@pytest.mark.parametrize('fault', ['plus', 'socket', 'binding', 'quantity', 'operation', 'duplicate'])
def test_changed_or_replayed_manual_evidence_never_reconciles(case, fault):
    journal, intent, f, m = case
    confirm(case)
    steps = trace(journal)
    receipt = next(s['payload'] for s in steps if s['stage'] == STAGE)
    if fault == 'operation': receipt['operation_id'] = 'another'
    elif fault == 'duplicate': steps.append(copy.deepcopy(steps[-1]))
    else:
        field = {'plus':'plus', 'socket':'gem1', 'binding':'bound', 'quantity':'quantity'}[fault]
        m['inventory'][-1][field] = True if fault == 'binding' else 9
    with pytest.raises(ReconciliationBlocked):
        reconciliation_outcome(intent, f, m, trace=steps, now=105)


def test_both_journals_agree_on_untouched_batch_with_operator_addition(case, tmp_path):
    from conquest.merchants.delivery import DeliveryTransaction
    from conquest.merchants.delivery_reservation import reserve, disposition, active
    journal, intent, f, m = case
    receiver = Journal(tmp_path/'receiver.sqlite3')
    reserve(receiver, 'test', intent['farmer'], intent['merchant'], intent['items'],
            origin={'operation_id':'test', 'farmer_profile_id':'Parasite'}, now=100)
    confirm(case)
    for stamp in (100, 105):
        journal.step('test', 'reconciliation_observation', 'observed',
                     dict(ownership_digest=ownership_digest(intent, f, m), observed_at=stamp))
    class Driver:
        def read_pair(self, character): return copy.deepcopy(f), copy.deepcopy(m)
    class Peer:
        def disposition(self, key, saved, outcome):
            result = disposition(receiver, 'Spiritual', key, f, m,
                                 trace=trace(journal), intent=saved, now=105)
            return {'request_id':key, **result}
    DeliveryTransaction(journal, Driver(), Peer(), clock=lambda:105).recover('test')
    with journal.db() as db:
        result = db.execute('SELECT phase,result_json FROM transactions WHERE id=?', ('test',)).fetchone()
    assert result['phase'] == 'aborted'
    assert json.loads(result['result_json'])['outcome'] == 'no_transfer'
    assert active(receiver, 'Spiritual') is None
    assert receiver.get('Spiritual', 'delivery_reservation')['phase'] == 'no_transfer_reconciled'
