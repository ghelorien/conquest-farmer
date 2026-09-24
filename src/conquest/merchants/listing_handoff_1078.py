"""Reuse the route controller's bounded, memory-verified farmer input grant."""
import os
import time
import re
import uuid

from conquest.capture import CaptureUnavailable

SCOPE = 'listing_1078'
AUTHORITY = 'listing_handoff_1078'


def _safe_health(ui, control, expected_target=None, *, grant_check=lambda: None):
    """Retry only cached observation gaps; never retain authority through danger."""
    from conquest.safe_reload import clear_observation, CLEARANCE
    deadline = time.monotonic()+.6
    target_binding = expected_target

    def permission():
        current = ui.app.control.snapshot()
        if (ui.closed or ui.app.closing or ui.runtime.stop_event.is_set()
                or ui.coordinator.stopped or ui.coordinator.manual_active()
                or ui.runtime.manual_handoff_status() is not None
                or ui.coordinator.manual_session_blocked('Farmer')
                or ui.coordinator.manual_session_blocked(ui.coordinator.owner)
                or control.get('enabled') is not False or control.get('paused')
                or any(current.get(key) != control.get(key)
                       for key in ('enabled', 'paused', 'revision'))):
            raise CaptureUnavailable('Farmer handoff rejected: Stop, manual input or control revision changed')
        grant_check()

    while True:
        permission()
        observer = getattr(ui.app, 'observer', None)
        bridge = getattr(observer, 'bridge', None)
        if (observer is None or bridge is None or bridge.read_only
                or not getattr(ui.app.attachment, 'attached', False)
                or not getattr(ui.app.attachment, 'ready', False)
                or bridge.operations.session is not observer.session
                or bridge.operations.target.hwnd != observer.operations.target.hwnd
                or bridge.info_path.name != f'embedded-worker-{os.getpid()}.json'):
            raise CaptureUnavailable('Farmer handoff rejected: current attached worker is unavailable')
        health = bridge.health()
        if (ui.app.observer is not observer or observer.bridge is not bridge
                or not getattr(ui.app.attachment, 'attached', False)):
            raise CaptureUnavailable('Farmer handoff rejected: attached worker changed')
        permission()
        data = health.get('embedded_controls') or {}
        target = health.get('target')
        failures = []
        if health.get('profile_id') != ui.runtime.manual_target('Farmer'):
            failures.append('farmer_profile_changed')
        if not isinstance(target, dict) or target_binding is not None and target != target_binding:
            failures.append('farmer_process_changed')
        if data.get('external_execution') is not False:
            failures.append('farmer_execution_not_released')
        if data.get('manual_input_fence') or data.get('manual_mouse'):
            failures.append('manual_input')
        if any((data.get('control') or {}).get(key) != control.get(key)
               for key in ('enabled', 'paused', 'revision')):
            failures.append('worker_control_changed')
        life, monsters = data.get('life'), data.get('monsters')
        if life:
            if life['dead_candidate'] or life['current_hp'] < life['max_hp']*.6:
                failures.append('farmer_dead_or_low_health')
            if isinstance(monsters, list):
                x, y = life['position']
                if any(m.get('alive') is not False and max(abs(m['position'][0]-x),
                       abs(m['position'][1]-y)) < CLEARANCE for m in monsters):
                    failures.append('nearby_living_or_unknown_monster')
        age = time.time()-data.get('observed_at', 0)
        if age < 0:
            failures.append('observation_clock_changed')
        if failures:
            raise CaptureUnavailable('Farmer handoff rejected: '+', '.join(failures))
        if target_binding is None:
            target_binding = dict(target)
        if clear_observation(health):
            permission()
            return health
        gap = not data.get('observations_available') or age > 1
        reason = ('observation_unavailable' if not data.get('observations_available') else
                  'observation_stale' if age > 1 else 'observation_shape_invalid')
        if not gap or time.monotonic() >= deadline:
            raise CaptureUnavailable('Farmer handoff rejected: '+reason+
                f'; age={age:.3f}s; note={data.get("observation_note", "unavailable")}')
        time.sleep(min(.04, max(0, deadline-time.monotonic())))


def parked(ui, expected_target=None):
    """Fresh native worker proof, also usable before activating a grant."""
    return _safe_health(ui, ui.app.control.snapshot(), expected_target)


def _preview(ui, body):
    from conquest.merchants.booth_listing_once_1078 import (
        _profile, _validate_snapshot, _price_plan, _row, ITEM_FIELDS,
    )
    from conquest.merchants.observe_1078 import observe
    from conquest.merchants.pricing import validate_booth_price
    character = body['character']
    if _row(ui.runtime.journal, body['request_id']):
        raise ValueError('Listing request already exists; reconcile it or request exact cancellation')
    if (not isinstance(body['item_fingerprint'], dict)
            or set(body['item_fingerprint']) != set(ITEM_FIELDS)):
        raise ValueError('Exact listing item fingerprint required')
    validate_booth_price(body['price'])
    snapshot = observe(ui.runtime, character, listing_preflight=True)
    if not snapshot['closed_modal']:
        raise ValueError('Trade/request windows must be closed for listing admission')
    snapshot = {**snapshot, 'trade': None, 'request': None}
    item = _validate_snapshot(snapshot, _profile(character), body)
    if (not snapshot['closed_modal'] or not snapshot['profile_uid_verified']
            or not snapshot['listing_preflight']['layout_observed']
            or snapshot['listing_preflight']['price_modal'].get('observed') is not False
            or _price_plan(ui, character, snapshot, item)['price'] != body['price']):
        raise ValueError('Exact highest-priced listing preview is no longer eligible')
    return snapshot


def request_operator(ui, body):
    """Read-only admission; the resulting key grants no input by itself."""
    common = {'action', 'character', 'request_id'}
    fields = common | {'item_uid', 'item_fingerprint', 'price', 'expected_identity',
                       'expected_character_uid', 'expected_own_booth_uid'}
    if set(body) not in (common, fields) or not re.fullmatch(
            r'booth-list1078-[A-Za-z0-9_-]{8,80}', str(body.get('request_id', ''))):
        raise ValueError('An exact listing request or unresolved cancellation request is required')
    from conquest.merchants.booth_listing_once_1078 import _profile
    profile = _profile(body['character'])
    if (ui.grant or ui.coordinator.owner or ui.runtime.handoff
            or ui.runtime.stop_event.is_set() or ui.coordinator.stopped
            or ui.runtime.manual_handoff_status() is not None):
        raise ValueError('Release other handoffs and preserve Stop before requesting listing input')
    request = dict(body)
    mode = 'cancel' if set(body) == common else 'list'
    if mode == 'cancel':
        from conquest.merchants.booth_listing_cancel_1078 import _pending
        _pending(ui.runtime.journal, body['request_id'], body['character'])
    else:
        request['action'] = 'merchant-booth-list-once-1078'
        _preview(ui, request)
    key = 'listing1078:'+uuid.uuid4().hex
    record = {'request_id': key, 'listing_request_id': body['request_id'],
              'profile_id': profile.id, 'mode': mode, 'request': request,
              'expires_at': time.time()+120}
    with ui.runtime.lock:
        if ui.runtime.handoff or ui.grant or ui.coordinator.owner:
            raise ValueError('Another handoff began during listing admission')
        ui.runtime.journal.set(body['character'], AUTHORITY, record)
        ui.runtime.handoff = key
    return {'requested': key, 'scope': SCOPE, 'character': str(body['character']),
            'listing_request_id': body['request_id'], 'mode': mode,
            'admission_expires_at': record['expires_at'], 'max_seconds': 45}


def validate_grant(ui, body):
    character, key = body['character'], body['request_id']
    from conquest.merchants.booth_listing_once_1078 import _profile
    profile = _profile(character)
    if (ui.coordinator.owner or ui.coordinator.stopped or ui.runtime.stop_event.is_set()
            or ui.coordinator.manual_active() or ui.runtime.manual_handoff_status() is not None
            or ui.coordinator.manual_session_blocked(character)
            or ui.coordinator.manual_session_blocked('Farmer')
            or getattr(ui.runtime, 'delivery_window', None)):
        raise ValueError('Listing handoff is blocked by Stop, manual control or another owner')
    from conquest.merchants.observe_1078 import observe
    if key.startswith(f'merchant-refill:{character}:'):
        if not ui.runtime.refill_enabled(character):
            raise ValueError('Refill is paused')
        from conquest.merchants.owned_booth_panel_1078 import pending, validate_grant as panel_grant
        panel = pending(ui.runtime.journal,character)
        if panel and panel['phase'] == 'prepared':
            authority = panel_grant(ui,character)
            health = parked(ui)
            if body['revision'] != ui.app.control.snapshot()['revision']:
                raise ValueError('Farmer revision changed during panel admission')
            return {**authority,'farmer_target':health['target']}
        from conquest.merchants.listing_capability_1078 import require
        snapshot = observe(ui.runtime, character, listing_preflight=True)
        require(ui.runtime.journal, character, snapshot)
        if (not snapshot['closed_modal'] or snapshot['map_id'] != 1036
                or snapshot['hp'] <= 0 or not snapshot['booth_open']
                or not snapshot['own_booth_uid']):
            raise ValueError('Refill requires a living merchant with the exact open Market booth')
        authority = {'mode': 'refill', 'profile_id': profile.id}
        if ui.runtime.refills[character].state().get('listing1078_request'):
            from conquest.merchants.refill_1078 import pending_cancel
            request = pending_cancel(ui.runtime, character, snapshot, preflight=True)
            authority.update(mode='refill_cancel', listing_request_id=request['request_id'])
    else:
        authority = ui.runtime.journal.get(character, AUTHORITY) or {}
        if (authority.get('request_id') != key or authority.get('profile_id') != profile.id
                or authority.get('expires_at', 0) <= time.time()):
            raise ValueError('Exact operator listing admission expired or changed')
        if authority['mode'] == 'list':
            _preview(ui, authority['request'])
        elif authority['mode'] == 'cancel':
            from conquest.merchants.booth_listing_cancel_1078 import _pending, _unchanged
            before = _pending(ui.runtime.journal, authority['listing_request_id'], character)
            snapshot = observe(ui.runtime, character)
            if not snapshot['closed_modal']:
                raise ValueError('Trade/request windows must be closed for exact cancellation')
            _unchanged({**snapshot, 'trade': None, 'request': None}, before, profile)
        else:
            raise ValueError('Unknown listing authority')
    health = parked(ui)
    if body['revision'] != ui.app.control.snapshot()['revision']:
        raise ValueError('Farmer revision changed during listing admission')
    return {**authority, 'farmer_target': health['target']}


def scope_allows(ui, character, *, request_id=None, scheduled=False, cleanup=False, body=None):
    grant = getattr(ui, 'grant', None) or {}
    if grant.get('scope') != SCOPE:
        return True
    authority = grant.get('listing_authority') or {}
    if authority.get('mode') == 'open_panel':
        return False  # Panel admission never authorizes listing or cancellation.
    from conquest.merchants.booth_listing_once_1078 import _profile
    if (grant.get('character') != character
            or authority.get('profile_id') != _profile(character).id):
        return False
    if authority.get('mode') == 'refill':
        return scheduled and not cleanup
    if authority.get('mode') == 'refill_cancel':
        return (cleanup and not scheduled and body is None
                and request_id is not None
                and authority.get('listing_request_id') == request_id
                and ui.runtime.refill_enabled(character))
    return (not scheduled and cleanup == (authority.get('mode') == 'cancel')
            and (request_id is None or authority.get('listing_request_id') == request_id)
            and (body is None or authority.get('request') == body))


def farmer_safe(ui, expected_target=None, *, deadline=None):
    """Observe safe stopped input ownership; never change farming intent."""
    grant = getattr(ui, 'grant', None)
    if not grant:
        if ui.coordinator.purpose in (None,'booth_listing_1078_once','owned_booth_panel_1078'):
            # An already stopped route needs no artificial Market visit. The
            # same native parking proof used for grant admission still requires
            # fresh life/threats, exact worker identity, released execution,
            # unchanged Off revision, and no manual ownership at every boundary.
            if not ui.safe_to_yield():
                raise CaptureUnavailable('Farmer route has not released input for merchant refill')
            def idle_current():
                if (getattr(ui,'grant',None) is not None or not ui.safe_to_yield()
                        or deadline is not None and time.monotonic()>=deadline):
                    raise CaptureUnavailable('Stopped Farmer input ownership changed during refill')
            health=_safe_health(ui,ui.app.control.snapshot(),expected_target,grant_check=idle_current)
            return health['target']
        from conquest.merchants.booth_probe_1078 import _farmer_safe_market
        return _farmer_safe_market(ui, expected_target)
    fence = getattr(ui.coordinator, 'fence', None)
    control = ui.app.control.snapshot()
    token = fence.check() if fence else None
    if (token is None or token != fence.active
            or token.request_id != grant.get('request_id')
            or token.request_id != ui.runtime.handoff
            or token.revision != control.get('revision')
            or grant.get('revision') != control.get('revision')
            or token.expires_at != grant.get('expires_at')
            or grant.get('safe') is not True
            or not 0 < token.expires_at-time.time() <= 60
            or control.get('enabled') is not False or control.get('paused')):
        raise CaptureUnavailable('Farmer input handoff changed or expired')
    def grant_current():
        fence.check(token)
        if (token != fence.active or ui.grant != grant
                or ui.runtime.handoff != token.request_id
                or deadline is not None and time.monotonic() >= deadline
                or token.farmer_profile_id != ui.runtime.manual_target('Farmer')):
            raise CaptureUnavailable('Farmer input handoff changed during observation refresh')

    health = _safe_health(ui, control, expected_target, grant_check=grant_current)
    controls = health.get('embedded_controls') or {}
    target = health.get('target')
    # Hunting safety is a live monster/HP/position observation, not a map-name
    # assumption. Market grants also retain their independently checked visit.
    if token.scope == 'market_visit' and (controls.get('life') or {}).get('map_id') != 1036:
        raise CaptureUnavailable('Farmer left the granted Market service visit')
    if token.scope == SCOPE:
        authority = grant.get('listing_authority') or {}
        if (ui.coordinator.purpose not in (None, 'booth_listing_1078_once','owned_booth_panel_1078')
                or ui.coordinator.owner is not None and ui.coordinator.owner != grant.get('character')
                or authority.get('farmer_target') != target
                or token.expires_at-time.time() > 45):
            raise CaptureUnavailable('Listing grant cannot authorize another input purpose or farmer')
    if token.scope not in ('market_visit', 'hunting', SCOPE):
        raise CaptureUnavailable('Unknown farmer input handoff scope')
    fence.check(token)
    return target


def request_handoff(runtime, character):
    """Ask the existing route loop to park; do not modify its saved intent."""
    with runtime.lock:
        if runtime.handoff is None:
            runtime.handoff = f'merchant-refill:{character}:{time.time_ns()}'
        return runtime.handoff


def release_completed_refill(ui, character):
    """Retire an exact unsent completed request through normal release logic."""
    from conquest.merchants.journal import character_name
    from conquest.merchants.booth_listing_once_1078 import WORKER_LOCK, WORKERS
    from conquest.merchants.background_probe import probe_busy
    runtime, coordinator = ui.runtime, ui.coordinator
    peer = character_name(character)
    with runtime.lock:
        request = runtime.handoff
        if (not isinstance(request,str)
                or not re.fullmatch(r'merchant-refill:'+re.escape(peer)+r':\d+',request)
                or ui.grant is not None or ui.grant_fence.active is not None
                or request in ui.grant_fence.requests
                or runtime.delivery_window or runtime.refill_window
                or getattr(ui,'host_release_pending',None)
                or runtime.manual_handoff_status() is not None
                or runtime.stop_event.is_set() or coordinator.stopped):
            return False
        if not coordinator.lock.acquire(blocking=False):return False
        try:
            state=runtime.refills[character].state()
            if (state.get('pending') is not False
                    or state.get('status') not in ('completed','no_stock','booth_full')
                    or state.get('listing1078_request')
                    or coordinator.owner is not None or coordinator.manual_active()
                    or any(row.get('holds_automation') for row in coordinator.manual_sessions.values())
                    or runtime.farmer_bot_owned() or getattr(ui,'calibrating',None)
                    or probe_busy(ui)):
                return False
            workers=[getattr(ui,name,None) for name in
                     ('delivery_probe_thread','trade_qualification_prep_thread','background_thread')]
            workers.extend(getattr(ui,'connect_threads',{}).values())
            if any(worker is not None and worker.is_alive() for worker in workers):return False
            with WORKER_LOCK:
                if any(worker.is_alive() for worker in WORKERS.values()):return False
            with runtime.journal.db() as db:
                if db.execute("SELECT 1 FROM transactions WHERE phase NOT IN "
                              "('verified','aborted','operator_overridden') LIMIT 1").fetchone():
                    return False
            if runtime.handoff!=request:return False
            # Same lock as grant admission: no active grant can appear between
            # the guards and this exact-ID release. Saved controls stay intact.
            return ui.dispatch({'action':'handoff-release','request_id':request}).get('released') is True
        finally:
            coordinator.lock.release()


def release_ungranted_unavailable_peer(ui, character):
    """Forfeit only an unsent refill request from a peer that cannot be read.

    A request with any grant, active input, or transaction stays held for
    reconciliation. This does not cancel a listing or modify saved refill intent.
    """
    from conquest.merchants.journal import character_name
    from conquest.merchants.owned_booth_panel_1078 import pending as panel_pending
    peer = character_name(character)
    runtime, coordinator = ui.runtime, ui.coordinator
    with runtime.lock:
        request = runtime.handoff
        if (not isinstance(request, str) or
                not re.fullmatch(r'merchant-refill:' + re.escape(peer) + r':\d+', request)
                or ui.grant is not None or ui.grant_fence.active is not None
                or request in ui.grant_fence.requests
                or coordinator.owner is not None or coordinator.manual_active()
                or runtime.manual_handoff_status() is not None
                or runtime.stop_event.is_set() or coordinator.stopped
                or runtime.delivery_window or runtime.refill_window
                or getattr(ui, 'host_release_pending', None)):
            return False
        panel = panel_pending(runtime.journal, peer)
        if panel and panel['phase'] not in ('verified','aborted','operator_overridden'):
            return False
        from conquest.merchants.background_probe import probe_busy
        if probe_busy(ui) or runtime.journal.pending(peer):
            return False
        with runtime.journal.db() as db:
            if (db.execute("SELECT 1 FROM transactions WHERE phase NOT IN "
                           "('verified','aborted','operator_overridden') LIMIT 1").fetchone()
                    or db.execute("SELECT 1 FROM delivery_admissions WHERE phase NOT IN "
                                  "('verified','aborted','operator_overridden') LIMIT 1").fetchone()):
                return False
        if runtime.handoff != request:
            return False
        runtime.handoff = None
        return True
