"""Synthetic safety regression tests; these do not establish live qualification."""
from copy import deepcopy
import json
import threading
import time
from types import SimpleNamespace

import pytest

from conquest.memory_build_layout import CLIENT_SHA256_1078
from conquest.merchants import listing_capability_1078 as capability
from conquest.merchants import booth_listing_once_1078 as listing
from conquest.merchants import refill_1078
from conquest.merchants.journal import Journal
from conquest.merchants.refill import RefillSchedule
from conquest.merchants.listing_preflight_1078 import _MODAL_RENDER_SHA256


def item(uid=91, price=None):
    return dict(uid=uid, type_id=130805, name='Coat', plus=2, gem1=0, gem2=0,
                bound=False, quantity=1, slot=0, price=price)


def snapshot():
    return dict(client_sha256=CLIENT_SHA256_1078,
                identity={'pid': 1, 'creation_time_100ns': 2, 'path': 'C:/ImConquer.exe'},
                character='Dutch', character_uid=8, server='America', map_id=1036,
                position=[262, 211], hp=900, silver=10, capacity=40,
                inventory=[item()], booth=[], own_booth_uid=18, booth_open=True,
                trade=None, request=None, timestamp=time.time())


@pytest.fixture
def receipt(tmp_path,monkeypatch):
    monkeypatch.setattr(listing,'_profile',lambda character:SimpleNamespace(
        id='test-dutch',character_uid=8,name='Dutch',server='America'))
    journal = Journal(tmp_path/'journal.sqlite3')
    before = snapshot()
    request = dict(action='merchant-booth-list-once-1078', character='Dutch',
                   request_id='booth-list1078-synthetic01', item_uid=91,
                   item_fingerprint=listing._item_fingerprint(item()), price=73000,
                   expected_identity=before['identity'], expected_character_uid=8,
                   expected_own_booth_uid=18)
    baseline = dict(request=request, snapshot=before, profile_id='test-dutch',
                    client_sha256=CLIENT_SHA256_1078,
                    listing_engine_revision=capability.ENGINE_REVISION,
                    control={'enabled': False, 'paused': False, 'revision': 1},
                    farmer_target={'pid': 10, 'hwnd': 11})
    journal.begin(request['request_id'], 'Dutch', listing.KIND, baseline)
    stages = [('baseline', 'verified', {}), ('drag_press', 'before_action', {}),
              ('native_dialog', 'verified', {'uid': 91, 'renderer_sha256': _MODAL_RENDER_SHA256}),
              ('amount_press', 'before_action', {}), ('price', 'verified', {}),
              ('confirm_press', 'before_mouse_down', {'uid': 91, 'price': 73000, 'owned_booth_uid': 18})]
    for stage, status, payload in stages:
        journal.step(request['request_id'], stage, status, payload)
    after = deepcopy(before)
    after.update(inventory=[], booth=[item(price=73000)], timestamp=time.time())
    second = {**deepcopy(after), 'timestamp': time.time()}
    return SimpleNamespace(j=journal, before=before, first=after, second=second,
                           request=request, key=request['request_id'], baseline=baseline)


def test_verified_exact_receipt_promotes_only_same_process_open_booth_path(receipt):
    x = receipt
    capability.settle(x.j, x.key, x.first, x.second)
    # Reopen durable storage to prove restart does not depend on a cache.
    journal = Journal(x.j.path)
    state = capability.status(journal, 'Dutch', x.second)
    assert state[capability.CAPABILITY] is True
    assert state['booth_input'] is False and state['trade'] is False
    assert state['market_return'] is False and state['automatic_focus'] is False
    changed = {**x.second, 'identity': {**x.second['identity'], 'creation_time_100ns': 3}}
    with pytest.raises(ValueError, match='build_process_or_character_changed'):
        capability.require(journal, 'Dutch', changed)


@pytest.mark.parametrize('mutation', ['missing_marker', 'wrong_price', 'other_item_changed',
                                      'read_gap', 'old_engine', 'duplicate_press'])
def test_incomplete_or_ambiguous_proof_never_promotes(receipt, mutation):
    x = receipt
    if mutation == 'missing_marker':
        with x.j.db() as db:
            db.execute("DELETE FROM transaction_steps WHERE stage='confirm_press'")
    elif mutation == 'wrong_price':
        x.first['booth'][0]['price'] += 1
        x.second['booth'][0]['price'] += 1
    elif mutation == 'other_item_changed':
        x.first['inventory'] = [item(99)]
        x.second['inventory'] = [item(99)]
    elif mutation == 'read_gap':
        x.first['timestamp'] -= 10
    elif mutation == 'old_engine':
        with x.j.db() as db:
            baseline = deepcopy(x.baseline)
            baseline.pop('listing_engine_revision')
            db.execute('UPDATE transactions SET before_json=?', (json.dumps(baseline),))
    else:
        x.j.step(x.key, 'confirm_press', 'before_mouse_down', {})
    with pytest.raises(ValueError):
        capability.settle(x.j, x.key, x.first, x.second)
    assert x.j.get('Dutch', capability.STATE) is None
    assert listing._row(x.j, x.key)['phase'] == 'prepared'


def test_saved_boolean_or_legacy_qualification_cannot_unlock(receipt):
    x = receipt
    x.j.set('Dutch', capability.STATE, {'request_id': x.key, 'capability': capability.CAPABILITY,
                                      'qualified': True, 'proof_digest': 'anything'})
    assert capability.status(x.j, 'Dutch', x.before)[capability.CAPABILITY] is False


def test_receipt_tampering_is_rejected(receipt):
    x = receipt
    capability.settle(x.j, x.key, x.first, x.second)
    with x.j.db() as db:
        result = json.loads(db.execute('SELECT result_json FROM transactions').fetchone()[0])
        result['listing_capability_evidence']['identity']['pid'] += 1
        db.execute('UPDATE transactions SET result_json=?', (json.dumps(result),))
    assert capability.status(x.j, 'Dutch', x.second)[capability.CAPABILITY] is False


def make_ui(journal, state):
    schedule = RefillSchedule('Dutch', journal)
    journal.set('Dutch', 'refill', {'pending': False, 'next_check': 0, 'interval_seconds': 900,
                                   'last_checked': None, 'status': 'waiting'})
    journal.set('Dutch', 'enabled', False)
    journal.set('Dutch', 'refill_enabled', True)
    runtime = SimpleNamespace(journal=journal, listing1078_lock=threading.Lock(),
                              refills={'Dutch': schedule},
                              observers={'Dutch': SimpleNamespace(hwnd=55, adapter=SimpleNamespace(identity=state['identity']))})
    runtime.refill_enabled = lambda character: journal.get(character, 'refill_enabled', True)
    runtime.can_start_work = lambda seconds: seconds <= 20
    ui = SimpleNamespace(runtime=runtime, safe_to_yield=lambda: True,
                         app=SimpleNamespace(control=SimpleNamespace(snapshot=lambda: {'enabled': False, 'paused': False})))
    return ui


def test_scheduler_reports_farmer_on_without_sending_input(receipt, monkeypatch):
    x = receipt
    capability.settle(x.j, x.key, x.first, x.second)
    current = {**deepcopy(x.second), 'inventory': [item(92)]}
    ui = make_ui(x.j, current)
    ui.app.control.snapshot = lambda: {'enabled': True}
    monkeypatch.setattr('conquest.merchants.listing_plan_1078.plan',
                        lambda *a: [{'uid': 92, 'price': 90000}])
    monkeypatch.setattr('conquest.merchants.listing_handoff_1078.request_handoff',
                        lambda *a: 'merchant-refill:Dutch')
    monkeypatch.setattr(listing, 'dispatch', lambda *a, **k: pytest.fail('No input allowed'))
    result = refill_1078.step(ui, 'Dutch', current)
    assert result['blocker'] == 'waiting_farmer_handoff'
    assert result['handoff_request_id'] == 'merchant-refill:Dutch'
    assert ui.runtime.refills['Dutch'].state()['last_checked'] is None


def test_refill_pause_independent_of_operations(receipt, monkeypatch):
    x = receipt
    capability.settle(x.j, x.key, x.first, x.second)
    ui = make_ui(x.j, x.second)
    x.j.set('Dutch', 'refill_enabled', False)
    x.j.set('Dutch', 'enabled', True)
    monkeypatch.setattr(listing, 'dispatch', lambda *a, **k: pytest.fail('No input allowed'))
    assert refill_1078.step(ui, 'Dutch', x.second)['blocker'] == 'refill_paused_or_global_stop'


def test_prepared_receipt_after_restart_never_replays(receipt, monkeypatch):
    x = receipt
    ui = make_ui(x.j, x.before)
    state = ui.runtime.refills['Dutch'].state()
    state.update(pending=True, listing1078_engine=1, listing1078_request=x.request)
    x.j.set('Dutch', 'refill', state)
    calls = []
    monkeypatch.setattr(listing, 'reconcile', lambda *a: calls.append('read_only'))
    monkeypatch.setattr(listing, 'dispatch', lambda *a, **k: pytest.fail('Cannot replay'))
    for _ in range(2):
        assert refill_1078.step(ui, 'Dutch', x.before)['blocker'] == 'listing_receipt_needs_reconciliation'
    assert calls == ['read_only', 'read_only']


def test_refill_cursor_counts_verified_receipt_once(receipt):
    x = receipt
    capability.settle(x.j, x.key, x.first, x.second)
    ui = make_ui(x.j, x.second)
    state = ui.runtime.refills['Dutch'].state()
    state.update(pending=True, listed=2, cursor=[91, 92], listing1078_engine=1,
                 listing1078_request=x.request)
    x.j.set('Dutch', 'refill', state)
    refill_1078._settle_cursor(x.j, 'Dutch', x.key)
    refill_1078._settle_cursor(x.j, 'Dutch', x.key)
    saved = ui.runtime.refills['Dutch'].state()
    assert saved['listed'] == 3 and saved['cursor'] == [92]
    assert saved['listing1078_request'] is None and saved['pending'] is True


def test_legacy_cursor_is_not_silently_discarded(receipt):
    x = receipt
    ui = make_ui(x.j, x.before)
    state = ui.runtime.refills['Dutch'].state()
    state.update(pending=True, cursor=[91])
    x.j.set('Dutch', 'refill', state)
    assert refill_1078.step(ui, 'Dutch', x.before)['blocker'] == 'legacy_refill_cursor_needs_reconciliation'
    assert ui.runtime.refills['Dutch'].state()['cursor'] == [91]


def test_due_scheduler_uses_shared_highest_value_plan_with_operations_paused(receipt, monkeypatch):
    x = receipt
    capability.settle(x.j, x.key, x.first, x.second)
    current = {**deepcopy(x.second), 'inventory': [item(92), item(93)]}
    ui = make_ui(x.j, current)
    monkeypatch.setattr(listing, '_policy', lambda *a, **k: None)
    monkeypatch.setattr(listing, '_farmer_safe_market', lambda *a: {'pid': 10})
    from conquest import input_probe
    monkeypatch.setattr(input_probe, 'MessageTarget', lambda *a: SimpleNamespace(
        snapshot=lambda: {'foreground': 55, 'root_hwnd': 55, 'minimized': False}))
    from conquest.merchants import listing_plan_1078
    monkeypatch.setattr(listing_plan_1078, 'plan', lambda *a: [
        {'uid': 93, 'price': 90000}, {'uid': 92, 'price': None}])
    requests = []
    monkeypatch.setattr(listing, 'dispatch', lambda ui, request, **kw:
                        requests.append((request, kw)) or {'phase': 'prepared'})
    assert refill_1078.step(ui, 'Dutch', current)['state'] == 'listing_pending'
    assert requests[0][0]['item_uid'] == 93 and requests[0][0]['price'] == 90000
    assert requests[0][1] == {'scheduled_refill': True}
    assert x.j.get('Dutch', 'enabled') is False
    assert x.j.get('Dutch', 'inventory_queue') == [93, 92]
    assert ui.runtime.refills['Dutch'].state()['last_checked'] is None


def fresh_binding(x,booth_uid=38):
    current=deepcopy(x.before)
    current.update(own_booth_uid=booth_uid,profile_id='test-dutch',profile_uid_verified=True,
        closed_modal=True,timestamp=time.time(),listing_preflight={
            'layout_observed':True,'inventory_grid':{'id':1},'booth_grid':{'id':2},
            'owned_booth':{'model_key':25,'owner_uid':booth_uid,'model_owner_verified':True},
            'price_modal':{'observed':False}})
    request={**deepcopy(x.request),'request_id':'booth-list1078-new-stall',
             'expected_own_booth_uid':booth_uid}
    return current,request


def test_same_actor_new_owned_booth_retains_control_proof_and_records_current_binding(receipt):
    x=receipt;capability.settle(x.j,x.key,x.first,x.second)
    with x.j.db() as db:
        original=tuple(db.execute('SELECT before_json,result_json FROM transactions WHERE id=?',(x.key,)).fetchone())
    saved=x.j.get('Dutch',capability.STATE)
    current,request=fresh_binding(x)
    assert capability.require(x.j,'Dutch',current)['qualified_own_booth_uid']==18
    binding=capability.bind_current(x.j,'Dutch',current,request)
    assert binding['source_request_id']==x.key and binding['source_proof_digest']==saved['proof_digest']
    assert binding['qualified_own_booth_uid']==18 and binding['current_own_booth_uid']==38
    assert binding['current_request_id']==request['request_id']
    assert binding['snapshot_digest']==capability._digest(current)
    with x.j.db() as db:
        assert tuple(db.execute('SELECT before_json,result_json FROM transactions WHERE id=?',(x.key,)).fetchone())==original
    assert x.j.get('Dutch',capability.STATE)==saved


@pytest.mark.parametrize('field,value',[('identity',{'pid':2}),('character_uid',9),
                                      ('character','Spiritual'),('server','Other'),
                                      ('client_sha256','different-build')])
def test_control_receipt_cannot_cross_identity_or_build(receipt,field,value):
    x=receipt;capability.settle(x.j,x.key,x.first,x.second)
    current,request=fresh_binding(x);current[field]=value
    with pytest.raises(ValueError,match='build_process_or_character_changed'):
        capability.bind_current(x.j,'Dutch',current,request)


def test_control_receipt_cannot_cross_profile(receipt,monkeypatch):
    x=receipt;capability.settle(x.j,x.key,x.first,x.second)
    current,request=fresh_binding(x)
    monkeypatch.setattr(listing,'_profile',lambda c:SimpleNamespace(
        id='other-profile',character_uid=8,name='Dutch',server='America'))
    with pytest.raises(ValueError,match='build_process_or_character_changed'):
        capability.bind_current(x.j,'Dutch',current,request)


@pytest.mark.parametrize('change',['foreign_model','unverified_model','missing_grid','stale','closed_panel'])
def test_new_booth_requires_current_owned_model_and_live_grids(receipt,change):
    x=receipt;capability.settle(x.j,x.key,x.first,x.second)
    current,request=fresh_binding(x)
    if change=='foreign_model':current['listing_preflight']['owned_booth']['owner_uid']=99
    elif change=='unverified_model':current['listing_preflight']['owned_booth']['model_owner_verified']=False
    elif change=='missing_grid':current['listing_preflight']['booth_grid']=None
    elif change=='stale':current['timestamp']-=2
    else:current['booth_open']=False
    with pytest.raises(ValueError):capability.bind_current(x.j,'Dutch',current,request)


def test_pending_old_request_cannot_be_rebound_or_used_on_new_booth(receipt):
    x=receipt;capability.settle(x.j,x.key,x.first,x.second)
    current,request=fresh_binding(x)
    old={**deepcopy(x.request),'request_id':'booth-list1078-pending-old'}
    x.j.begin(old['request_id'],'Dutch',listing.KIND,{'request':old,'snapshot':x.before})
    redirected={**old,'expected_own_booth_uid':38}
    with pytest.raises(ValueError,match='cannot be rebound'):
        capability.bind_current(x.j,'Dutch',current,redirected)
    with pytest.raises(ValueError,match='owned booth'):
        listing._validate_snapshot(current,listing._profile('Dutch'),old)
    assert json.loads(listing._row(x.j,old['request_id'])['before_json'])['request']['expected_own_booth_uid']==18
