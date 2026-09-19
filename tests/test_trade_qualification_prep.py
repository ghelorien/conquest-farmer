from types import SimpleNamespace
import copy
import os
import threading
import time

import pytest

from conquest.merchants import trade_qualification_prep as prep


def item(uid, kind=410008, *, plus=1, slot=0, **fields):
    value = {'uid': uid, 'type_id': kind, 'plus': plus, 'gem1': 0, 'gem2': 0,
             'quantity': 1, 'bound': False, 'slot': slot, 'name': 'item'}
    value.update(fields)
    return value


def account(name, uid, inventory, *, map_id=1011, identity=None):
    identity = identity or {'pid': uid, 'creation_time_100ns': uid*100,
                            'path': f'C:/game/{name}.exe'}
    return {'character': name, 'character_uid': uid, 'identity': identity,
            'server': 'America', 'timestamp': time.time(), 'map_id': map_id,
            'hp': 100, 'silver': 1000, 'capacity': 40, 'position': [10, 10],
            'inventory': inventory, 'booth': [], 'booth_open': False,
            'own_booth_uid': 0, 'trade': None, 'request': None}


@pytest.mark.parametrize('change', [
    {'bound': True}, {'plus': 2}, {'gem1': 1}, {'gem2': 1},
    {'quantity': 2}, {'type_id': 410009}, {'type_id': 1088001},
])
def test_selected_uid_is_exactly_one_ordinary_plus_one(change):
    selected = item(7);selected.update(change)
    with pytest.raises(ValueError, match='Qualification requires'):
        prep._selected(account('Parasite', 1, [selected]), 7)


def test_corrupt_prep_journal_is_a_fail_closed_pending_hold(tmp_path, monkeypatch):
    path = tmp_path/'prep.json';path.write_text('{', encoding='utf-8')
    monkeypatch.setattr(prep, 'JOURNAL', path)
    assert prep.pending() is True
    with pytest.raises(ValueError, match='unreadable'):prep._read()


def deposit_state(farmer, bank, moved):
    return {'phase': 'deposit_pending', 'farmer': farmer, 'banked_uids': [],
            'deposit': {'item': moved, 'bag_before': list(farmer['inventory']),
                        'warehouse_before': list(bank), 'warehouse_capacity': 40}}


def test_lost_deposit_ack_requires_exact_rich_ownership_delta(tmp_path, monkeypatch):
    monkeypatch.setattr(prep, 'JOURNAL', tmp_path/'prep.json')
    selected, meteor = item(10), item(22, 1088001, plus=0, slot=1)
    before = account('Parasite', 1, [selected, meteor])
    after = account('Parasite', 1, [selected])
    state = deposit_state(before, [], meteor)
    class Loop:
        def town(self, action, **fields):
            assert (action, fields) == ('warehouse-items', {'rich': True})
            return {'items': [copy.deepcopy(meteor)], 'capacity': 40}
    send = lambda body: {'farmer': copy.deepcopy(after)}
    assert prep._reconcile_deposit(Loop(), state, send=send) is True
    assert state['phase'] == 'warehouse_opening' and state['deposit'] is None
    assert state['banked_uids'] == [22]


@pytest.mark.parametrize('mutation', [
    lambda bag, bank: bank[0].update(bound=True),
    lambda bag, bank: bank[0].update(gem1=1),
    lambda bag, bank: bank.append(item(99, 720027, plus=0)),
    lambda bag, bank: bag.append(item(98, 1088001, plus=0)),
])
def test_deposit_recovery_rejects_any_nonexact_delta(tmp_path, monkeypatch, mutation):
    monkeypatch.setattr(prep, 'JOURNAL', tmp_path/'prep.json')
    selected, meteor = item(10), item(22, 1088001, plus=0, slot=1)
    before = account('Parasite', 1, [selected, meteor])
    bag, bank = [copy.deepcopy(selected)], [copy.deepcopy(meteor)]
    mutation(bag, bank)
    state = deposit_state(before, [], meteor)
    class Loop:
        def town(self, action, **fields):return {'items': copy.deepcopy(bank), 'capacity': 40}
    with pytest.raises(ValueError, match='changed ownership unexpectedly'):
        prep._reconcile_deposit(Loop(), state,
            send=lambda body: {'farmer': account('Parasite', 1, copy.deepcopy(bag))})


def test_unchanged_rich_deposit_baseline_is_the_only_retryable_state(tmp_path, monkeypatch):
    monkeypatch.setattr(prep, 'JOURNAL', tmp_path/'prep.json')
    selected, meteor = item(10), item(22, 1088001, plus=0, slot=1)
    before = account('Parasite', 1, [selected, meteor])
    state = deposit_state(before, [], meteor)
    class Loop:
        def town(self, action, **fields):return {'items': [], 'capacity': 40}
    assert prep._reconcile_deposit(Loop(), state,
        send=lambda body: {'farmer': copy.deepcopy(before)}) is False
    assert state['phase'] == 'deposit_pending'


def test_banking_keeps_only_selected_item_with_rich_delta_receipts(tmp_path, monkeypatch):
    monkeypatch.setattr(prep, 'JOURNAL', tmp_path/'prep.json')
    selected = item(10);other = item(11, 111008, slot=1)
    meteor = item(12, 1088001, plus=0, slot=2)
    arrow = item(13, 1050002, plus=0, slot=3, quantity=5000)
    farmer = account('Parasite', 1, [selected, other, meteor, arrow])
    bank, actions = [], []
    class Loop:
        def town(self, action, **fields):
            if action == 'warehouse-items':return {'items': copy.deepcopy(bank), 'capacity': 40}
            if action == 'warehouse-deposit':
                uid=fields['uid'];actions.append(uid)
                moved=next(value for value in farmer['inventory'] if value['uid']==uid)
                farmer['inventory'].remove(moved);bank.append(copy.deepcopy(moved))
                return {'verified_in_warehouse': True, 'uid': uid}
            if action == 'warehouse-money':return {'silver': 500, 'stored_silver': 1000}
            raise AssertionError((action, fields))
    monkeypatch.setattr('conquest.banking.open_warehouse', lambda loop: None)
    monkeypatch.setattr('conquest.banking.close_warehouse', lambda loop: None)
    state={'phase':'prepared','selected_uid':10,'selected_item':copy.deepcopy(selected),
           'farmer':copy.deepcopy(farmer),'route':{'outbound':{'fare':100}},'banked_uids':[]}
    prep._bank_nonselected(Loop(), state,
        send=lambda body: {'farmer': copy.deepcopy(farmer)})
    assert actions == [11, 12] and 10 not in actions
    assert state['phase'] == 'banked' and state['banked_uids'] == [11, 12]


def test_withdraw_recovery_never_resubmits_an_unchanged_intent(tmp_path, monkeypatch):
    monkeypatch.setattr(prep, 'JOURNAL', tmp_path/'prep.json')
    state={'phase':'withdraw_submitted','withdraw':{'amount':100,
           'before':{'silver':0,'stored_silver':1000}}}
    class Loop:
        def town(self, action, **fields):return {'silver':0,'stored_silver':1000}
    monkeypatch.setattr('conquest.banking.transfer',
        lambda *args, **fields: pytest.fail('Recovery must never resubmit the withdrawal'))
    with pytest.raises(ValueError, match='needs attention'):
        prep._reconcile_withdraw(Loop(), state)


def test_withdraw_recovery_accepts_only_the_exact_saved_delta(tmp_path, monkeypatch):
    monkeypatch.setattr(prep, 'JOURNAL', tmp_path/'prep.json')
    state={'phase':'withdraw_submitted','withdraw':{'amount':100,
           'before':{'silver':0,'stored_silver':1000}}}
    class Loop:
        def town(self, action, **fields):return {'silver':100,'stored_silver':900}
    prep._reconcile_withdraw(Loop(), state)
    assert state['phase']=='warehouse_opening' and state['withdraw'] is None


@pytest.mark.parametrize('extra', [item(20,1088001,plus=0), item(21,720027,plus=0), item(22)])
def test_banked_payload_revalidation_rejects_new_protected_stock(extra):
    selected=item(10);farmer=account('Parasite',1,[selected])
    state={'farmer':copy.deepcopy(farmer),'selected_uid':10,'selected_item':selected}
    changed=copy.deepcopy(farmer);changed['inventory'].append(extra)
    with pytest.raises(ValueError, match='Loose Meteors|More than one'):
        prep._payload_is_safe(changed,state)


def test_resumed_banked_phase_revalidates_before_any_fare(tmp_path, monkeypatch):
    monkeypatch.setattr(prep,'JOURNAL',tmp_path/'prep.json')
    selected=item(10);saved=account('Parasite',1,[selected]);changed=copy.deepcopy(saved)
    changed['inventory'].append(item(20,1088001,plus=0))
    merchant=account('Spiritual',2,[],map_id=1036)
    state={'phase':'banked','work_window':'w','character':'Spiritual','origin':1011,
           'farmer':saved,'merchant':merchant,'selected_uid':10,'selected_item':selected,
           'route':{'outbound':{}},'control_revision':1,'started_at':time.time(),'route_id':'bandit'}
    fake=SimpleNamespace(refresh=lambda:None)
    monkeypatch.setattr(prep,'PrepLoop',lambda ui,state:fake)
    monkeypatch.setattr(prep,'_reserve_window',lambda ui,key:None)
    monkeypatch.setattr(prep,'_release_window',lambda ui,key:None)
    monkeypatch.setattr(prep,'pair',lambda *args,**fields:(copy.deepcopy(changed),copy.deepcopy(merchant)))
    with pytest.raises(ValueError,match='Loose Meteors'):
        prep._run(object(),state,send=lambda body:pytest.fail('No bridge/fare call is allowed'))
    assert state['phase']=='banked'


def test_completion_revalidates_payload_after_actionability_read(tmp_path, monkeypatch):
    monkeypatch.setattr(prep,'JOURNAL',tmp_path/'prep.json')
    selected=item(10);safe=account('Parasite',1,[selected],map_id=1036)
    unsafe=account('Parasite',1,[selected,item(20,1088001,plus=0)],map_id=1036)
    merchant=account('Spiritual',2,[],map_id=1036)
    state={'phase':'market','work_window':'w','character':'Spiritual','origin':1011,
           'farmer':safe,'merchant':merchant,'selected_uid':10,'selected_item':selected,
           'route':{'outbound':{}},'control_revision':1,'started_at':time.time(),'route_id':'bandit'}
    fake=SimpleNamespace(refresh=lambda:None,terrain=SimpleNamespace(map_id=1036))
    sequence=iter([(safe,merchant),(safe,merchant),(safe,merchant),(unsafe,merchant)])
    monkeypatch.setattr(prep,'PrepLoop',lambda ui,state:fake)
    monkeypatch.setattr(prep,'_reserve_window',lambda ui,key:None)
    monkeypatch.setattr(prep,'_release_window',lambda ui,key:None)
    monkeypatch.setattr(prep,'pair',lambda *args,**fields:tuple(map(copy.deepcopy,next(sequence))))
    monkeypatch.setattr('conquest.merchants.delivery_route.approach_merchant',lambda *args,**fields:True)
    send=lambda body:{'merchant_position':[10,10],'ready':True}
    with pytest.raises(ValueError,match='Loose Meteors'):
        prep._run(object(),state,send=send)
    assert state['phase']=='approaching' and 'completed_at' not in state


def test_archive_publication_is_idempotent_nonoverwriting_and_resyncs(tmp_path, monkeypatch):
    monkeypatch.setattr(prep,'JOURNAL',tmp_path/'prep.json')
    state={'phase':'completed','selected_uid':7}
    prep._archive(state);prep._archive(state)
    audit=list((tmp_path/'trade-qualification-prep-audit').glob('*.json'))
    assert len(audit)==1
    audit[0].write_text('{}',encoding='utf-8')
    with pytest.raises(ValueError,match='differs'):prep._archive(state)


def test_archive_failed_publication_never_leaves_partial_final(tmp_path, monkeypatch):
    monkeypatch.setattr(prep,'JOURNAL',tmp_path/'prep.json')
    monkeypatch.setattr(os,'link',lambda source,target:(_ for _ in ()).throw(OSError('crash')))
    with pytest.raises(OSError):prep._archive({'phase':'completed','selected_uid':8})
    directory=tmp_path/'trade-qualification-prep-audit'
    assert not list(directory.glob('*.json')) and not list(directory.glob('*.tmp'))


def test_actual_authenticated_bridge_start_does_not_self_call_deadlock(tmp_path, monkeypatch):
    from conquest.merchants.bridge import MerchantBridge, request
    from conquest.merchants.ui import UnifiedUI
    farmer=account('Parasite',1,[item(10)]);merchant=account('Spiritual',2,[],map_id=1036)
    monkeypatch.setattr(prep,'JOURNAL',tmp_path/'prep.json')
    monkeypatch.setattr(prep,'pair',lambda *args,**fields:(copy.deepcopy(farmer),copy.deepcopy(merchant)))
    monkeypatch.setattr(prep,'_run',lambda *args,**fields:None)
    monkeypatch.setattr(prep,'read_json',lambda path:{'origins':{'1011':{'outbound':{
        'verified':True,'source_map':1011,'destination_map':1036,'fare':100}}}})
    monkeypatch.setattr('conquest.merchants.farmer_preferences.permits_new_delivery',lambda *args:None)
    monkeypatch.setattr('conquest.protected_withdrawal.pending',lambda:[])
    monkeypatch.setattr('conquest.merchants.delivery_operation.pending',lambda:False)
    monkeypatch.setattr('conquest.merchants.delivery_route.pending',lambda:False)
    monkeypatch.setattr('conquest.merchants.delivery_journey.pending',lambda:False)
    monkeypatch.setattr('conquest.meteor_banking.pending',lambda:False)
    monkeypatch.setattr('conquest.storage_overflow.pending',lambda:False)
    monkeypatch.setattr('conquest.merchants.delivery_probe.previous_probe',lambda:None)
    coordinator=SimpleNamespace(stopped=False,manual_session_blocked=lambda target:False)
    app=SimpleNamespace(control=SimpleNamespace(snapshot=lambda:{'enabled':False,'paused':False,'revision':3}),
        thread=None,selected_route=SimpleNamespace(id='bandit'))
    journal=SimpleNamespace(pending=lambda name:[])
    ui=object.__new__(UnifiedUI);ui.coordinator=coordinator;ui.app=app
    ui.runtime=SimpleNamespace(journal=journal,controllers={'Spiritual':object()})
    ui.safe_to_yield=lambda:True;ui.calibrating=set();ui.delivery_workers={}
    path=tmp_path/'bridge.json';bridge=MerchantBridge(ui.dispatch,path=path)
    try:
        result=request({'action':'prepare-trade-qualification','character':'Spiritual',
                        'selected_uid':10},path=path)
        assert result['started'] is True and result['selected_uid']==10
    finally:bridge.close()


def recovery_ui(warehouse):
    observer=SimpleNamespace(lock=threading.RLock(),
        town_trade=lambda body:{'items':copy.deepcopy(warehouse),'capacity':40})
    return SimpleNamespace(trade_qualification_prep_thread=None,delivery_probe_thread=None,
        delivery_workers={},app=SimpleNamespace(observer=observer))


def test_digest_bound_input_free_override_recovers_after_crash(tmp_path, monkeypatch):
    import conquest.recovery_override as recovery
    monkeypatch.setattr(prep,'JOURNAL',tmp_path/'prep.json')
    farmer=account('Parasite',1,[item(10)]);merchant=account('Spiritual',2,[],map_id=1036)
    state={'phase':'outbound_pending','character':'Spiritual','selected_uid':10,
           'selected_item':farmer['inventory'][0],'farmer':farmer,'merchant':merchant}
    prep._durable(state);ui=recovery_ui([])
    monkeypatch.setattr(prep,'pair',lambda *args,**fields:(copy.deepcopy(farmer),copy.deepcopy(merchant)))
    preview=prep.recheck(ui);original=recovery._complete
    monkeypatch.setattr(recovery,'_complete',lambda *args,**fields:(_ for _ in ()).throw(OSError('crash')))
    with pytest.raises(OSError):
        prep.operator_override(ui,operator_confirmed=True,
            confirmation_reference=preview['incident_digest'],incident_digest=preview['incident_digest'])
    assert (tmp_path/'prep.json.override-intent.json').exists()
    monkeypatch.setattr(recovery,'_complete',original)
    recovered=prep._read()
    assert recovered['phase']=='operator_overridden' and recovered['replan_required'] is True
    assert prep.pending() is False


def test_override_rejects_changed_fresh_ownership(tmp_path, monkeypatch):
    monkeypatch.setattr(prep,'JOURNAL',tmp_path/'prep.json')
    farmer=account('Parasite',1,[item(10)]);merchant=account('Spiritual',2,[],map_id=1036)
    prep._durable({'phase':'outbound_pending','character':'Spiritual','selected_uid':10,
        'selected_item':farmer['inventory'][0],'farmer':farmer,'merchant':merchant})
    ui=recovery_ui([]);current=[farmer]
    monkeypatch.setattr(prep,'pair',lambda *args,**fields:(copy.deepcopy(current[0]),copy.deepcopy(merchant)))
    preview=prep.recheck(ui)
    current[0]=account('Parasite',1,[item(10),item(99,1050002,plus=0,quantity=5)])
    with pytest.raises(ValueError,match='ownership changed'):
        prep.operator_override(ui,operator_confirmed=True,
            confirmation_reference=preview['incident_digest'],incident_digest=preview['incident_digest'])


def test_bridge_recovery_schemas_are_exact(monkeypatch):
    from conquest.merchants.ui import UnifiedUI
    ui=object.__new__(UnifiedUI)
    monkeypatch.setattr(prep,'recheck',lambda ui:{'ok':True})
    monkeypatch.setattr(prep,'operator_override',lambda ui,**fields:fields)
    assert ui.dispatch({'action':'trade-qualification-prep-recheck'})=={'ok':True}
    with pytest.raises(ValueError,match='Unsupported'):
        ui.dispatch({'action':'trade-qualification-prep-override','operator_confirmed':True,
            'confirmation_reference':'x','incident_digest':'x','extra':True})


def test_guard_blocks_farming_while_prep_is_nonterminal(tmp_path, monkeypatch):
    from conquest.merchants import delivery_operation
    monkeypatch.setattr(prep,'JOURNAL',tmp_path/'prep.json')
    prep._durable({'phase':'outbound_pending'})
    monkeypatch.setattr('conquest.protected_withdrawal.pending',lambda:[])
    with pytest.raises(ValueError,match='Supervised trade preparation'):
        delivery_operation.guard_protected_assets()
