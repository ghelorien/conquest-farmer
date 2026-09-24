"""One incident-bound close of an empty 1078 delivery trade.

This removes the open modal only. It never settles the source delivery or its
receiver reservation: historical booth sales still require their own proof.
"""
import json
import hashlib
import threading
import time
import uuid
from pathlib import Path

from conquest.capture import CaptureUnavailable
from conquest.character_context import state_path
from conquest.merchants.delivery import (exact_items, exact_listings,
    validate_snapshot, _sale_adjustment)
from conquest.merchants.delivery_bridge import pair
from conquest.merchants.delivery_operation import JOURNAL, saved_intent, status
from conquest.merchants.journal import Journal, character_name

PURPOSE = 'delivery_empty_recovery'
WINDOW = 15
SETTLE = 5


def _trace(journal, key):
    return [{**step, 'payload': json.loads(step['payload'])}
            for step in journal.trace(key) if step['stage'] != 'transaction']


def _marker(trace):
    return next((step for step in trace if step['stage'] == 'cleanup_trade'
                 and step['status'] == 'before_action'), None)


def _incident(ui, key, character, *, allow_marker=False):
    journal = Journal(JOURNAL)
    receipt = status(journal, key)
    if (not receipt or receipt['character'] != character
            or receipt['phase'] != 'uncertain' or receipt['outcome'] != 'unresolved'):
        raise ValueError('Exact uncertain farmer delivery is required')
    worker = getattr(ui, 'delivery_workers', {}).get(key)
    if worker and worker.is_alive() or key in getattr(ui, 'delivery_admissions', set()):
        raise ValueError('Original delivery worker is still active')
    trace = _trace(journal, key)
    markers = [s for s in trace if s['stage'] == 'cleanup_trade'
               and s['status'] == 'before_action']
    if (not any(s['stage'] == 'action_trace' and s['status'] == 'initialized' for s in trace)
            or not any(s['stage'] == 'trade_request' and s['status'] == 'observed' for s in trace)
            or any(s['stage'].startswith('offer_item') or 'confirm' in s['stage'] for s in trace)
            or any(s['status'] == 'before_action' and s['stage'] not in
                   ('trade_target_mode', 'trade_request', 'cleanup_trade') for s in trace)
            or len(markers) > 1
            or (not allow_marker and any(s['stage'] == 'cleanup_trade' and s['status'] == 'observed'
                                         for s in trace))
            or (_marker(trace) is not None and not allow_marker)):
        raise ValueError('Only the original request-only trade may be closed once')
    intent = saved_intent(journal, key)
    reservation = ui.runtime.journal.get(character, 'delivery_reservation') or {}
    if (reservation.get('request_id') != key or reservation.get('phase') not in
            ('reserved', 'offer_ready') or
            exact_items(reservation['intent']['items']) != exact_items(intent['items'])):
        raise ValueError('Exact receiver delivery hold changed')
    return journal, intent, trace


def _empty(snapshot, other, *, open_trade):
    if snapshot.get('request') is not None:
        raise ValueError('Another trade request is present')
    trade = snapshot.get('trade')
    if bool(trade) != open_trade:
        raise ValueError('Exact bilateral trade window changed')
    if trade and (trade.get('participant') != other['character']
                  or trade.get('participant_uid') != other['character_uid']
                  or trade.get('own_items') or trade.get('items')
                  or trade.get('own_silver') != 0 or trade.get('other_silver') != 0
                  or trade.get('accepted') is not False
                  or trade.get('other_accepted') is not False):
        raise ValueError('Trade is not the empty unaccepted bilateral session')


def _baseline(intent, farmer, merchant):
    """Admit existing booth drift only as unclaimed context for closing."""
    now = time.time()
    validate_snapshot(farmer, intent['farmer']['character'], now)
    validate_snapshot(merchant, intent['merchant']['character'], now)
    for role, current in (('farmer', farmer), ('merchant', merchant)):
        original = intent[role]
        if (current['identity'] != original['identity']
                or current['character_uid'] != original['character_uid']
                or exact_items(current['inventory']) != exact_items(original['inventory'])):
            raise ValueError('Original delivery participant or carried assets changed')
    if (farmer['silver'] != intent['farmer']['silver']
            or exact_listings(farmer.get('booth', [])) != exact_listings(intent['farmer'].get('booth', []))):
        raise ValueError('Farmer ownership changed since original request')
    original = exact_listings(intent['merchant'].get('booth', []))
    current = exact_listings(merchant.get('booth', []))
    if any(original.get(uid) != item for uid, item in current.items()):
        raise ValueError('Merchant booth was replaced or repriced')
    from conquest.merchants.sales import net_bounds
    gone = [item for item in intent['merchant'].get('booth', []) if item['uid'] not in current]
    low, high = net_bounds(gone)
    if not low <= merchant['silver'] - intent['merchant']['silver'] <= high:
        raise ValueError('Merchant booth and silver drift is not sale-shaped')
    _empty(farmer, merchant, open_trade=True)
    _empty(merchant, farmer, open_trade=True)


def _same_since(ui, baseline, fresh, *, open_trade):
    """Only verified *new* sales may change the pre-click booth baseline."""
    before_f, before_m = baseline['farmer'], baseline['merchant']
    farmer, merchant = fresh['farmer'], fresh['merchant']
    now = time.time()
    validate_snapshot(farmer, before_f['character'], now)
    validate_snapshot(merchant, before_m['character'], now)
    for before, current in ((before_f, farmer), (before_m, merchant)):
        if any(current[k] != before[k] for k in ('identity', 'character_uid', 'map_id', 'position')):
            raise ValueError('Empty trade participant moved or changed identity')
        if exact_items(current['inventory']) != exact_items(before['inventory']):
            raise ValueError('Inventory changed during empty trade cleanup')
    if (farmer['silver'] != before_f['silver'] or
            exact_listings(farmer.get('booth', [])) != exact_listings(before_f.get('booth', []))):
        raise ValueError('Farmer ownership changed during empty trade cleanup')
    from conquest.merchants.sales import qualified_delivery_receipts
    receipts = qualified_delivery_receipts(ui.runtime.journal, baseline, merchant)
    expected_booth, silver_gain, receipts = _sale_adjustment(baseline, merchant, receipts)
    if (exact_listings(merchant.get('booth', [])) != expected_booth
            or merchant['silver'] != before_m['silver'] + silver_gain):
        raise ValueError('Merchant changed without exact verified new sale receipts')
    _empty(farmer, merchant, open_trade=open_trade)
    _empty(merchant, farmer, open_trade=open_trade)
    return receipts


def lease_authorized(ui, character):
    """The native surface exception belongs to this one worker and grant."""
    state = getattr(ui, 'delivery_empty_recovery_state', None)
    observer = ui.runtime.observers.get(character)
    controller = ui.runtime.controllers.get(character)
    if (ui.coordinator.purpose != PURPOSE or state is None
            or state['character'] != character
            or threading.current_thread() is not getattr(ui, 'delivery_empty_recovery_worker', None)
            or (ui.grant or {}).get('request_id') != state['grant_id']
            or (ui.grant or {}).get('scope') != PURPOSE
            or observer is None or controller is None
            or controller.driver.observer is not observer
            or observer.adapter.identity != state['baseline']['merchant']['identity']):
        return False
    observer.adapter.assert_identity()
    return True


def _guard(ui, state):
    control = ui.app.control.snapshot()
    if (ui.closed or ui.app.closing or ui.coordinator.stopped
            or Path(state_path('.runtime/overnight.stop')).exists()
            or ui.coordinator.manual_active() or ui.runtime.manual_handoff_status() is not None
            or control['enabled'] or control.get('paused')
            or control['revision'] != state['revision']
            or not ui.safe_to_yield() or time.time() >= state['expires_at']
            or (ui.grant or {}).get('request_id') != state['grant_id']
            or ui.runtime.handoff != state['grant_id']):
        raise CaptureUnavailable('Exact empty trade recovery grant stopped or changed')
    ui.coordinator.check()
    _incident(ui, state['request_id'], state['character'], allow_marker=True)


def _reconcile(ui, key, character, *, deadline=None):
    journal, _, trace = _incident(ui, key, character, allow_marker=True)
    marker = _marker(trace)
    if marker is None:
        return {'request_id': key, 'phase': 'pre_input', 'source_uncertain': True}
    baseline = marker['payload']['baseline']
    if any(s['stage'] == 'cleanup_trade' and s['status'] == 'observed' for s in trace):
        return {'request_id': key, 'phase': 'closed_verified', 'source_uncertain': True}
    while True:
        farmer, merchant = pair(ui, character)
        fresh = {'farmer': farmer, 'merchant': merchant}
        try:
            receipts = _same_since(ui, baseline, fresh, open_trade=False)
        except (ValueError, KeyError, TypeError):
            if deadline is None or time.monotonic() >= deadline:
                return {'request_id': key, 'phase': 'click_uncertain', 'source_uncertain': True}
        else:
            # Two durable, matching closed ownership samples allow a restart
            # after the one-shot marker without another native click.
            proof = {'farmer': {'identity': farmer['identity'], 'silver': farmer['silver'],
                                'inventory': exact_items(farmer['inventory'])},
                     'merchant': {'identity': merchant['identity'], 'silver': merchant['silver'],
                                  'inventory': exact_items(merchant['inventory']),
                                  'booth': exact_listings(merchant.get('booth', []))},
                     'verified_new_sales': receipts}
            digest = hashlib.sha256(json.dumps(proof, sort_keys=True).encode()).hexdigest()
            observations = [s['payload']['observed_at'] for s in _trace(journal, key)
                            if s['stage'] == 'cleanup_recovery_observation'
                            and s['status'] == 'observed'
                            and s['payload'].get('ownership_digest') == digest
                            and type(s['payload'].get('observed_at')) in (int, float)]
            now = time.time()
            if observations and now - min(observations) >= SETTLE:
                journal.step(key, 'cleanup_trade', 'observed', {
                    'closed_at': now, 'verified_new_sales': receipts,
                    'farmer_trade': False, 'merchant_trade': False,
                    'farmer': farmer, 'merchant': merchant,
                    'historical_delivery_outcome': 'unknown'}, terminal=True)
                return {'request_id': key, 'phase': 'closed_verified', 'source_uncertain': True}
            if not observations:
                journal.step(key, 'cleanup_recovery_observation', 'observed', {
                    'ownership_digest': digest, 'observed_at': now})
        if deadline is None or time.monotonic() >= deadline:
            return {'request_id': key, 'phase': 'settlement_pending', 'source_uncertain': True}
        time.sleep(.1)


def _run(ui, state, token):
    from conquest.desktop_runtime import physical_coordinates
    from conquest.foreground import foreground_click
    from conquest.merchants.driver import wait_hover_validation
    from conquest.merchants.empty_delivery_cancel import control
    from conquest.merchants.trade_driver_1078 import native_foreground
    key, character = state['request_id'], state['character']
    journal = Journal(JOURNAL)
    driver = ui.runtime.controllers[character].driver
    try:
        with ui.grant_fence.bind_worker(token), ui.coordinator.lease(character, purpose=PURPOSE), physical_coordinates():
            _guard(ui, state)
            farmer, merchant = pair(ui, character)
            _same_since(ui, state['baseline'], {'farmer': farmer, 'merchant': merchant}, open_trade=True)
            native_foreground(driver, merchant['identity'], activate=True)
            window, point = control(driver, merchant)
            size = tuple(driver.target.snapshot()['client_size'])
            if list(size) != list(driver.memory.gui.viewport_size()):
                raise ValueError('Trade viewport changed before cleanup')
            layout = driver.layout_revision(); revision = layout.stable()

            def guard():
                _guard(ui, state)
                fresh_f, fresh_m = pair(ui, character)
                _same_since(ui, state['baseline'], {'farmer': fresh_f, 'merchant': fresh_m}, open_trade=True)
                native_foreground(driver, fresh_m['identity'])
                if control(driver, fresh_m) != (window, point):
                    raise ValueError('Exact native trade close control moved')
                driver.memory.gui.assert_hovered(window, '#CLOSE')
                layout.assert_current(revision)

            def press():
                wait_hover_validation(guard, lambda: _guard(ui, state))
                if _marker(_trace(journal, key)) is not None:
                    raise ValueError('Empty trade close was already submitted')
                # Persist the irreversible boundary before the native press.
                journal.step(key, 'cleanup_trade', 'before_action', {
                    'grant_id': state['grant_id'], 'baseline': state['baseline'],
                    'point': point, 'submitted_at': time.time()}, terminal=True)

            foreground_click(driver.target, *point, size, require_foreground=True,
                             layout_guard=lambda: layout.assert_current(revision), before_press=press)
        result = _reconcile(ui, key, character, deadline=time.monotonic() + SETTLE + 2)
        ui.delivery_empty_recovery_result = result
    except Exception as error:
        ui.delivery_empty_recovery_result = {
            'request_id': key, 'phase': 'click_uncertain' if _marker(_trace(journal, key)) else 'pre_input',
            'source_uncertain': True, 'error': str(error)}
    finally:
        with ui.runtime.lock:
            if (ui.grant or {}).get('request_id') == state['grant_id']:
                ui.grant_fence.revoke(state['grant_id'])
                ui.grant = None
            if ui.runtime.handoff == state['grant_id']:
                ui.runtime.handoff = None
            if ui.runtime.work_deadline == state['expires_at']:
                ui.runtime.work_deadline = state['previous_work_deadline']


def dispatch(ui, body):
    if (set(body) != {'action', 'request_id'} or body['action'] not in
            ('delivery-empty-recovery', 'delivery-empty-recovery-status')):
        raise ValueError('Unsupported empty delivery recovery command')
    key = body['request_id']
    if not isinstance(key, str) or not 1 <= len(key) <= 100:
        raise ValueError('Invalid exact delivery request ID')
    journal = Journal(JOURNAL)
    receipt = status(journal, key)
    if not receipt: raise ValueError('Unknown exact delivery request')
    character = character_name(receipt['character'])
    if body['action'] == 'delivery-empty-recovery-status':
        worker = getattr(ui, 'delivery_empty_recovery_worker', None)
        if worker and worker.is_alive():
            return {'request_id': key, 'phase': 'running', 'source_uncertain': True}
        with ui.runtime.lock:
            result = _reconcile(ui, key, character) if _marker(_trace(journal, key)) else None
            return result or {'request_id': key, 'phase': 'pre_input', 'source_uncertain': True}
    if not ui.coordinator.lock.acquire(blocking=False):
        raise CaptureUnavailable('Wait for the current input action')
    try:
        with ui.runtime.lock:
            if (ui.grant is not None or ui.runtime.handoff is not None
                    or ui.coordinator.owner is not None
                    or getattr(ui.runtime, 'delivery_window', None)
                    or getattr(ui.runtime, 'refill_window', None)
                    or ui.coordinator.stopped or ui.coordinator.manual_active()
                    or Path(state_path('.runtime/overnight.stop')).exists()
                    or ui.runtime.manual_handoff_status() is not None
                    or not ui.safe_to_yield()):
                raise CaptureUnavailable('Empty trade cleanup needs an idle protected app')
            _, intent, _ = _incident(ui, key, character)
            farmer, merchant = pair(ui, character)
            _baseline(intent, farmer, merchant)
            control = ui.app.control.snapshot()
            if control['enabled'] or control.get('paused'):
                raise CaptureUnavailable('Farmer must remain Off before empty trade cleanup')
            grant_id = PURPOSE + ':' + uuid.uuid4().hex
            expires = time.time() + WINDOW
            state = {'request_id': key, 'character': character, 'grant_id': grant_id,
                     'baseline': {'farmer': farmer, 'merchant': merchant},
                     'revision': control['revision'], 'expires_at': expires,
                     'previous_work_deadline': ui.runtime.work_deadline}
            token = ui.grant_fence.activate(grant_id, control['revision'], expires,
                                            scope=PURPOSE)
            ui.grant = {'request_id': grant_id, 'revision': control['revision'],
                        'expires_at': expires, 'scope': PURPOSE}
            ui.runtime.handoff = grant_id
            ui.runtime.work_deadline = expires
            ui.delivery_empty_recovery_state = state
            ui.delivery_empty_recovery_result = None
            worker = threading.Thread(target=_run, args=(ui, state, token), daemon=True,
                                      name='delivery-empty-recovery')
            ui.delivery_empty_recovery_worker = worker
            try: worker.start()
            except Exception:
                ui.grant_fence.revoke(grant_id)
                ui.grant = None
                ui.runtime.handoff = None
                ui.runtime.work_deadline = state['previous_work_deadline']
                raise
            return {'request_id': key, 'started': True, 'source_uncertain': True,
                    'expires_at': expires}
    finally:
        ui.coordinator.lock.release()
