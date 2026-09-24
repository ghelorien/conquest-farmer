"""Exact-item 1078 listing engine, with receipt-gated foreground refill reuse.

This path is independent of MerchantDriver and does not qualify general 1078
merchant input. A request ID is consumed once, including on a crash or an
uncertain mouse-down. Only fresh native memory may settle its receipt.
"""

from contextlib import nullcontext
import ctypes as c
from ctypes import wintypes as w
import json
import re
import struct
import threading
import time

from conquest.capture import CaptureUnavailable
from conquest.character_context import registry
from conquest.memory import MemorySession
from conquest.memory_build_layout import CLIENT_SHA256_1078, read_build_layout
from conquest.merchants.memory import GuiReader, HoverNotReady, unpack
from conquest.merchants.pricing import validate_booth_price
from conquest.merchants.reader_1078 import open_read_only_1078
from conquest.valuables import DRAGONBALL_TYPES, require_marketable

from conquest.merchants.booth_probe_1078 import (
    _farmer_journals_clear, _hovered_window, _row, _window,
)
from conquest.merchants.listing_handoff_1078 import farmer_safe as _farmer_safe_market


KIND = 'booth_listing_1078_once'
WORKERS = {}
WORKER_LOCK = threading.RLock()
ITEM_FIELDS = ('uid', 'type_id', 'name', 'plus', 'gem1', 'gem2', 'bound', 'quantity')
OWNERSHIP_FIELDS = ('identity', 'character', 'character_uid', 'server', 'map_id',
                    'position', 'hp', 'silver', 'capacity', 'inventory', 'booth',
                    'own_booth_uid', 'booth_open', 'trade', 'request')


def _profile(character):
    profiles = registry()
    if profiles is None:
        raise ValueError('Configured merchant profile is required')
    profile = profiles.resolve(getattr(character, 'profile_id', character),
                               role='Merchant', server='America')
    if (not profile.local_enabled or profile.name not in ('Dutch', 'Spiritual')
            or not profile.character_uid):
        raise ValueError('A configured, UID-pinned local merchant is required')
    return profile


def _item_fingerprint(item):
    return {key: item[key] for key in ITEM_FIELDS}


def _stock(items):
    result = {}
    for item in items:
        uid = item['uid']
        if type(uid) is not int or uid <= 0 or uid in result:
            raise ValueError('Merchant stock contains an ambiguous UID')
        result[uid] = _item_fingerprint(item)
    return result


def _selected(snapshot, request):
    matches = [item for item in snapshot['inventory'] if item['uid'] == request['item_uid']]
    if len(matches) != 1 or _item_fingerprint(matches[0]) != request['item_fingerprint']:
        raise ValueError('Exact requested item changed or left merchant inventory')
    item = matches[0]
    require_marketable(item)
    if item['bound'] or item['type_id'] in DRAGONBALL_TYPES or item['type_id'] == 1088001:
        raise ValueError('Protected or loose Meteor stock cannot be listed')
    return item


def _validate_snapshot(snapshot, profile, request):
    if (snapshot['identity'] != request['expected_identity']
            or snapshot['character_uid'] != profile.character_uid
            or snapshot['character_uid'] != request['expected_character_uid']
            or snapshot['character'] != profile.name or snapshot['server'] != profile.server
            or snapshot['own_booth_uid'] != request['expected_own_booth_uid']
            or not snapshot['own_booth_uid'] or not snapshot['booth_open']
            or snapshot['map_id'] != 1036 or snapshot['hp'] <= 0
            or snapshot['trade'] is not None or snapshot['request'] is not None
            or len(snapshot['booth']) >= 32
            or not 0 <= time.time() - snapshot['timestamp'] <= 1):
        raise ValueError('Merchant identity, owned booth, capacity, or closed trade changed')
    return _selected(snapshot, request)


def _merchant_intent(runtime, character):
    return {'operations': runtime.journal.get(character, 'enabled', False) is True,
            'refill': runtime.journal.get(character, 'refill_enabled', True) is True}


def _policy(ui, character, profile, control, *, request_id=None, deadline=None,
            farmer_target=None, merchant_intent=None, phases=('prepared',),
            scheduled_refill=False, explicit_cleanup=False):
    runtime, coordinator = ui.runtime, ui.coordinator
    from conquest.merchants.listing_handoff_1078 import scope_allows
    if not scope_allows(ui, character, request_id=request_id,
                        scheduled=scheduled_refill, cleanup=explicit_cleanup):
        raise CaptureUnavailable('Listing scope belongs to another exact request or operation')
    operator_scope = (getattr(ui, 'grant', None) or {}).get('scope') == 'listing_1078'
    current = ui.app.control.snapshot()
    intent = _merchant_intent(runtime, character)
    if (ui.closed or ui.app.closing or runtime.stop_event.is_set() or coordinator.stopped
            or coordinator.manual_active() or runtime.manual_handoff_status() is not None
            or coordinator.manual_session_blocked(character)
            or coordinator.manual_session_blocked('Farmer')
            or not ui.safe_to_yield()
            or any(current.get(key) != control.get(key)
                   for key in ('enabled', 'paused', 'revision'))
            or control['enabled'] or control.get('paused')
            or not explicit_cleanup and not any(intent.values())
            or scheduled_refill and not intent['refill']
            or merchant_intent is not None and (
                intent['refill'] != merchant_intent['refill'] if scheduled_refill
                else intent != merchant_intent)
            or _profile(character) != profile
            or getattr(runtime, 'delivery_window', None)
            or getattr(runtime, 'refill_window', None) and not (
                (scheduled_refill or explicit_cleanup or operator_scope) and getattr(ui, 'grant', None)
                and runtime.refill_window == ui.grant.get('request_id') == runtime.handoff)
            or runtime.refilling or runtime.connecting or ui.calibrating
            or deadline is not None and time.monotonic() >= deadline):
        raise CaptureUnavailable('One-shot listing stopped or lacks a safe farmer handoff')
    _farmer_safe_market(ui, farmer_target, deadline=deadline)
    from conquest.merchants.delivery_reservation import active
    from conquest.merchants.background_probe import probe_busy
    if active(runtime.journal, character) or probe_busy(ui):
        raise CaptureUnavailable('Another delivery or diagnostic owns the merchant')
    _farmer_journals_clear(runtime)
    with runtime.journal.db() as db:
        pending = [dict(row) for row in db.execute(
            "SELECT id,kind,phase FROM transactions WHERE phase NOT IN "
            "('verified','aborted','operator_overridden')")]
    if request_id is None:
        if pending:
            raise ValueError('Reconcile the existing merchant transaction first')
    elif (len(pending) != 1 or pending[0]['id'] != request_id
          or pending[0]['kind'] != KIND or pending[0]['phase'] not in phases):
        raise ValueError('The one-shot listing journal changed')


def _grids(gui, snapshot, uid):
    windows = gui.windows()
    inv = _window(windows, 'Inventory/', prefix=True)
    booth = _window(windows, 'Booth')
    child = _window(windows, 'Booth/', prefix=True)
    inventory = gui.table(inv, '##ItemTable')
    table = gui.table(child, 'BoothTable')
    columns = inventory['columns']
    if (len(columns) != 10 or inventory['row_height'] != 40
            or any(columns[n+1]['content_x']-columns[n]['content_x'] != 40
                   for n in range(len(columns)-1)) or table['row_height'] != 64):
        raise ValueError('1078 inventory/booth grid schema changed')
    item = next(item for item in snapshot['inventory'] if item['uid'] == uid)
    slot = item['slot']
    if type(slot) is not int or not 0 <= slot < 40:
        raise ValueError('Selected inventory slot is unavailable')
    source = (columns[slot % 10]['content_x']+20,
              inventory['outer'][1]+slot // 10 * 40+20)
    left, top, right, bottom = inventory['clip']
    if not left+2 < source[0] < right-2 or not top+2 < source[1] < bottom-2:
        raise ValueError('Selected item is clipped; one-shot listing never scrolls')
    x, y, width, height = booth['geometry']
    destination = (x+width/2, y+height/2)
    cx, cy, cw, ch = child['geometry']
    if not cx+2 < destination[0] < cx+cw-2 or not cy+2 < destination[1] < cy+ch-2:
        raise ValueError('Owned booth drop target is outside its live child panel')
    return {'inventory_window': inv, 'booth_window': booth, 'booth_child': child,
            'inventory_table': inventory, 'booth_table': table,
            'source': source, 'destination': destination}


def _modal(gui, model, snapshot, uid):
    """A geometry candidate only; native OK hover is mandatory before press."""
    from conquest.merchants.listing_preflight_1078 import _modal_candidate, _modal_code_verified
    _modal_code_verified(gui.session, read_build_layout(gui.session))
    window = _window(gui.windows(), 'Add Item to Booth')
    raw = gui.session.read_block(window['address'], 0x250)
    x, y, width, height = struct.unpack_from('<4f', raw, 0x18)
    end_x, button_y = struct.unpack_from('<2f', raw, 0xe8)
    button_height = struct.unpack_from('<f', raw, 0x114)[0]
    start_x, start_y = struct.unpack_from('<2f', raw, 0xf0)
    if (tuple(window['geometry']) != (x, y, width, height)
            or (width, height) != (264., 92.) or button_height != 18
            or end_x != x+256 or button_y != y+66
            or (start_x, start_y) != (x+8, y+26)
            or gui.session.read_block(window['address']+0x18, 16) != raw[0x18:0x28]):
        raise ValueError('1078 native price dialog geometry changed')
    candidate = _modal_candidate(gui.session, model, snapshot)
    if candidate['candidate_selected_item_uid'] != uid:
        raise ValueError('Price dialog selected another item')
    # Loaded renderer draws 120px OK first, then Cancel, within the 264px row.
    # These are cursor destinations, never input qualification by themselves.
    return window, {'##Amount': (start_x+64, button_y-13),
                    'OK': (x+68, button_y+9),
                    'Cancel': (end_x-60, button_y+9)}, candidate['candidate_price_buffer_text']


def _price_plan(ui, character, snapshot, item):
    """Reobserve every configured owned peer; never silently lose its floor."""
    from conquest.merchants.listing_plan_1078 import plan
    plans = plan(ui.runtime, character, snapshot)
    eligible = [plan for plan in plans if plan.get('price') is not None]
    if not eligible or eligible[0]['uid'] != item['uid']:
        raise ValueError('Only the highest-valued reliably priced inventory item may fill the booth')
    return eligible[0]


def _same_quote(before, after):
    return all(before.get(key) == after.get(key) for key in
               ('uid', 'price', 'attributes', 'reason', 'reference',
                'source_observed_at'))


def _listed(before, after, request):
    if (any(after[key] != before[key] for key in
            ('identity', 'character', 'character_uid', 'server', 'map_id',
             'silver', 'capacity', 'own_booth_uid'))
            or after['hp'] <= 0 or not after['booth_open']
            or after['trade'] is not None or after['request'] is not None):
        return False
    uid = request['item_uid']
    inventory_before, inventory_after = _stock(before['inventory']), _stock(after['inventory'])
    booth_before, booth_after = _stock(before['booth']), _stock(after['booth'])
    booth_prices_before = {item['uid']: item['price'] for item in before['booth']}
    booth_prices_after = {item['uid']: item['price'] for item in after['booth']
                          if item['uid'] != uid}
    expected = request['item_fingerprint']
    matches = [item for item in after['booth']
               if item['uid'] == uid and item['price'] == request['price']
               and _item_fingerprint(item) == expected]
    return (inventory_before.get(uid) == expected and uid not in booth_before
            and len(matches) == 1 and uid not in inventory_after
            and inventory_after == {key: value for key, value in inventory_before.items() if key != uid}
            and booth_after == {**booth_before, uid: expected}
            and booth_prices_after == booth_prices_before)


def _status(journal, request_id, character, *, runtime=None):
    row = _row(journal, request_id)
    if not row or row['kind'] != KIND or row['character'] != character:
        raise ValueError('No matching one-shot 1078 listing receipt')
    steps = journal.trace(request_id)
    result = json.loads(row['result_json'] or '{}')
    cancel_failure = next((json.loads(step['payload']) for step in reversed(steps)
                           if step['stage'] == 'cancel_failed'), None)
    qualified, qualification_blocker = None, 'fresh_merchant_observation_required'
    if runtime is not None:
        from conquest.merchants.listing_capability_1078 import require
        from conquest.merchants.observe_1078 import observe
        try:
            require(journal, character, observe(runtime, character))
            qualified, qualification_blocker = True, None
        except (ValueError, OSError, KeyError, TypeError, CaptureUnavailable) as error:
            qualified, qualification_blocker = False, str(error)
    return {'request_id': request_id, 'phase': row['phase'],
            'last_stage': steps[-1]['stage'] if steps else None,
            'confirmation_marker': any(step['stage'] == 'confirm_press'
                                       for step in steps),
            'cancellation_marker': any(step['stage'] == 'cancel_press' for step in steps),
            'cancel_failure': cancel_failure,
            'result': result, 'needs_attention': row['phase'] not in ('verified', 'aborted'),
            'replay_allowed': False, 'routine_refill_qualified': qualified,
            'routine_refill_qualification_scope': 'foreground_open_booth_listing_1078',
            'routine_refill_qualification_blocker': qualification_blocker}


def _unchanged_before_input(before, profile):
    """Certify pointer-only failure without trusting the old snapshot alone."""
    request = before['request']
    identity = request['expected_identity']
    with MemorySession(identity['pid'], CLIENT_SHA256_1078) as session:
        if session.identity != identity:
            return False
        reader = open_read_only_1078(session, profile.name)
        gui = GuiReader.for_session(session)
        first = reader.read_manual_ownership()
        if first['trade'] is not None or first['request'] is not None:
            return False
        if any(win['name'] == 'Add Item to Booth' for win in gui.windows()):
            return False
        time.sleep(.25)
        second = reader.read_manual_ownership()
        if second['trade'] is not None or second['request'] is not None:
            return False
        if any(win['name'] == 'Add Item to Booth' for win in gui.windows()):
            return False
        session.assert_identity()
        old = before['snapshot']
        return (all(first[key] == second[key] for key in OWNERSHIP_FIELDS)
                and all(second[key] == old[key] for key in OWNERSHIP_FIELDS
                        if key in old)
                and second['character_uid'] == profile.character_uid
                and second['own_booth_uid'] == request['expected_own_booth_uid'])


def reconcile(ui, request_id, character):
    """Read-only game observation; positive exact proof alone may close the hold."""
    journal = ui.runtime.journal
    row = _row(journal, request_id)
    if not row or row['kind'] != KIND or row['character'] != character:
        raise ValueError('No matching one-shot 1078 listing receipt')
    if row['phase'] in ('verified', 'aborted', 'operator_overridden'):
        return _status(journal, request_id, character)
    before = json.loads(row['before_json'])
    request = before['request']
    steps = journal.trace(request_id)
    marker = any(step['stage'] == 'confirm_press' for step in steps)
    if any(step['stage'] == 'cancel_press' for step in steps):
        if marker:
            raise ValueError('Conflicting listing confirmation and cancellation markers need operator reconciliation')
        from conquest.merchants.booth_listing_cancel_1078 import reconcile_cancel
        return reconcile_cancel(ui, request_id, character)
    if not marker:
        return _status(journal, request_id, character)
    profile = _profile(character)
    identity = request['expected_identity']
    with MemorySession(identity['pid'], CLIENT_SHA256_1078) as session:
        if session.identity != identity:
            return _status(journal, request_id, character)
        reader = open_read_only_1078(session, profile.name)
        first = reader.read_manual_ownership()
        if any(win['name'] == 'Add Item to Booth' for win in GuiReader.for_session(session).windows()):
            return _status(journal, request_id, character)
        time.sleep(.25)
        second = reader.read_manual_ownership()
        if (any(first[key] != second[key] for key in OWNERSHIP_FIELDS)
                or any(win['name'] == 'Add Item to Booth'
                       for win in GuiReader.for_session(session).windows())):
            return _status(journal, request_id, character)
        if _listed(before['snapshot'], second, request):
            session.assert_identity()
            from conquest.merchants.listing_capability_1078 import ENGINE_REVISION, settle
            if before.get('listing_engine_revision') == ENGINE_REVISION:
                settle(journal, request_id, first, second)
            else:
                # Positive ownership proof can close an older receipt, but
                # cannot manufacture the new engine's missing input evidence.
                journal.transition(request_id, 'verified',
                                   {'uid': request['item_uid'], 'price': request['price'],
                                    'owned_booth_uid': request['expected_own_booth_uid'],
                                    'confirmation_attempted': True,
                                    'exact_memory_listing_verified': True,
                                    'foreground_listing_qualified': False})
            attention = journal.get(character, 'attention') or {}
            if (attention.get('kind') == KIND
                    and attention.get('transaction_id') == request_id):
                journal.set(character, 'attention', None)
    return _status(journal, request_id, character)


def dispatch(ui, body, *, scheduled_refill=False):
    """Called only by the existing authenticated MerchantBridge dispatcher."""
    action = body['action']
    allowed = ('merchant-booth-list-once-1078', 'merchant-booth-list-once-status-1078',
               'merchant-booth-list-once-reconcile-1078', 'merchant-booth-list-once-cancel-1078')
    if action not in allowed:
        raise ValueError('Unknown one-shot booth operation')
    common = {'action', 'character', 'request_id'}
    fields = common | {'item_uid', 'item_fingerprint', 'price', 'expected_identity',
                       'expected_character_uid', 'expected_own_booth_uid'}
    if set(body) != (fields if action == allowed[0] else common):
        raise ValueError('One-shot listing requires an exact item, price, and process binding')
    request_id = body['request_id']
    if (not isinstance(request_id, str)
            or not re.fullmatch(r'booth-list1078-[A-Za-z0-9_-]{8,80}', request_id)):
        raise ValueError('Use a unique booth-list1078-prefixed request ID')
    character = body['character']
    profile = _profile(character)
    journal = ui.runtime.journal
    if action == allowed[1]:
        return _status(journal, request_id, character, runtime=ui.runtime)
    if action == allowed[2]:
        return reconcile(ui, request_id, character)
    if action == allowed[3]:
        from conquest.merchants.booth_listing_cancel_1078 import dispatch_cancel
        return dispatch_cancel(ui, request_id, character)
    if (type(body['item_uid']) is not int or body['item_uid'] <= 0
            or not isinstance(body['expected_identity'], dict)
            or not isinstance(body['item_fingerprint'], dict)
            or set(body['item_fingerprint']) != set(ITEM_FIELDS)
            or body['item_fingerprint'].get('uid') != body['item_uid']
            or any(type(body[key]) is not int or body[key] <= 0
                   for key in ('expected_character_uid', 'expected_own_booth_uid'))):
        raise ValueError('Invalid exact item or merchant identity')
    validate_booth_price(body['price'])
    from conquest.merchants.listing_handoff_1078 import scope_allows
    if not scope_allows(ui, character, request_id=request_id, scheduled=scheduled_refill, body=body):
        raise CaptureUnavailable('Listing differs from the exact admitted operator request')
    existing = _row(journal, request_id)
    if existing:
        prior = json.loads(existing['before_json'])
        if prior.get('request') != dict(body):
            raise ValueError('One-shot request ID was used for different work')
        return _status(journal, request_id, character)
    control = ui.app.control.snapshot()
    farmer_target = _farmer_safe_market(ui)
    merchant_intent = _merchant_intent(ui.runtime, character)
    _policy(ui, character, profile, control, farmer_target=farmer_target,
            merchant_intent=merchant_intent, scheduled_refill=scheduled_refill)
    if scheduled_refill and not ui.runtime.can_start_work(20):
        raise CaptureUnavailable('Scheduled listing needs twenty seconds remaining in the current safe grant')
    from conquest.merchants.observe_1078 import observe
    observed = observe(ui.runtime, character, listing_preflight=True)
    if (observed['identity'] != body['expected_identity']
            or observed['character_uid'] != body['expected_character_uid']
            or observed['own_booth_uid'] != body['expected_own_booth_uid']
            or not observed['profile_uid_verified'] or not observed['closed_modal']
            or not observed['listing_preflight']['layout_observed']
            or observed['listing_preflight']['price_modal'].get('observed') is not False):
        raise ValueError('Refresh exact merchant ownership with the price dialog closed')
    item = _selected(observed, body)
    if scheduled_refill:
        from conquest.merchants.listing_capability_1078 import require
        require(journal, character, observed)
    if len(observed['booth']) >= 32:
        raise ValueError('Owned booth has no free listing slot')
    plan = _price_plan(ui, character, observed, item)
    if body['price'] != plan['price']:
        raise ValueError('Requested price differs from the reliable current quote')
    candidates = [client for client in ui.runtime.merchant_windows()
                  if client.identity == observed['identity']]
    if len(candidates) != 1:
        raise ValueError('Expected exactly one native HWND for the selected merchant')
    from conquest.input_probe import MessageTarget
    target = MessageTarget(observed['identity']['pid'], candidates[0].hwnd)
    native = target.snapshot()
    if native['root_hwnd'] != target.hwnd:
        raise ValueError('Listing requires the exact native top-level merchant window')
    _policy(ui, character, profile, control, farmer_target=farmer_target,
            merchant_intent=merchant_intent, scheduled_refill=scheduled_refill)
    if scheduled_refill and not ui.runtime.can_start_work(20):
        raise CaptureUnavailable('Scheduled listing preflight consumed its safe work budget; no input started')
    fence = ui.coordinator.fence
    token = fence.capture() if fence else None
    from conquest.merchants.listing_capability_1078 import ENGINE_REVISION
    before = {'request': dict(body), 'profile_id': profile.id, 'hwnd': target.hwnd,
              'control': control, 'client_sha256': CLIENT_SHA256_1078,
              'farmer_target': farmer_target, 'snapshot': observed,
              'merchant_intent': merchant_intent,
              'price_plan': plan, 'routine_refill_permitted': False,
              'listing_engine_revision': ENGINE_REVISION,
              'scheduled_foreground_refill': scheduled_refill,
              'farmer_grant': dict(ui.grant) if getattr(ui, 'grant', None) else None}
    if not journal.begin(request_id, character, KIND, before):
        return _status(journal, request_id, character)
    worker = threading.Thread(target=_run, args=(ui, character, profile, before, token),
                              daemon=True, name='booth-1078-list-once')
    try:
        with WORKER_LOCK:
            WORKERS[request_id] = worker
            worker.start()
    except BaseException:
        journal.transition(request_id, 'aborted',
                           {'reason': 'Worker did not start', 'confirmation_attempted': False})
        raise
    return _status(journal, request_id, character)


def _type_price(target, price, guard, stage):
    """Enter into a verified empty native field; no Enter, paste, or process write."""
    from conquest.foreground import Input, InputUnion, KeyboardInput
    from conquest.mouse_priority import guarded_send
    from conquest.win32 import bind
    keys = bind(target.backend.user, 'GetAsyncKeyState', [c.c_int], c.c_short)
    send = guarded_send(bind(target.backend.user, 'SendInput',
                             [w.UINT, c.POINTER(Input), c.c_int], w.UINT))
    if guard() != '' or any(keys(key) & 0x8000 for key in (0x10, 0x11, 0x12, 0x7B)):
        raise CaptureUnavailable('Native amount field is not empty or a physical key is held')
    digits = str(price)
    for index, digit in enumerate(digits):
        entered = guard()
        if entered.replace(',', '') != digits[:index]:
            raise CaptureUnavailable('Native amount differs from the exact already-entered price prefix')
        if any(keys(key) & 0x8000 for key in (0x10, 0x11, 0x12, 0x7B)):
            raise CaptureUnavailable('Manual key or Stop interrupted the one-shot price')
        stage('price_digit_'+str(index+1), input_boundary=True)
        try:
            event = Input(type=1, data=InputUnion(ki=KeyboardInput(0, ord(digit), 4, 0, 0)))
            if send(1, c.byref(event), c.sizeof(Input)) != 1:
                raise OSError('Native booth price key input was incomplete')
        finally:
            event = Input(type=1, data=InputUnion(ki=KeyboardInput(0, ord(digit), 6, 0, 0)))
            if send(1, c.byref(event), c.sizeof(Input)) != 1:
                raise OSError('Native booth price key release was incomplete')


def _run(ui, character, profile, before, token):
    journal = ui.runtime.journal
    request = before['request']
    request_id = request['request_id']
    attempted = False
    confirmation_marked = False
    try:
        from conquest.desktop_runtime import physical_coordinates
        from conquest.foreground import foreground_click, foreground_drag
        from conquest.input_probe import MessageTarget
        from conquest.layout_revision import SharedLayoutRevision
        from conquest.merchants.pricing import parse_booth_price, wait_booth_price
        target = MessageTarget(request['expected_identity']['pid'], before['hwnd'])
        coordinator = ui.coordinator
        grant = before.get('farmer_grant') or {}
        seconds = min(35, grant['expires_at']-time.time()-3) if grant.get('scope') == 'listing_1078' else 20
        deadline = time.monotonic()+seconds
        focus_verified = False
        with MemorySession(target.pid, CLIENT_SHA256_1078) as session, physical_coordinates():
            if session.identity != request['expected_identity']:
                raise ValueError('Exact merchant process changed before one-shot listing')
            gui = GuiReader.for_session(session)
            reader = open_read_only_1078(session, profile.name)
            booth_vtable = read_build_layout(session).merchant_booth_vtable_rva
            model = gui.model(25, booth_vtable)

            def policy():
                _policy(ui, character, profile, before['control'], request_id=request_id,
                        deadline=deadline, farmer_target=before['farmer_target'],
                        merchant_intent=before['merchant_intent'],
                        phases=('prepared', 'submitted'),
                        scheduled_refill=before.get('scheduled_foreground_refill', False))
                session.assert_identity()
                state = target.snapshot()
                if state['root_hwnd'] != target.hwnd or (
                        focus_verified and state['foreground'] != target.hwnd):
                    raise CaptureUnavailable('Merchant lost exact foreground ownership')

            def check():
                # The active listing lease calls this capability's policy
                # through coordinator.owner_allowed on every check.
                coordinator.check()

            fence = coordinator.fence
            with (fence.bind_worker(token) if fence else nullcontext()), \
                    coordinator.booth_listing_once_scope(character, policy):
                with coordinator.lease(character, purpose=KIND):
                    check()
                    baseline = reader.read_manual_ownership()
                    _validate_snapshot(baseline, profile, request)
                    if before.get('scheduled_foreground_refill'):
                        from conquest.merchants.listing_capability_1078 import require
                        require(journal, character, baseline)
                    from conquest.focus_recovery import activate_client
                    # The explicit exact-item request also authorizes safe
                    # native focus for its first supervised live proof. This
                    # does not require a prior listing receipt, open a booth,
                    # click guessed game controls or change farmer intent.
                    journal.step(request_id, 'foreground', 'before_action',
                                 {'identity': session.identity, 'hwnd': target.hwnd})
                    if not activate_client(target.hwnd, session.identity):
                        raise CaptureUnavailable('Exact merchant foreground activation was denied')
                    focus_verified = True
                    check()
                    baseline = reader.read_manual_ownership()
                    _validate_snapshot(baseline, profile, request)
                    journal.step(request_id, 'foreground', 'verified',
                                 {'identity': session.identity, 'hwnd': target.hwnd})
                    if not _same_quote(before['price_plan'],
                            _price_plan(ui, character, baseline, _selected(baseline, request))):
                        raise ValueError('Reliable item price or listing priority changed')
                    if any(win['name'] == 'Add Item to Booth' for win in gui.windows()):
                        raise ValueError('Existing price dialog prevents one-shot listing')
                    if any(baseline[key] != before['snapshot'][key]
                           for key in OWNERSHIP_FIELDS if key in before['snapshot']):
                        raise ValueError('Stock changed after the operator request was recorded')
                    layout = SharedLayoutRevision(target, windows=gui.windows,
                                                  gui_size=gui.viewport_size)
                    revision = layout.stable()
                    # Foreground activation may settle panel/table geometry on
                    # a later frame. Bind grids to the stabilized revision,
                    # never to an earlier transient focus frame.
                    grids = _grids(gui, baseline, request['item_uid'])
                    layout.assert_current(revision)

                    def fresh(*, modal=None):
                        check()
                        layout.assert_current(revision)
                        snapshot = reader.read_manual_ownership()
                        _validate_snapshot(snapshot, profile, request)
                        changed = [key for key in OWNERSHIP_FIELDS if snapshot[key] != baseline[key]]
                        if changed:
                            raise ValueError('Exact merchant ownership changed: '+', '.join(changed))
                        current_grids = _grids(gui, snapshot, request['item_uid'])
                        if current_grids != grids:
                            changed = []
                            for key in grids:
                                if current_grids[key] == grids[key]:
                                    continue
                                if isinstance(grids[key], dict) and isinstance(current_grids[key], dict):
                                    changed.extend(key+'.'+field for field in sorted(set(grids[key]) | set(current_grids[key]))
                                                   if grids[key].get(field) != current_grids[key].get(field))
                                else:
                                    changed.append(key)
                            raise ValueError('Exact merchant grid changed: '+', '.join(changed))
                        if gui.model(25, booth_vtable) != model:
                            raise ValueError('Exact merchant booth model pointer changed')
                        if unpack(gui.session, model+0x4c, '<I')[0] != baseline['own_booth_uid']:
                            raise ValueError('Exact merchant displayed booth owner changed')
                        present = any(win['name'] == 'Add Item to Booth' for win in gui.windows())
                        if modal is not None and present != modal:
                            raise ValueError('Native price dialog changed before input')
                        return snapshot

                    def stage(name, payload=None, *, input_boundary=False):
                        nonlocal attempted
                        journal.step(request_id, name, 'before_action', payload)
                        if input_boundary:
                            attempted = True

                    def point(logical):
                        result = tuple(round(value*physical/logical_size)
                                       for value, physical, logical_size
                                       in zip(logical, revision.client_size, revision.gui_size))
                        if any(not 1 < value < maximum-2
                               for value, maximum in zip(result, revision.client_size)):
                            raise ValueError('Listing input candidate is outside native client')
                        return result

                    source, destination = point(grids['source']), point(grids['destination'])
                    fresh(modal=False)
                    journal.step(request_id, 'baseline', 'verified',
                                 {'uid': request['item_uid'], 'price': request['price'],
                                  'owned_booth_uid': baseline['own_booth_uid']})
                    stage('drag_pointer', {'source': source, 'destination': destination})

                    def drag_guard():
                        fresh(modal=False)

                    def drag_press():
                        drag_guard()
                        _hovered_window(gui, grids['inventory_window'])
                        stage('drag_press', input_boundary=True)

                    def drag_release():
                        stage('drag_release')
                        drag_guard()
                        _hovered_window(gui, grids['booth_child'])

                    def drag_layout_guard():
                        # Every press/release above reobserves full ownership,
                        # model, grid and farmer safety. Intermediate pointer
                        # moves retain Stop/grant/identity and layout checks
                        # without repeating the complete inventory traversal.
                        check()
                        layout.assert_current(revision)

                    foreground_drag(target, source, destination, revision.client_size,
                                    before_press=drag_press, before_release=drag_release,
                                    layout_guard=drag_layout_guard)
                    until = time.monotonic()+2
                    while not any(win['name'] == 'Add Item to Booth' for win in gui.windows()):
                        check()
                        if time.monotonic() >= until:
                            raise ValueError('One-shot drag outcome unverified; no repeat')
                        time.sleep(.03)
                    revision = layout.stable()
                    current = fresh(modal=True)
                    window, controls, text = _modal(gui, model, current, request['item_uid'])
                    from conquest.merchants.listing_preflight_1078 import _MODAL_RENDER_SHA256
                    journal.step(request_id, 'native_dialog', 'verified',
                                 {'uid': request['item_uid'],
                                  'renderer_sha256': _MODAL_RENDER_SHA256})
                    if text != '':
                        raise ValueError('New price buffer must be empty')
                    binding = (window, controls)

                    def dialog_guard(label, *, expected_text=None):
                        until = time.monotonic()+1.5
                        while True:
                            now = fresh(modal=True)
                            win, points, value = _modal(gui, model, now, request['item_uid'])
                            if ((win, points) != binding
                                    or expected_text is not None and value != expected_text):
                                raise ValueError('Exact item, price dialog, or amount changed')
                            try:
                                gui.assert_hovered(win, label)
                                return value
                            except HoverNotReady:
                                # Rendering can briefly clear HoveredId. Retry
                                # only this read gap, under the original full
                                # ownership/Stop/deadline guards on every pass.
                                if time.monotonic() >= until:
                                    raise
                                check()
                                time.sleep(.02)

                    stage('amount_pointer')

                    def amount_hover():
                        return dialog_guard('##Amount', expected_text='')

                    def amount_press():
                        dialog_guard('##Amount', expected_text='')
                        stage('amount_press', input_boundary=True)

                    foreground_click(target, *point(controls['##Amount']), revision.client_size,
                                     require_foreground=True, before_press=amount_hover,
                                     before_mouse_down=amount_press,
                                     layout_guard=lambda: layout.assert_current(revision))

                    def typing_guard():
                        return dialog_guard('##Amount')

                    _type_price(target, request['price'],
                                typing_guard, stage)
                    raw_price = lambda: gui.session.read_block(model+0x54, 12).split(b'\0')[0]
                    wait_booth_price(raw_price, request['price'], check)
                    displayed = _modal(gui, model, fresh(modal=True), request['item_uid'])[2]
                    if parse_booth_price(displayed.encode('ascii')) != request['price']:
                        raise ValueError('Native price buffer differs from the verified quote')
                    journal.step(request_id, 'price', 'verified',
                                 {'uid': request['item_uid'], 'price': request['price']})
                    stage('confirm_pointer')

                    def confirm_guard(*, expected_text=None):
                        # Loaded OK hash/geometry are live-proven. A transient
                        # render gap can also occur after the quote reread, so
                        # retain the same bounded reobservation at mouse-down.
                        value = dialog_guard('OK', expected_text=expected_text)
                        if parse_booth_price(value.encode('ascii')) != request['price']:
                            raise ValueError('Native price changed before confirmation')
                        return value

                    def confirm_press():
                        nonlocal confirmation_marked, attempted
                        quote_snapshot = fresh(modal=True)
                        if not _same_quote(before['price_plan'],
                                _price_plan(ui, character, quote_snapshot, _selected(quote_snapshot, request))):
                            raise ValueError('Reliable item price or listing priority changed before submission')
                        confirm_guard()
                        # This FULL-synchronous SQLite step is the irreversible
                        # boundary. A crash after it never permits re-pressing OK.
                        journal.step(request_id, 'confirm_press', 'before_mouse_down',
                                     {'uid': request['item_uid'], 'price': request['price'],
                                      'owned_booth_uid': baseline['own_booth_uid']})
                        confirmation_marked = True
                        attempted = True

                    foreground_click(target, *point(controls['OK']), revision.client_size,
                                     require_foreground=True, before_press=confirm_guard,
                                     before_mouse_down=confirm_press,
                                     layout_guard=lambda: layout.assert_current(revision))
                    journal.transition(request_id, 'submitted',
                                       {'confirmation_attempted': True})
                    until = time.monotonic()+5
                    while time.monotonic() < until:
                        # Read-only settlement after irreversible submission.
                        try:
                            if reconcile(ui, request_id, character)['phase'] == 'verified':
                                return
                        except (OSError, ValueError, CaptureUnavailable):
                            # A brief post-click memory gap is not permission to
                            # send OK again. The same exact process is checked
                            # below before the next read-only observation.
                            pass
                        check()
                        time.sleep(.1)
                    raise CaptureUnavailable('Listing submitted; exact booth ownership needs reconciliation')
    except BaseException as error:
        unchanged = False
        if not attempted:
            try:
                unchanged = _unchanged_before_input(before, profile)
            except (OSError, ValueError, CaptureUnavailable):
                pass
        result = {'input_attempted': attempted,
                  'confirmation_attempted': confirmation_marked,
                  'unchanged_ownership_verified': unchanged,
                  'replay_allowed': False, 'reason': str(error)}
        try:
            journal.transition(request_id, 'aborted' if unchanged else 'uncertain', result)
            if not unchanged:
                journal.set(character, 'attention', {'kind': KIND,
                    'transaction_id': request_id,
                    'note': 'One-shot 1078 listing needs read-only reconciliation: '+str(error)})
        except (ValueError, OSError):
            pass  # Prepared or submitted journal still holds all future input.
    finally:
        with WORKER_LOCK:
            if WORKERS.get(request_id) is threading.current_thread():
                WORKERS.pop(request_id, None)
