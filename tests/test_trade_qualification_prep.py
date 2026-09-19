from pathlib import Path
from types import SimpleNamespace
import copy
import time

import pytest

from conquest.merchants import trade_qualification_prep as prep


def item(uid, kind=410008, *, plus=1, slot=0):
    return {'uid': uid, 'type_id': kind, 'plus': plus, 'gem1': 0, 'gem2': 0,
            'quantity': 1, 'bound': False, 'slot': slot, 'name': 'item'}


def account(name, uid, inventory, *, map_id=1011):
    return {'character': name, 'character_uid': uid, 'identity': {'pid': uid},
            'server': 'America', 'timestamp': time.time(), 'map_id': map_id,
            'hp': 100, 'silver': 1000, 'capacity': 40, 'position': [10, 10],
            'inventory': inventory, 'booth': [], 'trade': None, 'request': None}


@pytest.mark.parametrize('change', [
    {'bound': True}, {'plus': 2}, {'gem1': 1}, {'gem2': 1},
    {'quantity': 2}, {'type_id': 410009}, {'type_id': 1088001},
])
def test_selected_uid_is_exactly_one_ordinary_plus_one(change):
    selected = item(7)
    selected.update(change)
    with pytest.raises(ValueError, match='Qualification requires'):
        prep._selected(account('Parasite', 1, [selected]), 7)


def test_corrupt_prep_journal_is_a_fail_closed_pending_hold(tmp_path, monkeypatch):
    path = tmp_path/'prep.json'
    path.write_text('{', encoding='utf-8')
    monkeypatch.setattr(prep, 'JOURNAL', path)
    assert prep.pending() is True
    with pytest.raises(ValueError, match='unreadable'):
        prep._read()


def test_lost_deposit_ack_reconciles_from_fresh_uid_ownership(tmp_path, monkeypatch):
    monkeypatch.setattr(prep, 'JOURNAL', tmp_path/'prep.json')
    pending = {'uid': 22, 'type_id': 1088001, 'plus': 0, 'amount': 1}
    state = {'phase': 'deposit_pending', 'pending_item': pending,
             'banked_uids': [], 'updated_at': 0}
    class Loop:
        def town(self, action, **fields):
            if action == 'supplies': return {'items': []}
            if action == 'warehouse-items': return {'items': [copy.deepcopy(pending)]}
            raise AssertionError(action)
    assert prep._reconcile_deposit(Loop(), state) is True
    assert state['phase'] == 'warehouse_opening'
    assert state['pending_item'] is None
    assert state['banked_uids'] == [22]


def test_pending_deposit_still_carried_is_safe_to_retry(tmp_path, monkeypatch):
    monkeypatch.setattr(prep, 'JOURNAL', tmp_path/'prep.json')
    pending = {'uid': 22, 'type_id': 1088001, 'plus': 0, 'amount': 1}
    state = {'phase': 'deposit_pending', 'pending_item': pending, 'banked_uids': []}
    class Loop:
        def town(self, action, **fields):
            if action == 'supplies': return {'items': [copy.deepcopy(pending)]}
            if action == 'warehouse-items': return {'items': []}
            raise AssertionError(action)
    assert prep._reconcile_deposit(Loop(), state) is False
    assert state['phase'] == 'deposit_pending'


def test_banking_keeps_only_selected_delivery_item_and_never_deposits_it(tmp_path, monkeypatch):
    monkeypatch.setattr(prep, 'JOURNAL', tmp_path/'prep.json')
    selected = item(10, slot=0)
    other = item(11, 111008, slot=1)
    meteor = item(12, 1088001, plus=0, slot=2)
    bag = [
        {**selected, 'amount': 1},
        {**other, 'amount': 1},
        {**meteor, 'amount': 1},
        {'uid': 13, 'type_id': 1050002, 'plus': 0, 'amount': 5000, 'slot': 3},
    ]
    bank, actions = [], []
    class Loop:
        def town(self, action, **fields):
            if action == 'supplies': return {'items': copy.deepcopy(bag), 'silver': 500}
            if action == 'warehouse-items': return {'items': copy.deepcopy(bank), 'capacity': 40}
            if action == 'warehouse-deposit':
                uid = fields['uid']; actions.append(uid)
                moved = next(value for value in bag if value['uid'] == uid)
                bag.remove(moved); bank.append(copy.deepcopy(moved))
                return {'verified_in_warehouse': True, 'uid': uid}
            if action == 'warehouse-money': return {'silver': 500, 'stored_silver': 1000}
            raise AssertionError(action)
    monkeypatch.setattr('conquest.banking.open_warehouse', lambda loop: None)
    monkeypatch.setattr('conquest.banking.close_warehouse', lambda loop: None)
    source = account('Parasite', 1, [selected,
        {'uid': 13, 'type_id': 1050002, 'plus': 0, 'gem1': 0, 'gem2': 0,
         'quantity': 5000, 'bound': False, 'slot': 3, 'name': 'SpeedArrow'}])
    state = {'phase': 'prepared', 'selected_uid': 10, 'selected_item': selected,
             'farmer': source, 'route': {'outbound': {'fare': 100}}, 'banked_uids': []}
    prep._bank_nonselected(Loop(), state,
        send=lambda body: {'farmer': copy.deepcopy(source)})
    assert actions == [11, 12]
    assert 10 not in actions
    assert state['phase'] == 'banked'
    assert state['banked_uids'] == [11, 12]


def test_bridge_schema_for_prep_is_exact(monkeypatch):
    from conquest.merchants.ui import UnifiedUI
    ui = object.__new__(UnifiedUI)
    called = []
    monkeypatch.setattr(prep, 'start',
        lambda ui, character, selected_uid: called.append((character, selected_uid)) or {'started': True})
    assert ui.dispatch({'action': 'prepare-trade-qualification',
                        'character': 'Spiritual', 'selected_uid': 123}) == {'started': True}
    assert called == [('Spiritual', 123)]
    with pytest.raises(ValueError, match='Unsupported'):
        ui.dispatch({'action': 'prepare-trade-qualification', 'character': 'Spiritual',
                     'selected_uid': 123, 'extra': True})


def test_guard_blocks_farming_while_prep_is_nonterminal(tmp_path, monkeypatch):
    from conquest.merchants import delivery_operation
    monkeypatch.setattr(prep, 'JOURNAL', tmp_path/'prep.json')
    prep._durable({'phase': 'outbound_pending'})
    monkeypatch.setattr('conquest.protected_withdrawal.pending', lambda: [])
    with pytest.raises(ValueError, match='Supervised trade preparation'):
        delivery_operation.guard_protected_assets()
