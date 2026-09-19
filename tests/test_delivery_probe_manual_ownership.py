from copy import deepcopy
import threading
from types import SimpleNamespace as NS

import pytest

from conquest.merchants import delivery_probe as probe
from conquest.merchants.delivery_probe_ownership import ownership
from conquest.merchants.manual_operator import status_text
from conquest.merchants.manual_sessions import ManualSessionError
from test_manual_runtime import rig, snapshot


@pytest.fixture
def supervised(rig, monkeypatch, tmp_path):
    x = rig
    x.state['request'] = {'participant': 'Parasite', 'participant_uid': 55,
                          'message': 'Parasite wishes to trade with you.'}
    x.farmer = snapshot(character='Parasite', character_uid=55, request=None,
                       identity={'pid': 8, 'creation_time_100ns': 19, 'path': 'C:/Game/ImConquer.exe'},
                       booth=[], booth_open=False, own_booth_uid=0)
    # Distinct inventory owners; the source item is the exact selected payload.
    x.farmer['inventory'][0]['uid'] = 99
    x.probe = {'phase': 'request_verified', 'character': 'Dutch',
               'target_profile_id': 'Dutch', 'farmer_profile_id': 'Farmer',
               'started_at': 100, 'updated_at': 100, 'selected_uids': [99],
               'recipient': {'uid': 123, 'name': 'Dutch', 'position': [210, 210]},
               'intent': {'farmer': deepcopy(x.farmer),
                          'merchant': {**deepcopy(x.state), 'request': None},
                          'items': deepcopy(x.farmer['inventory'])},
               'farmer_after': deepcopy(x.farmer), 'merchant_after': deepcopy(x.state)}
    x.path = tmp_path / 'probe.json'
    monkeypatch.setattr(probe, 'JOURNAL', x.path)
    x.save = lambda: probe.write_probe(x.path, x.probe)
    x.save()
    x.farmer_read = lambda: {**deepcopy(x.farmer), 'timestamp': x.now}
    x.source = NS(character='Parasite', lock=threading.RLock(),
                  adapter=NS(identity=deepcopy(x.farmer['identity']),assert_identity=lambda: None))
    x.runtime.manual_farmer_provider = lambda: x.source
    class Memory:
        def __init__(self, observer):assert observer is x.source
        def read(self, *, farmer_preflight=False):
            assert farmer_preflight
            return x.farmer_read()
    monkeypatch.setattr('conquest.merchants.memory.MerchantMemory', Memory)
    monkeypatch.setattr('conquest.merchants.manual_farmer.MerchantMemory', Memory)
    monkeypatch.setattr('conquest.merchants.unrelated_request.decline_unrelated_request',
                        lambda *a, **kw: pytest.fail('Probe routing must never send decline input'))
    return x


def test_exact_probe_precedes_admission_without_granting_delivery_trust(supervised, monkeypatch):
    x = supervised
    monkeypatch.setattr('conquest.character_context.trusted_delivery', lambda *a, **kw: False)
    x.controller.reconcile = lambda *a: pytest.fail('Supervised probe is not an ordinary transaction')
    x.runtime.step('Dutch')
    assert x.runtime.manual_status('Dutch') is None
    assert x.calls == [] and x.runtime.manual_sessions.permissions() == []
    assert x.journal.get('Dutch', 'enabled') is True
    assert x.path.read_text() and x.probe['phase'] == 'request_verified'


@pytest.mark.parametrize('phase', ['request_submitted', 'request_verified'])
@pytest.mark.parametrize('entry', ['step', 'manual', 'probe'])
def test_peer_read_completion_time_does_not_create_false_manual_admission(supervised, phase, entry):
    x=supervised
    x.probe['phase']=phase;x.save()
    def read_source():
        # Native MerchantMemory timestamps at the end of its read, later than
        # the caller's entry time even while Farming Off.
        x.now+=.2
        return {**deepcopy(x.farmer),'timestamp':x.now}
    x.farmer_read=read_source
    if entry=='step':x.runtime.step('Dutch')
    elif entry=='manual':assert x.runtime.process_manual('Dutch',x.read())
    else:assert x.runtime.process_probe_owned('Dutch',x.read())
    assert x.runtime.manual_status('Dutch') is None
    assert x.calls==[] and x.runtime.manual_sessions.permissions()==[]


def test_farmer_probe_validation_uses_completion_time_of_merchant_read(supervised):
    x=supervised
    open_trade(x)
    def read_merchant():
        x.now+=.2
        return x.read()
    x.driver.read=read_merchant
    assert x.runtime.process_probe_owned('Farmer',x.farmer_read())
    assert x.runtime.manual_status('Farmer') is None and x.calls==[]


def test_explicit_validation_time_is_not_silently_advanced(supervised):
    x=supervised
    def read_source():
        x.now+=.2
        return {**deepcopy(x.farmer),'timestamp':x.now}
    x.farmer_read=read_source
    assert not x.runtime.process_probe_owned('Dutch',x.read(),now=x.now)
    assert x.runtime.manual_status('Dutch') is None and x.calls==[]


@pytest.mark.parametrize('read_fails', [False, True])
def test_slow_peer_read_does_not_refresh_expired_local_evidence(supervised, read_fails):
    x=supervised
    def read_source():
        x.now+=6
        if read_fails:raise OSError('slow failed read')
        return {**deepcopy(x.farmer),'timestamp':x.now}
    x.farmer_read=read_source
    # Neither the bilateral nor structural fallback may validate the local
    # sample at its pre-read age after six real seconds have elapsed.
    assert not x.runtime.process_probe_owned('Dutch',x.read())
    assert x.runtime.manual_status('Dutch') is None and x.calls==[]


def test_restart_rebinds_same_attached_observer_without_changing_farming_off(supervised):
    from conquest.merchants.runtime import MerchantRuntime
    x=supervised
    restarted=MerchantRuntime(object(),x.guard,journal=x.journal)
    # An exact saved verified receipt only reserves observation during startup;
    # it supplies no current bilateral proof or input authority.
    assert restarted.process_probe_owned('Dutch',x.read())
    assert not restarted.process_probe_owned('Dutch',x.read(),require_bilateral=True)
    intent={'enabled':False,'paused':False,'revision':7}
    restarted.configure_manual_farmer(lambda:x.source,lambda:dict(intent))
    def read_source():
        x.now+=.2
        return {**deepcopy(x.farmer),'timestamp':x.now}
    x.farmer_read=read_source
    assert restarted.process_manual('Dutch',x.read())
    assert restarted.manual_status('Dutch') is None
    assert intent=={'enabled':False,'paused':False,'revision':7} and x.calls==[]


def test_advancing_clock_never_hides_a_different_exact_request(supervised):
    x=supervised
    x.state['request']['participant_uid']+=1
    def read_source():
        x.now+=.2
        return {**deepcopy(x.farmer),'timestamp':x.now}
    x.farmer_read=read_source
    assert x.runtime.process_manual('Dutch',x.read())
    assert x.runtime.manual_status('Dutch')['phase']=='approval_pending'
    assert x.calls==[]


def test_observer_contention_retains_existing_fence_and_never_grants_bilateral_proof(supervised):
    x=supervised
    row=x.runtime.manual_sessions.begin_request('Dutch',x.read(),now=x.now)
    x.runtime._sync_manual_fence()
    x.source.lock=NS(acquire=lambda **_kwargs:False,release=lambda:None)
    x.now+=.2
    assert x.runtime.process_probe_owned('Dutch',x.read())
    assert not x.runtime.process_probe_owned('Dutch',x.read(),require_bilateral=True)
    assert x.runtime.manual_sessions.get(row['id'])['holds_automation']
    assert x.guard.manual_session_blocked('Dutch') and x.calls==[]


@pytest.mark.parametrize('failure', ['peer_lock', 'peer_read'])
def test_unresolved_exact_probe_structurally_suppresses_manual_admission_on_transient_peer_failure(
        supervised, monkeypatch, failure):
    """A failed peer read is not permission to mint a competing manual hold."""
    x=supervised
    # The request may remain unresolved after a recoverable delivery error;
    # durable verified snapshots are intentionally old, while local memory is
    # fresh at this repeated polling boundary.
    x.now=10_000
    x.probe.update(error='Character was paused during input', finished_at=x.now)
    x.save()
    assert x.runtime._structural_request_probe_owned('Dutch',x.read(),probe.read_probe(),now=x.now)
    if failure=='peer_lock':
        x.source.lock=NS(acquire=lambda **_kwargs:False,release=lambda:None)
    else:
        class FailingMemory:
            def __init__(self,_observer):pass
            def read(self,**_kwargs):raise OSError('observer temporarily busy')
        monkeypatch.setattr('conquest.merchants.memory.MerchantMemory',FailingMemory)
    for _ in range(3):
        assert x.runtime.process_manual('Dutch',x.read(),decline_enabled=True,now=x.now)
    assert x.runtime.manual_status('Dutch') is None
    assert x.calls==[] and not x.runtime.manual_sessions.permissions()


def test_transient_probe_precedence_never_suppresses_different_visitor(supervised):
    x=supervised
    x.source.lock=NS(acquire=lambda **_kwargs:False,release=lambda:None)
    x.state['request'].update(participant='Visitor',participant_uid=777,
                              message='Visitor wishes to trade with you.')
    assert x.runtime.process_manual('Dutch',x.read(),now=x.now)
    assert x.runtime.manual_status('Dutch')['phase']=='approval_pending'
    assert x.calls==[]


def test_structural_probe_precedence_allows_unselected_farmer_inventory(supervised):
    x=supervised
    extra={**deepcopy(x.farmer['inventory'][0]),'uid':100}
    for where in (x.farmer['inventory'],x.probe['intent']['farmer']['inventory'],x.probe['farmer_after']['inventory']):
        where.append(deepcopy(extra))
    x.save();x.source.lock=NS(acquire=lambda **_kwargs:False,release=lambda:None)
    assert x.runtime.process_manual('Dutch',x.read(),now=x.now)
    assert x.runtime.manual_status('Dutch') is None and x.calls==[]


def test_structural_probe_precedence_rejects_rebound_farmer_before_peer_read(supervised):
    x=supervised
    x.source.adapter.identity={**x.source.adapter.identity,'pid':999}
    x.source.lock=NS(acquire=lambda **_kwargs:False,release=lambda:None)
    assert x.runtime.process_manual('Dutch',x.read(),now=x.now)
    assert x.runtime.manual_status('Dutch')['phase']=='approval_pending'


@pytest.mark.parametrize('fault',['stale_intent','future_accepted'])
def test_structural_probe_precedence_rejects_malformed_durable_chronology(supervised,fault):
    x=supervised;x.source.lock=NS(acquire=lambda **_kwargs:False,release=lambda:None)
    if fault=='stale_intent':x.probe['intent']['farmer']['timestamp']=90
    else:x.probe['accepted_at']=101
    x.save()
    assert x.runtime.process_manual('Dutch',x.read(),now=x.now)
    assert x.runtime.manual_status('Dutch')['phase']=='approval_pending'


@pytest.mark.parametrize('change',['silver','slot'])
def test_request_submitted_structural_precedence_requires_exact_live_merchant_ownership(supervised,change):
    x=supervised
    x.probe['phase']='request_submitted'
    x.probe.pop('farmer_after');x.probe.pop('merchant_after');x.save()
    x.source.lock=NS(acquire=lambda **_kwargs:False,release=lambda:None)
    if change=='silver':x.state['silver']+=1
    else:x.state['inventory'][0]['slot']=5
    assert x.runtime.process_manual('Dutch',x.read(),now=x.now)
    assert x.runtime.manual_status('Dutch')['phase']=='approval_pending'


@pytest.mark.parametrize('phase', ['request_submitted', 'request_verified', 'accept_submitted', 'cancel_submitted'])
def test_request_stages_are_owned_even_when_incident_is_old(supervised, phase):
    x = supervised
    x.probe['phase'] = phase
    x.save()
    x.now = 10_000
    assert x.runtime.process_manual('Dutch', x.read(), decline_enabled=True)
    assert x.runtime.manual_status('Dutch') is None and x.calls == []


@pytest.mark.parametrize('fault', [
    'missing', 'corrupt', 'list', 'terminal', 'unknown_phase', 'prepared',
    'wrong_character', 'wrong_target_profile', 'wrong_farmer_profile',
    'wrong_uid', 'wrong_name', 'wrong_message', 'wrong_server',
    'live_wrong_message', 'live_request_server', 'live_bool_uid', 'stale_merchant',
    'merchant_process', 'farmer_process', 'merchant_character_uid', 'farmer_character_uid',
    'merchant_position', 'farmer_position', 'recipient_uid', 'recipient_name', 'recipient_position',
    'selected_uid', 'missing_intent', 'missing_start', 'future_start', 'past_update',
    'future_update', 'nan_update', 'stale_farmer', 'wrong_source_observer',
    'farmer_trade', 'stock_changed', 'source_server', 'journal_changes',
    'missing_saved_request', 'saved_request_changed', 'saved_process_changed',
    'saved_request_future',
])
def test_nonmatching_journal_never_suppresses_a_real_manual_request(supervised, monkeypatch, fault):
    x = supervised
    if fault == 'terminal':x.probe['phase'] = 'delivery_verified'
    if fault == 'unknown_phase':x.probe['phase'] = 'unknown'
    if fault == 'prepared':x.probe['phase'] = 'prepared'
    if fault == 'wrong_character':x.probe['character'] = 'Spiritual'
    if fault == 'wrong_target_profile':x.probe['target_profile_id'] = 'other-profile'
    if fault == 'wrong_farmer_profile':x.probe['farmer_profile_id'] = 'other-source'
    if fault == 'wrong_uid':x.state['request']['participant_uid'] += 1
    if fault == 'live_bool_uid':x.state['request']['participant_uid'] = True
    if fault == 'wrong_name':
        x.state['request'].update(participant='Visitor', message='Visitor wishes to trade with you.')
    if fault == 'wrong_message':x.probe['intent']['farmer']['character'] = 'Visitor'
    if fault == 'live_wrong_message':x.state['request']['message'] = 'Unrelated confirmation'
    if fault == 'live_request_server':x.state['request']['server'] = 'other-server'
    if fault == 'stale_merchant':x.read = lambda: {**deepcopy(x.state), 'timestamp': 90}
    if fault == 'wrong_server':x.probe['intent']['merchant']['server'] = 'other-server'
    if fault == 'merchant_process':x.probe['intent']['merchant']['identity']['creation_time_100ns'] += 1
    if fault == 'farmer_process':x.farmer['identity']['creation_time_100ns'] += 1
    if fault == 'merchant_character_uid':x.probe['intent']['merchant']['character_uid'] += 1
    if fault == 'farmer_character_uid':x.farmer['character_uid'] += 1
    if fault == 'merchant_position':x.probe['intent']['merchant']['position'][0] += 1
    if fault == 'farmer_position':x.farmer['position'][0] += 1
    if fault == 'recipient_uid':x.probe['recipient']['uid'] += 1
    if fault == 'recipient_name':x.probe['recipient']['name'] = 'Spiritual'
    if fault == 'recipient_position':x.probe['recipient']['position'][0] += 1
    if fault == 'selected_uid':x.probe['selected_uids'] = [1000]
    if fault == 'missing_intent':x.probe.pop('intent')
    if fault == 'missing_start':x.probe.pop('started_at')
    if fault == 'future_start':x.probe['started_at'] = 101
    if fault == 'past_update':x.probe['updated_at'] = 99
    if fault == 'future_update':x.probe['updated_at'] = 101
    if fault == 'nan_update':x.probe['updated_at'] = float('nan')
    if fault == 'stale_farmer':x.farmer_read = lambda: {**deepcopy(x.farmer), 'timestamp': 90}
    if fault == 'wrong_source_observer':x.source.character = 'Different'
    if fault == 'farmer_trade':x.farmer['trade'] = {'participant': 'Dutch', 'participant_uid': 123}
    if fault == 'stock_changed':x.farmer['silver'] += 1
    if fault == 'source_server':x.farmer['server'] = 'other-server'
    if fault == 'missing_saved_request':x.probe.pop('merchant_after')
    if fault == 'saved_request_changed':x.probe['merchant_after']['request']['participant_uid'] += 1
    if fault == 'saved_process_changed':x.probe['farmer_after']['identity']['pid'] += 1
    if fault == 'saved_request_future':x.probe['merchant_after']['timestamp'] = 101
    x.save()
    if fault == 'missing':x.path.unlink()
    if fault == 'corrupt':x.path.write_text('{invalid', encoding='utf-8')
    if fault == 'list':x.path.write_text('[]', encoding='utf-8')
    if fault == 'journal_changes':
        states = iter([deepcopy(x.probe), {**x.probe, 'phase': 'cancel_verified'}])
        monkeypatch.setattr(probe, 'read_probe', lambda: next(states))
    x.runtime.process_manual('Dutch', x.read(), decline_enabled=True)
    expected = 'needs_attention' if fault in ('live_bool_uid', 'live_wrong_message', 'live_request_server', 'stale_merchant') else 'approval_pending'
    assert x.runtime.manual_status('Dutch')['phase'] == expected
    assert x.calls == [] and not x.runtime.manual_sessions.permissions()


@pytest.mark.parametrize('phase', ['approval_pending', 'needs_attention'])
@pytest.mark.parametrize('decline', [False, True])
def test_exact_false_manual_admission_is_retracted_durably_without_input(supervised, phase, decline):
    x = supervised
    store = x.runtime.manual_sessions
    row = store.begin_request('Dutch', x.read(), now=x.now)
    binding = row['approval_binding']
    if decline:store.reject(binding, operator='Floor', now=x.now)
    if phase == 'needs_attention':store.observe(row['id'], {'reader_error': 'temporary'}, now=x.now)
    x.now += 1
    x.runtime.step('Dutch')
    final = store.get(row['id'])
    assert final['phase'] == 'request_withdrawn' and not final['holds_automation']
    assert final['approval_binding'] == binding
    assert final['request_state'] == ('decline_pending' if decline else 'pending')
    receipt = final['terminal']
    assert receipt['disposition'] == 'manual_admission_retracted_bot_owned'
    assert receipt['original_phase'] == phase and receipt['request_still_visible'] is True
    assert receipt['gameplay_input'] is False and receipt['sales_receipt'] is False
    assert receipt['probe'] == x.probe
    assert x.runtime.manual_status('Dutch') is None and not x.guard.manual_session_blocked('Dutch')
    assert x.calls == [] and store.permissions() == [] and store.verify_audit()
    with store.db() as db:
        assert db.execute('SELECT COUNT(*) FROM manual_decline_claims').fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM manual_replans').fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM sales').fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM manual_declines').fetchone()[0] == int(decline)
    events = [a for a in store.audit(row['id']) if a['event'] == 'manual_admission_retracted_bot_owned']
    assert len(events) == 1
    x.now += 1
    x.runtime.step('Dutch')
    assert store.get(row['id']) == final  # immutable terminal, no duplicate audit
    message = status_text('Dutch', final)
    assert 'Game Request Still Visible' in message and 'Request Withdrawn' not in message
    assert 'Input fence: released' in message


def test_full_preaccept_reconciliation_retracts_false_pending_after_recoverable_error(supervised):
    x=supervised;store=x.runtime.manual_sessions
    row=store.begin_request('Dutch',x.read(),now=x.now)
    x.probe.update(error='Character was paused during input',finished_at=x.now)
    x.save();x.now+=1
    assert x.runtime.process_probe_owned('Dutch',x.read(),now=x.now,require_bilateral=True)
    final=store.get(row['id'])
    assert final['phase']=='request_withdrawn' and not final['holds_automation']
    assert final['terminal']['disposition']=='manual_admission_retracted_bot_owned'
    assert final['terminal']['gameplay_input'] is False and not store.permissions() and x.calls==[]


def test_supplied_preaccept_pair_reconciles_without_reacquiring_busy_farmer_observer(supervised):
    x=supervised;store=x.runtime.manual_sessions
    row=store.begin_request('Dutch',x.read(),now=x.now)
    farmer,merchant=x.farmer_read(),x.read();x.now+=1
    x.source.lock=NS(acquire=lambda **_kwargs:pytest.fail('pre-read pair must not reacquire farmer lock'),
                     release=lambda:None)
    assert x.runtime.reconcile_probe_owned('Dutch',farmer,merchant,now=x.now)
    final=store.get(row['id'])
    assert final['phase']=='request_withdrawn' and not final['holds_automation']
    assert final['terminal']['disposition']=='manual_admission_retracted_bot_owned'
    assert x.calls==[] and not store.permissions()


def test_supplied_preaccept_pair_mismatch_cannot_retract_or_bypass_manual_hold(supervised):
    x=supervised;store=x.runtime.manual_sessions
    row=store.begin_request('Dutch',x.read(),now=x.now)
    x.runtime._sync_manual_fence()
    farmer,merchant=x.farmer_read(),x.read();farmer['silver']+=1;x.now+=1
    assert not x.runtime.reconcile_probe_owned('Dutch',farmer,merchant,now=x.now)
    final=store.get(row['id'])
    assert final['holds_automation'] and final['terminal'] is None
    assert x.guard.manual_session_blocked('Dutch') and x.calls==[]


@pytest.mark.parametrize('protected',['approved','decline_claimed'])
def test_supplied_preaccept_pair_preserves_genuine_manual_hold(supervised,protected):
    x=supervised;store=x.runtime.manual_sessions
    row=store.begin_request('Dutch',x.read(),now=x.now)
    if protected=='approved':store.allow_and_activate(row['approval_binding'],x.read(),operator='Floor',now=x.now)
    else:
        store.reject(row['approval_binding'],operator='Floor',now=x.now)
        store.claim_decline(row['id'],x.read(),now=x.now)
    x.runtime._sync_manual_fence()
    x.now+=1
    assert x.runtime.reconcile_probe_owned('Dutch',x.farmer_read(),x.read(),now=x.now)
    final=store.get(row['id'])
    assert final['holds_automation'] and final['terminal'] is None
    assert x.guard.manual_session_blocked('Dutch') and x.calls==[]


@pytest.mark.parametrize('protected', [
    'approved', 'decline_claimed', 'settlement', 'original_uid', 'original_process',
    'original_stock', 'predates_probe', 'changed_probe', 'second_request',
    'original_position', 'original_map', 'original_slot', 'other_decline_input',
])
def test_existing_real_or_uncertain_manual_intervals_are_never_released(supervised, protected):
    x = supervised
    store = x.runtime.manual_sessions
    original = x.read()
    if protected == 'original_uid':original['request']['participant_uid'] += 1
    if protected == 'original_process':original['identity']['pid'] += 1
    if protected == 'original_stock':original['silver'] += 1
    if protected == 'original_position':original['position'][0] += 1
    if protected == 'original_map':original['map_id'] = 1002
    if protected == 'original_slot':original['inventory'][0]['slot'] = 5
    row = store.begin_request('Dutch', original, now=x.now)
    if protected == 'approved':store.allow_and_activate(row['approval_binding'], x.read(), operator='Floor', now=x.now)
    if protected == 'decline_claimed':
        store.reject(row['approval_binding'], operator='Floor', now=x.now)
        store.claim_decline(row['id'], x.read(), now=x.now)
    if protected in ('settlement', 'second_request'):
        x.now += 1
        store.observe(row['id'], {**x.read(), 'request': None}, now=x.now)
    if protected == 'second_request':
        x.now += 1
        store.begin_request('Dutch', x.read(), session_id=row['id'], now=x.now)
    if protected == 'predates_probe':
        x.probe['started_at'] = x.probe['updated_at'] = 101
        x.save()
    if protected == 'changed_probe':
        x.probe['intent']['merchant']['identity']['pid'] += 1
        x.save()
    if protected == 'other_decline_input':x.journal.set('Dutch', 'unrelated_request_decline', {'phase': 'submitted'})
    x.now += 1
    x.runtime.step('Dutch')
    final = store.get(row['id'])
    assert final['holds_automation'] and final['terminal'] is None
    assert x.guard.manual_session_blocked('Dutch') and x.calls == []


def test_manual_approval_cannot_grant_permission_to_an_existing_probe(supervised):
    x = supervised
    row = x.runtime.manual_sessions.begin_request('Dutch', x.read(), now=x.now)
    with pytest.raises(ManualSessionError, match='Supervised bot probe'):
        x.runtime.approve_manual(row['approval_binding'])
    assert x.runtime.manual_sessions.permissions() == [] and x.calls == []


def test_probe_open_trade_stays_with_supervised_worker(supervised):
    x = supervised
    open_trade(x)
    x.runtime.step('Dutch')
    assert x.runtime.manual_status('Dutch') is None and x.calls == []
    x.farmer['trade']['participant_uid'] += 1
    x.runtime.step('Dutch')
    assert x.runtime.manual_status('Dutch')['phase'] == 'needs_attention'


def open_trade(x, *, phase='trade_open_verified', offered=False):
    x.probe['phase'] = phase
    if phase == 'trade_open_verified':x.probe['offered_uids'] = list(x.probe['selected_uids']) if offered else []
    if phase == 'placement_submitted':
        x.probe['offered_uids'] = []
        x.probe['placing_uid'] = x.probe['selected_uids'][0]
    terms = dict(own_items=[], items=[], own_silver=0, other_silver=0,
                 accepted=False, other_accepted=False)
    x.state.update(request=None, trade={**deepcopy(terms), 'participant': 'Parasite', 'participant_uid': 55})
    x.farmer['trade'] = {**deepcopy(terms), 'participant': 'Dutch', 'participant_uid': 123}
    if offered:
        x.farmer['trade']['own_items'] = deepcopy(x.probe['intent']['items'])
        x.state['trade']['items'] = deepcopy(x.probe['intent']['items'])
    if phase in ('farmer_confirm_verified', 'merchant_confirm_submitted'):
        x.farmer['trade']['accepted'] = x.state['trade']['other_accepted'] = True
    if phase not in ('accept_submitted', 'cancel_submitted'):
        x.probe.update(accepted_at=x.now,updated_at=x.now,
                       farmer_after=x.farmer_read(),merchant_after=x.read())
    x.save()


@pytest.mark.parametrize('phase,offered', [
    ('accept_submitted', False), ('trade_open_verified', False),
    ('trade_open_verified', True), ('placement_submitted', True), ('offer_verified', True),
    ('farmer_confirm_submitted', True), ('farmer_confirm_verified', True),
    ('merchant_confirm_submitted', True), ('cancel_submitted', False),
])
def test_exact_farmer_trade_has_same_bilateral_probe_precedence(supervised, monkeypatch, phase, offered):
    from conquest.merchants import manual_farmer
    x = supervised
    open_trade(x, phase=phase, offered=offered)
    monkeypatch.setattr(manual_farmer, 'presence', lambda observer: True)
    monkeypatch.setattr(manual_farmer, 'controller', lambda *a: pytest.fail('Probe must not create a decline controller'))
    assert x.runtime.observe_manual_farmer() is False
    assert x.runtime.manual_farmer_observation['bot_owned'] is True
    assert x.runtime.manual_status('Farmer') is None
    assert x.runtime.process_manual('Farmer', x.farmer_read(), decline_enabled=True)
    assert x.runtime.manual_sessions.permissions() == [] and x.calls == []


@pytest.mark.parametrize('fault', [
    'merchant_uid', 'farmer_uid', 'merchant_name', 'farmer_name', 'process', 'farmer_process',
    'merchant_silver', 'farmer_silver', 'own_silver', 'other_silver', 'wrong_item', 'missing_offer',
    'merchant_offer', 'farmer_inventory', 'merchant_inventory', 'booth_price', 'capacity',
    'terminal', 'wrong_target', 'corrupt', 'stale_peer', 'changed_request',
    'missing_saved_peer', 'saved_peer_identity', 'malformed_currency', 'missing_acceptance',
])
def test_farmer_mismatch_is_quarantined_without_any_probe_privilege(supervised, fault):
    x = supervised
    open_trade(x, phase='offer_verified', offered=True)
    if fault == 'merchant_uid':x.farmer['trade']['participant_uid'] += 1
    if fault == 'farmer_uid':x.state['trade']['participant_uid'] += 1
    if fault == 'merchant_name':x.farmer['trade']['participant'] = 'Spiritual'
    if fault == 'farmer_name':x.state['trade']['participant'] = 'Visitor'
    if fault == 'process':x.state['identity']['creation_time_100ns'] += 1
    if fault == 'farmer_process':x.farmer['identity']['creation_time_100ns'] += 1
    if fault == 'merchant_silver':x.state['silver'] += 1
    if fault == 'farmer_silver':x.farmer['silver'] += 1
    if fault == 'own_silver':x.farmer['trade']['own_silver'] = 1
    if fault == 'other_silver':x.state['trade']['other_silver'] = 1
    if fault == 'wrong_item':x.farmer['trade']['own_items'][0]['plus'] = 2
    if fault == 'missing_offer':x.farmer['trade']['own_items'] = []; x.state['trade']['items'] = []
    if fault == 'merchant_offer':x.state['trade']['own_items'] = deepcopy(x.state['inventory'])
    if fault == 'farmer_inventory':x.farmer['inventory'].append({**deepcopy(x.farmer['inventory'][0]), 'uid': 999})
    if fault == 'merchant_inventory':x.state['inventory'] = []
    if fault == 'booth_price':x.state['booth'][0]['price'] += 1
    if fault == 'capacity':x.state['capacity'] -= 1
    if fault == 'terminal':x.probe['phase'] = 'delivery_verified'
    if fault == 'wrong_target':x.probe['target_profile_id'] = 'other-target'
    if fault == 'stale_peer':x.driver.read = lambda: {**deepcopy(x.state), 'timestamp': 90}
    if fault == 'changed_request':x.state['request'] = {'participant': 'Parasite', 'participant_uid': 55,
                                                      'message': 'Parasite wishes to trade with you.'}
    if fault == 'missing_saved_peer':x.probe.pop('farmer_after')
    if fault == 'saved_peer_identity':x.probe['merchant_after']['identity']['pid'] += 1
    if fault == 'malformed_currency':x.farmer['trade']['own_silver'] = False
    if fault == 'missing_acceptance':x.farmer['trade'].pop('accepted')
    x.save()
    if fault == 'corrupt':x.path.write_text('{bad', encoding='utf-8')
    x.runtime.process_manual('Farmer', x.farmer_read(), decline_enabled=True)
    assert x.runtime.manual_status('Farmer')['phase'] == 'needs_attention'
    assert x.guard.manual_session_blocked('Farmer') and x.calls == []


@pytest.mark.parametrize('approved', [False, True])
def test_probe_does_not_release_existing_farmer_interval(supervised, approved):
    x = supervised
    incoming = {**x.farmer_read(), 'request': {'participant': 'Dutch', 'participant_uid': 123,
                                            'message': 'Dutch wishes to trade with you.'}}
    row = x.runtime.manual_sessions.begin_request('Farmer', incoming, now=x.now)
    if approved:
        x.runtime.manual_sessions.allow_and_activate(row['approval_binding'], incoming, operator='Floor', now=x.now)
    else:
        x.runtime.manual_sessions.reject(row['approval_binding'], operator='Floor', now=x.now)
        x.runtime.manual_sessions.claim_decline(row['id'], incoming, now=x.now)
    open_trade(x)
    x.runtime.process_manual('Farmer', x.farmer_read())
    assert x.runtime.manual_sessions.get(row['id'])['holds_automation']
    assert x.guard.manual_session_blocked('Farmer') and x.calls == []


def test_profile_scoped_probe_requires_explicit_exact_profile_binding(supervised):
    x = supervised
    for key in ('target_profile_id', 'farmer_profile_id'):x.probe.pop(key)
    assert ownership(x.probe, 'Dutch', 'Dutch', 'Farmer', x.farmer_read(), x.read(), now=x.now)
    with pytest.raises(ValueError, match='profile'):
        ownership(x.probe, 'Dutch', 'merchant-profile', 'farmer-profile', x.farmer_read(), x.read(), now=x.now)
    x.probe.update(target_profile_id='merchant-profile', farmer_profile_id='farmer-profile')
    assert ownership(x.probe, 'Dutch', 'merchant-profile', 'farmer-profile', x.farmer_read(), x.read(), now=x.now)


@pytest.mark.parametrize('boundary', ['verified_after_admission', 'missing_verified_time', 'submitted_only',
                                    'evidence_predates_verified_time'])
def test_retraction_requires_original_admission_after_durable_request_boundary(supervised, boundary):
    x = supervised
    # Preparation started at 90, but only the durable write at 101 proves the
    # request was bot-owned. A manual visitor observed at 100 keeps its hold.
    x.probe['started_at'] = 90
    for role in ('farmer', 'merchant'):
        x.probe['intent'][role]['timestamp'] = 90
        x.probe[role + '_after']['timestamp'] = 101
    x.probe['updated_at'] = 101
    original = x.read()  # original evidence timestamp 100
    if boundary == 'evidence_predates_verified_time':x.now = 101
    row = x.runtime.manual_sessions.begin_request('Dutch', original, now=x.now)
    if boundary == 'missing_verified_time':x.probe.pop('updated_at')
    if boundary == 'submitted_only':x.probe['phase'] = 'request_submitted'
    x.save()
    x.now = 102
    x.runtime.step('Dutch')
    saved = x.runtime.manual_sessions.get(row['id'])
    assert saved['holds_automation'] and saved['terminal'] is None
    assert x.guard.manual_session_blocked('Dutch') and x.calls == []


@pytest.mark.parametrize('phase,prior,placing,live,allowed', [
    ('trade_open_verified', None, None, [], True),
    ('trade_open_verified', None, None, [99], False),
    ('trade_open_verified', [99], None, [99], True),
    ('trade_open_verified', [99], None, [], False),
    ('trade_open_verified', [99], None, [99, 100], False),
    ('trade_open_verified', [99, 100], None, [100], False),
    ('placement_submitted', [], 99, [], True),
    ('placement_submitted', [], 99, [99], True),
    ('placement_submitted', [], 99, [100], False),
    ('placement_submitted', [99], 100, [99], True),
    ('placement_submitted', [99], 100, [99, 100], True),
    ('placement_submitted', [99], 100, [100], False),
    ('placement_submitted', [99], 100, [], False),
    ('placement_submitted', [], None, [], False),
    ('placement_submitted', [99], 99, [99], False),
    ('accept_submitted', [], None, [99], False),
    ('cancel_submitted', [], None, [99], False),
    ('offer_verified', [], None, [99], False),
    ('offer_verified', [], None, [99, 100], True),
    ('farmer_confirm_verified', [], None, [100], False),
])
def test_open_trade_offer_is_bound_to_exact_durable_phase(supervised, phase, prior, placing, live, allowed):
    x = supervised
    second = {**deepcopy(x.farmer['inventory'][0]), 'uid': 100}
    x.farmer['inventory'].append(second)
    x.probe['intent']['farmer']['inventory'].append(deepcopy(second))
    x.probe['intent']['items'].append(deepcopy(second))
    x.probe['farmer_after']['inventory'].append(deepcopy(second))
    x.probe['selected_uids'].append(100)
    open_trade(x, phase=phase, offered=phase in ('offer_verified', 'farmer_confirm_verified'))
    if prior is None:x.probe.pop('offered_uids', None)
    else:x.probe['offered_uids'] = prior
    if placing is None:x.probe.pop('placing_uid', None)
    else:x.probe['placing_uid'] = placing
    x.save()
    offered = [item for item in x.probe['intent']['items'] if item['uid'] in live]
    x.farmer['trade']['own_items'] = deepcopy(offered)
    x.state['trade']['items'] = deepcopy(offered)
    assert x.runtime.process_probe_owned('Dutch', x.read()) is allowed
    assert x.runtime.process_probe_owned('Farmer', x.farmer_read()) is allowed
    assert x.calls == [] and x.runtime.manual_sessions.permissions() == []
