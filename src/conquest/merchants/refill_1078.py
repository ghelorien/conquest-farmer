"""App-scheduled refill using receipt-qualified listing and existing safe grants.

The route controller parks the farmer; this engine only consumes its live
grant. Native focus is reverified under the lease. No login/travel/trade or
booth-opening input is authorized.
"""
import json
import time
import uuid

from conquest.capture import CaptureUnavailable
from conquest.merchants.listing_capability_1078 import require
from conquest.merchants.listing_plan_1078 import OwnedPeerUnavailable


def _blocked(code, note=None):
    return {'state': 'waiting', 'blocker': code, 'note': note or code,
            'handoff_mode': 'existing_route_grant', 'focus_mode': 'exact_native_owner'}


def start(runtime, character, **kwargs):
    """Mark newly requested 1078 checks without adopting a legacy cursor."""
    schedule = runtime.refills[character]
    was_pending = schedule.state().get('pending')
    schedule.start(**kwargs)
    if not was_pending and runtime.read_only_1078(character):
        state = schedule.state()
        state['listing1078_engine'] = 1
        runtime.journal.set(character, 'refill', state)


def _settle_cursor(journal, character, request_id):
    """Count a verified listing exactly once, including after app restart."""
    with journal.db() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM transactions WHERE id=?', (request_id,)).fetchone()
        state = db.execute("SELECT value FROM state WHERE character=? AND name='refill'",
                           (character,)).fetchone()
        state = json.loads(state[0]) if state else {}
        request = state.get('listing1078_request')
        if not request or request['request_id'] != request_id:
            return
        from conquest.merchants.booth_listing_once_1078 import KIND
        if (not row or row['character'] != character or row['kind'] != KIND
                or row['phase'] != 'verified'
                or json.loads(row['before_json']).get('request') != request
                or not json.loads(row['result_json']).get('exact_memory_listing_verified')):
            raise ValueError('Refill cursor lacks its exact verified listing receipt')
        before = json.loads(row['before_json'])
        state.update(listing1078_request=None, listed=state.get('listed', 0)+1,
                     last_verified_listing={'request_id': request_id,
                                            'verified_at': row['updated'],
                                            'character': before['request']['character'],
                                            'profile_id': before['profile_id'],
                                            'character_uid': before['request']['expected_character_uid'],
                                            'identity': before['request']['expected_identity']},
                     cursor=[uid for uid in state.get('cursor', []) if uid != request['item_uid']])
        db.execute("UPDATE state SET value=? WHERE character=? AND name='refill'",
                   (json.dumps(state), character))


def _settle_cancelled(journal, character, request_id):
    """Release only a proven Cancel, leaving its consumed receipt immutable."""
    from conquest.merchants.booth_listing_once_1078 import KIND, OWNERSHIP_FIELDS
    with journal.db() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM transactions WHERE id=?', (request_id,)).fetchone()
        saved = db.execute("SELECT value FROM state WHERE character=? AND name='refill'",
                           (character,)).fetchone()
        state = json.loads(saved[0]) if saved else {}
        request = state.get('listing1078_request')
        if not request or request.get('request_id') != request_id:
            return
        if (not row or row['character'] != character or row['kind'] != KIND
                or row['phase'] != 'aborted'):
            raise ValueError('Refill cancellation lacks its exact terminal receipt')
        before = json.loads(row['before_json'])
        result = json.loads(row['result_json'] or '{}')
        first, second = result.get('first'), result.get('second')
        markers = {step['stage'] for step in db.execute(
            "SELECT stage FROM transaction_steps WHERE transaction_id=? "
            "AND stage IN ('confirm_press','cancel_press')", (request_id,))}
        if (before.get('request') != request or result.get('uid') != request.get('item_uid')
                or markers != {'cancel_press'}
                or any(result.get(key) is not expected for key, expected in (
                    ('cancel_verified', True), ('stock_unchanged', True),
                    ('listing_submitted', False), ('confirmation_attempted', False),
                    ('cancellation_attempted', True), ('replay_allowed', False)))
                or not isinstance(first, dict) or not isinstance(second, dict)
                or any(first.get(key) != before['snapshot'].get(key)
                       or second.get(key) != first.get(key)
                       for key in OWNERSHIP_FIELDS if key in before['snapshot'])):
            raise ValueError('Refill cancellation lacks unchanged-ownership proof')
        # The old request ID is permanently consumed. The next plan uses a
        # fresh observation and allocates a new ID; no old input is replayed.
        state['listing1078_request'] = None
        db.execute("UPDATE state SET value=? WHERE character=? AND name='refill'",
                   (json.dumps(state), character))


def pending_cancel(runtime, character, snapshot, *, preflight=False):
    """Prove an interrupted scheduled request is eligible for exact Cancel only.

    This is admission, never a receipt. The existing cancel worker rechecks
    native bindings and consumes its durable mouse-down marker once.
    """
    from conquest.memory_build_layout import CLIENT_SHA256_1078
    from conquest.merchants.listing_capability_1078 import ENGINE_REVISION
    from conquest.merchants.booth_listing_once_1078 import (
        WORKER_LOCK, WORKERS, OWNERSHIP_FIELDS, _profile,
    )
    from conquest.merchants.booth_listing_cancel_1078 import _pending, _unchanged
    state = runtime.refills[character].state()
    request = state.get('listing1078_request')
    if not runtime.refill_enabled(character) or state.get('listing1078_engine') != 1 or not request:
        raise ValueError('No enabled exact scheduled refill cleanup')
    key = request['request_id']
    with WORKER_LOCK:
        worker = WORKERS.get(key)
        if worker and worker.is_alive():
            raise ValueError('Exact listing input worker is still active')
        before = _pending(runtime.journal, key, character)
    profile = _profile(character)
    old = before.get('snapshot', {})
    # Original native dispatch records the read-only preflight shape, whose
    # closed-window booleans predate canonical trade/request None fields.
    # All assets remain mandatory; only those two redundant fields may be
    # absent when the original record explicitly proved both windows closed.
    ownership_complete = all(key in old for key in OWNERSHIP_FIELDS
                             if key not in ('trade', 'request'))
    original_closed = all(old.get(key) is None if key in old else (
        old.get('closed_modal') is True and old.get('trade_open') is False
        and old.get('request_open') is False) for key in ('trade', 'request'))
    if (before.get('request') != request or before.get('profile_id') != profile.id
            or before.get('client_sha256') != CLIENT_SHA256_1078
            or before.get('listing_engine_revision') != ENGINE_REVISION
            or before.get('scheduled_foreground_refill') is not True
            or not ownership_complete or not original_closed):
        raise ValueError('Pending receipt is not this exact native scheduled refill')
    require(runtime.journal, character, snapshot)
    if preflight:
        if snapshot.get('closed_modal') is not True:
            raise ValueError('Trade/request windows must be closed before refill cleanup')
        modal = (snapshot.get('listing_preflight') or {}).get('price_modal') or {}
        digits = modal.get('candidate_price_buffer_text')
        if (modal.get('observed') is not True
                or modal.get('candidate_selected_item_uid') != request['item_uid']
                or any(modal.get(key) is not True for key in (
                    'selected_item_uid_verified', 'price_buffer_binding_verified',
                    'native_ok_cancel_handlers_verified'))
                or not isinstance(digits, str)):
            raise ValueError('Exact native pending listing dialog is unavailable')
        digits = digits.replace(',', '')
        if digits and (not digits.isascii() or not digits.isdigit()
                       or not str(request['price']).startswith(digits)):
            raise ValueError('Pending native price differs from the exact request')
        snapshot = {**snapshot, 'trade': None, 'request': None}
    # TradeMemory1078.read adds a definitions-file `category` annotation to
    # each item. Original listing preflight and the Cancel worker use the
    # native stock reader without that annotation. Remove only this derived
    # field for admission; preserve every native field, item order and price.
    # The worker still compares its un-enriched native reads independently.
    snapshot = {**snapshot, **{collection: [
        {key: value for key, value in item.items() if key != 'category'}
        for item in snapshot[collection]] for collection in ('inventory', 'booth')}}
    _unchanged(snapshot, before, profile)
    return request


def _recover_pending(ui, character, snapshot):
    runtime = ui.runtime
    request = pending_cancel(runtime, character, snapshot)
    control = ui.app.control.snapshot()
    if control.get('paused'):
        return _blocked('farmer_paused')
    from conquest.merchants.listing_handoff_1078 import request_handoff, scope_allows
    if control.get('enabled') or not ui.safe_to_yield():
        key = request_handoff(runtime, character)
        return {**_blocked('waiting_farmer_handoff'), 'handoff_request_id': key}
    if not scope_allows(ui, character, request_id=request['request_id'], cleanup=True):
        return _blocked('exact_refill_cancel_grant_required')
    from conquest.merchants.booth_listing_cancel_1078 import dispatch_cancel
    receipt = dispatch_cancel(ui, request['request_id'], character)
    return {'state': 'listing_cancel_pending', 'request_id': request['request_id'],
            'phase': receipt['phase']}


def step(ui, character, snapshot):
    """One app observer tick; never repeats an uncertain input boundary."""
    runtime, journal = ui.runtime, ui.runtime.journal
    from conquest.merchants.booth_listing_once_1078 import (
        _row, reconcile, dispatch, _profile, _policy, _merchant_intent,
        _farmer_safe_market, _item_fingerprint,
    )
    if not runtime.listing1078_lock.acquire(blocking=False):
        return _blocked('other_refill_check_active')
    try:
        schedule = runtime.refills[character]
        state = schedule.state()
        from conquest.merchants.listing_handoff_1078 import release_completed_refill
        release_completed_refill(ui, character)
        request = state.get('listing1078_request')
        if request:
            row = _row(journal, request['request_id'])
            if row and row['phase'] not in ('verified', 'aborted', 'operator_overridden'):
                # Reconciliation is read-only, even during Stop/Pause.
                reconcile(ui, request['request_id'], character)
                row = _row(journal, request['request_id'])
            if row and row['phase'] == 'verified':
                _settle_cursor(journal, character, request['request_id'])
                return {'state': 'listing_verified', 'request_id': request['request_id']}
            if row and row['phase'] == 'aborted':
                try:
                    _settle_cancelled(journal, character, request['request_id'])
                except (ValueError, KeyError, TypeError) as error:
                    return _blocked('listing_receipt_needs_reconciliation', str(error))
                state = schedule.state()
                if state.get('listing1078_request'):
                    return _blocked('listing_receipt_needs_reconciliation', 'cancelled request changed')
            elif row:
                # An unproven abort or operator override is never authority
                # to start another automatic request for that same item.
                if row['phase'] in ('prepared', 'uncertain'):
                    try:
                        return _recover_pending(ui, character, snapshot)
                    except (ValueError, OSError, KeyError, TypeError, CaptureUnavailable) as error:
                        return _blocked('listing_receipt_needs_reconciliation', str(error))
                return _blocked('listing_receipt_needs_reconciliation', row['phase'])
            else:
                # No durable transaction means the engine never reached input.
                # Clear only this unsent request and recompute fresh item/price.
                state['listing1078_request'] = None
                journal.set(character, 'refill', state)
        if not runtime.refill_enabled(character):
            return _blocked('refill_paused_or_global_stop')
        from conquest.merchants.owned_booth_panel_1078 import pending as panel_pending, step as panel_step
        if panel_pending(journal,character):
            panel_result = panel_step(ui,character,snapshot)
            if panel_result is not None:
                return {'state':'owned_panel_probe', **panel_result}
        if not schedule.due() and journal.get(character, 'new_stock', False):
            start(runtime, character)
            state = schedule.state()
        if not schedule.due():
            return {'state': 'waiting_interval', 'next_check': state['next_check']}
        if state.get('pending') and state.get('listing1078_engine') != 1:
            return _blocked('legacy_refill_cursor_needs_reconciliation')
        if not snapshot['booth_open']:
            from conquest.merchants.owned_booth_panel_1078 import prepare_due
            panel_result = prepare_due(ui, character, snapshot)
            if panel_result is not None:
                return panel_result
        if (snapshot['map_id'] != 1036 or snapshot['hp'] <= 0
                or not snapshot['own_booth_uid'] or not snapshot['booth_open']):
            return _blocked('owned_booth_open_required')
        if snapshot['trade'] is not None or snapshot['request'] is not None:
            return _blocked('merchant_modal_open')
        if not 0 <= time.time()-snapshot['timestamp'] <= 2:
            return _blocked('merchant_observation_expired')
        if len(snapshot['booth']) >= 32 or not snapshot['inventory']:
            # A read-only full/empty capacity observation needs no input
            # qualification or farmer handoff. Pending transactions still
            # block any claim that the check completed.
            if journal.pending(character):
                return _blocked('merchant_transaction_needs_reconciliation')
            schedule.complete('booth_full' if snapshot['inventory'] else 'no_stock',
                              listed=state.get('listed', 0) if state.get('pending') else 0)
            journal.set(character, 'new_stock', False)
            release_completed_refill(ui, character)
            return {'state': 'capacity_checked'}
        try:
            require(journal, character, snapshot)
        except (ValueError, KeyError, TypeError) as error:
            return _blocked(str(error))
        control = ui.app.control.snapshot()
        if control.get('paused'):
            return _blocked('farmer_paused')
        from conquest.merchants.listing_plan_1078 import plan
        queue = plan(runtime, character, snapshot)
        journal.set(character, 'inventory_queue', [row['uid'] for row in queue])
        eligible = [row for row in queue if row['price'] is not None]
        if not eligible:
            schedule.complete('completed', listed=state.get('listed', 0) if state.get('pending') else 0,
                              deferred=len(queue))
            journal.set(character, 'new_stock', False)
            release_completed_refill(ui, character)
            return {'state': 'unknown_prices_deferred', 'deferred': len(queue)}
        if control.get('enabled') or not ui.safe_to_yield():
            from conquest.merchants.listing_handoff_1078 import request_handoff
            key = request_handoff(runtime, character)
            return {**_blocked('waiting_farmer_handoff',
                              'Existing route controller must grant a fresh memory-verified safe input window'),
                    'handoff_request_id': key}
        profile = _profile(character)
        farmer = _farmer_safe_market(ui)
        _policy(ui, character, profile, control, farmer_target=farmer,
                merchant_intent=_merchant_intent(runtime, character), scheduled_refill=True)
        from conquest.input_probe import MessageTarget
        observer = runtime.observers.get(character)
        if observer is None or observer.adapter.identity != snapshot['identity']:
            return _blocked('merchant_attachment_changed')
        native = MessageTarget(snapshot['identity']['pid'], observer.hwnd).snapshot()
        if native['root_hwnd'] != observer.hwnd:
            return _blocked('native_owner_surface_unavailable',
                            'Listing requires the identity-verified native top-level client surface')
        if not runtime.can_start_work(38):
            return _blocked('listing_work_budget_insufficient',
                            'Wait for a fresh safe listing grant with thirty-eight seconds remaining')
        selected = eligible[0]
        item = next(item for item in snapshot['inventory'] if item['uid'] == selected['uid'])
        request = {'action': 'merchant-booth-list-once-1078', 'character': character,
                   'request_id': 'booth-list1078-refill-'+uuid.uuid4().hex,
                   'item_uid': item['uid'], 'item_fingerprint': _item_fingerprint(item),
                   'price': selected['price'], 'expected_identity': snapshot['identity'],
                   'expected_character_uid': snapshot['character_uid'],
                   'expected_own_booth_uid': snapshot['own_booth_uid']}
        schedule.start()
        schedule.checkpoint([row['uid'] for row in queue], deferred=len(queue)-len(eligible))
        state = schedule.state()
        state.update(listing1078_engine=1, listing1078_request=request)
        journal.set(character, 'refill', state)
        receipt = dispatch(ui, request, scheduled_refill=True)
        return {'state': 'listing_pending', 'request_id': request['request_id'],
                'phase': receipt['phase']}
    except OwnedPeerUnavailable as error:
        from conquest.merchants.listing_handoff_1078 import release_ungranted_unavailable_peer
        released = release_ungranted_unavailable_peer(ui, error.character)
        return {**_blocked('owned_peer_observation_unavailable', str(error)),
                'unavailable_peer': error.character,
                'ungranted_peer_handoff_released': released}
    except (ValueError, OSError, KeyError, TypeError, CaptureUnavailable) as error:
        return _blocked('refill_precondition_unavailable', str(error))
    finally:
        runtime.listing1078_lock.release()
