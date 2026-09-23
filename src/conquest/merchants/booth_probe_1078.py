"""Explicit one-item, no-submission native 1078 calibration, never scheduled.

This first probe is restricted to the manually observed Dutch BreastPlate and
an empty, already-open owned booth. A receipt is diagnostic evidence only: it
never writes a qualification file or enables MerchantDriver/refill/recovery.
Every request ID is consumed once, including an interrupted or rejected run.
"""
from contextlib import nullcontext
import ctypes as c
from ctypes import wintypes as w
import json
from pathlib import Path
import re
import struct
import threading
import time

from conquest.capture import CaptureUnavailable
from conquest.character_context import registry
from conquest.memory import MemorySession
from conquest.memory_build_layout import CLIENT_SHA256_1078, read_build_layout
from conquest.merchants.memory import GuiReader, HoverNotReady, unpack
from conquest.merchants.reader_1078 import open_read_only_1078


KIND = 'booth_probe_1078_no_submit'
ITEM = {'uid': 295498033, 'type_id': 130624, 'name': 'BreastPlate',
        'plus': 1, 'gem1': 0, 'gem2': 0, 'bound': False, 'quantity': 1, 'price': None}
STOCK_FIELDS = ('identity', 'character', 'character_uid', 'server', 'map_id',
                'position', 'hp', 'silver', 'capacity', 'inventory', 'booth',
                'own_booth_uid', 'booth_open', 'trade', 'request')


def _row(journal, request_id):
    with journal.db() as db:
        value = db.execute('SELECT * FROM transactions WHERE id=?', (request_id,)).fetchone()
    return dict(value) if value else None


def _status(journal, request_id, character):
    row = _row(journal, request_id)
    if not row or row['kind'] != KIND or row['character'] != character:
        raise ValueError('No matching 1078 booth probe receipt')
    steps = journal.trace(request_id)
    return {'request_id': request_id, 'phase': row['phase'],
            'needs_attention': row['phase'] not in ('verified', 'aborted'),
            'last_stage': steps[-1]['stage'] if steps else None,
            'result': json.loads(row['result_json'] or '{}'),
            'listing_submitted': False, 'input_qualified': False,
            'refill_input_ready': False, 'replay_allowed': False}


def _profile(character):
    profiles = registry()
    if profiles is None:
        raise ValueError('Configured merchant profile is required')
    profile = profiles.resolve(getattr(character, 'profile_id', character),
                               role='Merchant', server='America')
    if not profile.local_enabled or profile.name != 'Dutch' or not profile.character_uid:
        raise ValueError('This probe requires the configured, UID-pinned Dutch profile')
    return profile


def _window(windows, name, *, prefix=False):
    matches = [win for win in windows if
               (win['name'].startswith(name) if prefix else win['name'] == name)]
    if len(matches) != 1:
        raise ValueError(f'Exactly one live {name} panel is required')
    return matches[0]


def _item(snapshot):
    matches = [item for item in snapshot['inventory'] if item['uid'] == ITEM['uid']]
    if len(matches) != 1 or any(matches[0].get(key) != value for key, value in ITEM.items()):
        raise ValueError('The exact manually observed BreastPlate fingerprint is required')
    return matches[0]


def _validate_snapshot(snapshot, profile, expected):
    if (snapshot['identity'] != expected['expected_identity']
            or snapshot['character_uid'] != profile.character_uid
            or snapshot['character_uid'] != expected['expected_character_uid']
            or snapshot['character'] != profile.name or snapshot['server'] != profile.server
            or snapshot['own_booth_uid'] != expected['expected_own_booth_uid']
            or not snapshot['own_booth_uid'] or not snapshot['booth_open']
            or snapshot['map_id'] != 1036 or snapshot['hp'] <= 0
            or snapshot['trade'] is not None or snapshot['request'] is not None
            or snapshot['booth'] or snapshot['capacity'] != 40
            or not 0 <= time.time() - snapshot['timestamp'] <= 1):
        raise ValueError('Exact living merchant, empty owned booth, or closed-trade evidence changed')
    _item(snapshot)


def _farmer_safe_market(ui, expected_target=None):
    """A stopped farmer still needs a live, identity-bound safe location."""
    import os

    from conquest.character_context import state_path
    from conquest.discord_notify import process_alive, read_json
    from conquest.worker import request

    if ui.app.thread and ui.app.thread.is_alive():
        raise CaptureUnavailable('Farmer route must exit before merchant focus')
    route = read_json(state_path('reports/overnight/status.json'))
    route_pid = route.get('pid')
    if (route.get('phase') not in ('stopped', 'completed', 'failed')
            or type(route_pid) is not int or process_alive(route_pid) is not False):
        raise CaptureUnavailable('Farmer route worker must exit before merchant focus')
    info = Path(state_path('.runtime')) / f'embedded-worker-{os.getpid()}.json'
    health = request(info, 'health')
    controls = health.get('embedded_controls') or {}
    life = controls.get('life') or {}
    target = health.get('target')
    if (health.get('profile_id') != ui.runtime.manual_target('Farmer')
            or not isinstance(target, dict)
            or expected_target is not None and target != expected_target
            or life.get('map_id') != 1036
            or life.get('dead_candidate') is not False
            or not isinstance(life.get('current_hp'), int)
            or life['current_hp'] <= 0
            or controls.get('manual_mouse')
            or controls.get('manual_input_fence')
            or controls.get('external_execution') is not False
            or not isinstance(controls.get('observed_at'), (int, float))
            or not 0 <= time.time() - controls['observed_at'] <= 1
            or controls.get('control', {}).get('enabled') is not False):
        raise CaptureUnavailable('Farmer must be alive and stopped in Market before merchant focus')
    return target


def _policy(ui, character, profile, control, *, request_id=None, deadline=None,
            farmer_target=None):
    runtime, coordinator = ui.runtime, ui.coordinator
    current_control = ui.app.control.snapshot()
    if (ui.closed or ui.app.closing or runtime.stop_event.is_set() or coordinator.stopped
            or coordinator.manual_active() or runtime.manual_handoff_status() is not None
            or coordinator.manual_session_blocked(character)
            or coordinator.manual_session_blocked('Farmer')
            or not ui.safe_to_yield()
            or any(current_control.get(key) != control.get(key) for key in ('enabled', 'paused', 'revision'))
            or control['enabled'] or control.get('paused')
            or _profile(character) != profile
            or getattr(runtime, 'delivery_window', None) or getattr(runtime, 'refill_window', None)
            or runtime.refilling or runtime.connecting or ui.calibrating
            or deadline is not None and time.monotonic() >= deadline):
        raise CaptureUnavailable('Booth probe stopped, expired, or lacks a safe farmer handoff')
    _farmer_safe_market(ui, farmer_target)
    from conquest.merchants.delivery_reservation import active
    from conquest.merchants.background_probe import probe_busy
    if active(runtime.journal, character) or probe_busy(ui):
        raise CaptureUnavailable('Another delivery or native diagnostic owns the merchant')
    _farmer_journals_clear(runtime)
    # Input is global: an unresolved transaction on the other merchant also
    # excludes this probe, including archived/disabled profiles in this DB.
    with runtime.journal.db() as db:
        pending = [dict(row) for row in db.execute(
            "SELECT id,kind,phase FROM transactions WHERE phase NOT IN ('verified','aborted','operator_overridden')")]
    if request_id is None:
        if pending:
            raise ValueError('Reconcile the existing merchant transaction first')
    elif (len(pending) != 1 or pending[0]['id'] != request_id
          or pending[0]['kind'] != KIND or pending[0]['phase'] != 'prepared'):
        raise ValueError('The one-shot booth probe journal changed')


def _farmer_journals_clear(runtime):
    """Inspect existing holds without recovering or rewriting their journals."""
    from conquest.protected_withdrawal import pending as protected_pending
    from conquest.merchants.delivery_operation import pending as delivery_pending
    from conquest.merchants.delivery_probe import JOURNAL as probe_path, TERMINAL as probe_terminal
    from conquest.merchants.trade_qualification_prep import JOURNAL as prep_path, TERMINAL as prep_terminal
    from conquest.meteor_banking import JOURNAL as meteor_path, TERMINAL as meteor_terminal
    from conquest.merchants.delivery_route import STATE as route_path
    if runtime.farmer_bot_owned() or protected_pending() or delivery_pending():
        raise ValueError('Farmer transaction holds must be reconciled before a booth probe')
    for path, terminal in ((probe_path, probe_terminal), (prep_path, prep_terminal),
                           (meteor_path, meteor_terminal), (route_path, None)):
        if Path(str(path)+'.override-intent.json').exists():
            raise ValueError('Farmer journal override recovery must finish before a booth probe')
        if not path.exists():
            continue
        state = json.loads(path.read_text(encoding='utf-8'))
        if (not isinstance(state, dict) or state and
                (state.get('phase') not in terminal if terminal is not None else state.get('active'))):
            raise ValueError('An unfinished farmer transaction excludes the booth probe')


def _hovered_window(gui, window):
    context = unpack(gui.session, gui.base+gui.context_rva, '<Q')[0]
    hovered = unpack(gui.session, context+0x3ec0, '<Q')[0]
    if (hovered != window['address']
            or unpack(gui.session, gui.base+gui.context_rva, '<Q')[0] != context
            or unpack(gui.session, context+0x3ec0, '<Q')[0] != hovered):
        raise HoverNotReady('Pointer is not over the exact memory-owned grid window')


def _grids(gui, snapshot):
    windows = gui.windows()
    inv = _window(windows, 'Inventory/', prefix=True)
    booth = _window(windows, 'Booth')
    child = _window(windows, 'Booth/', prefix=True)
    inventory = gui.table(inv, '##ItemTable')
    table = gui.table(child, 'BoothTable')
    columns = inventory['columns']
    if (len(columns) != 10 or inventory['row_height'] != 40
            or any(columns[n+1]['content_x'] - columns[n]['content_x'] != 40
                   for n in range(len(columns)-1)) or table['row_height'] != 64):
        raise ValueError('1078 inventory/booth grid schema changed')
    slot = _item(snapshot)['slot']
    if type(slot) is not int or not 0 <= slot < 40:
        raise ValueError('Selected item slot is unavailable')
    source = (columns[slot % 10]['content_x'] + 20,
              inventory['outer'][1] + slot // 10 * 40 + 20)
    left, top, right, bottom = inventory['clip']
    if not left+2 < source[0] < right-2 or not top+2 < source[1] < bottom-2:
        raise ValueError('Selected inventory cell is clipped; this probe never scrolls')
    x, y, width, height = booth['geometry']
    destination = (x + width/2, y + height/2)
    # The manually observed drop relation is the open empty booth body centre.
    cx, cy, cw, ch = child['geometry']
    if not cx+2 < destination[0] < cx+cw-2 or not cy+2 < destination[1] < cy+ch-2:
        raise ValueError('Owned booth body centre is outside its live child panel')
    return {'inventory_window': inv, 'booth_window': booth, 'booth_child': child,
            'inventory_table': inventory, 'booth_table': table,
            'source': source, 'destination': destination}


def _modal(gui, model, snapshot):
    """Exact-1078 candidate layout, then native hover before any press.

    No Confirm/OK point is calculated or exposed. The matching geometry is
    only a cursor destination candidate; the hash check is mandatory.
    """
    window = _window(gui.windows(), 'Add Item to Booth')
    raw = gui.session.read_block(window['address'], 0x250)
    x, y, width, height = struct.unpack_from('<4f', raw, 0x18)
    end_x, button_y = struct.unpack_from('<2f', raw, 0xe8)
    button_height = struct.unpack_from('<f', raw, 0x114)[0]
    start_x, start_y = struct.unpack_from('<2f', raw, 0xf0)
    if (tuple(window['geometry']) != (x, y, width, height)
            or (width, height) != (264., 92.) or button_height != 18
            or end_x != x+256 or button_y != y+66 or (start_x, start_y) != (x+8, y+26)
            or gui.session.read_block(window['address']+0x18, 16) != raw[0x18:0x28]):
        raise ValueError('1078 price dialog does not match the candidate layout')
    from conquest.merchants.listing_preflight_1078 import _modal_candidate
    candidate = _modal_candidate(gui.session, model, snapshot)
    if candidate['candidate_selected_item_uid'] != ITEM['uid']:
        raise ValueError('Price dialog selected another item')
    return window, {'##Amount': (start_x+64, button_y-13),
                    'Cancel': (end_x-60, button_y+9)}, candidate['candidate_price_buffer_text']


def dispatch(ui, body):
    """Only MerchantBridge's authenticated dispatcher calls this entry point."""
    action = body['action']
    if action not in ('merchant-booth-probe-1078', 'merchant-booth-probe-status-1078'):
        raise ValueError('Unknown booth probe operation')
    common = {'action', 'character', 'request_id'}
    fields = common | {'item_uid', 'expected_identity', 'expected_character_uid',
                       'expected_own_booth_uid'}
    if set(body) != (common if action == 'merchant-booth-probe-status-1078' else fields):
        raise ValueError('Booth probe requires an exact request, process and item binding')
    request_id = body['request_id']
    if not isinstance(request_id, str) or not re.fullmatch(r'booth1078-[A-Za-z0-9_-]{8,80}', request_id):
        raise ValueError('Use a unique booth1078-prefixed request ID')
    character = body['character']; profile = _profile(character)
    journal = ui.runtime.journal
    existing = _row(journal, request_id)
    if action == 'merchant-booth-probe-status-1078':
        return _status(journal, request_id, character)
    if (type(body['item_uid']) is not int or body['item_uid'] != ITEM['uid']
            or not isinstance(body['expected_identity'], dict)
            or any(type(body[key]) is not int or body[key] <= 0
                   for key in ('expected_character_uid', 'expected_own_booth_uid'))):
        raise ValueError('This first probe supports only the exact manually observed item')
    # Transport retries cannot rerun input, even after cancellation succeeded.
    if existing:
        prior = json.loads(existing['before_json'])
        if prior.get('request') != dict(body):
            raise ValueError('Probe request ID was used for different work')
        return _status(journal, request_id, character)
    control = ui.app.control.snapshot()
    farmer_target = _farmer_safe_market(ui)
    _policy(ui, character, profile, control, farmer_target=farmer_target)
    from conquest.merchants.observe_1078 import observe
    observed = observe(ui.runtime, character, listing_preflight=True)
    preflight = observed['listing_preflight']
    modal = preflight.get('price_modal') or {}
    if (observed['identity'] != body['expected_identity']
            or observed['character_uid'] != body['expected_character_uid']
            or observed['own_booth_uid'] != body['expected_own_booth_uid']
            or not observed['profile_uid_verified']
            or not observed['closed_modal'] or not preflight['layout_observed']
            or modal.get('observed') is not False):
        raise ValueError('Refresh exact merchant evidence with the price dialog closed')
    candidates = [client for client in ui.runtime.merchant_windows()
                  if client.identity == observed['identity']]
    if len(candidates) != 1:
        raise ValueError('Expected exactly one native HWND for the selected process')
    hwnd = candidates[0].hwnd
    from conquest.input_probe import MessageTarget
    target = MessageTarget(observed['identity']['pid'], hwnd)
    native = target.snapshot()
    if native['root_hwnd'] != hwnd or native['foreground'] != hwnd or native['minimized']:
        raise ValueError('Probe requires Dutch already foreground in a standalone native window')
    _policy(ui, character, profile, control, farmer_target=farmer_target)
    fence = ui.coordinator.fence
    token = fence.capture() if fence else None
    before = {'request': dict(body), 'profile_id': profile.id, 'hwnd': hwnd,
              'control': control, 'client_sha256': CLIENT_SHA256_1078,
              'farmer_target': farmer_target,
              'observed': observed, 'listing_submission_permitted': False}
    if not journal.begin(request_id, character, KIND, before):
        return _status(journal, request_id, character)
    worker = threading.Thread(target=_run, args=(ui, character, profile, before, token),
                              daemon=True, name='booth-1078-no-submit')
    try:
        worker.start()
    except BaseException:
        journal.transition(request_id, 'aborted', {'reason': 'Worker did not start', 'input_attempted': False})
        raise
    return {'request_id': request_id, 'phase': 'prepared', 'replay_allowed': False,
            'listing_submitted': False, 'input_qualified': False, 'refill_input_ready': False}


def _run(ui, character, profile, before, token):
    journal = ui.runtime.journal; request = before['request']; request_id = request['request_id']
    attempted = False
    try:
        from conquest.desktop_runtime import physical_coordinates
        from conquest.foreground import foreground_click, foreground_drag
        from conquest.input_probe import MessageTarget
        from conquest.layout_revision import SharedLayoutRevision
        coordinator = ui.coordinator
        deadline = time.monotonic()+15
        target = MessageTarget(request['expected_identity']['pid'], before['hwnd'])
        with MemorySession(target.pid, CLIENT_SHA256_1078) as session, physical_coordinates():
            if session.identity != request['expected_identity']:
                raise ValueError('Exact process identity changed before probe')
            gui = GuiReader.for_session(session)
            reader = open_read_only_1078(session, profile.name)
            booth_vtable = read_build_layout(session).merchant_booth_vtable_rva
            model = gui.model(25, booth_vtable)
            def policy():
                _policy(ui, character, profile, before['control'], request_id=request_id,
                        deadline=deadline, farmer_target=before['farmer_target'])
                session.assert_identity()
                state = target.snapshot()
                if state['root_hwnd'] != target.hwnd or state['foreground'] != target.hwnd:
                    raise CaptureUnavailable('Standalone merchant lost foreground ownership')
            def check():
                policy(); coordinator.check()
            fence = coordinator.fence
            with (fence.bind_worker(token) if fence else nullcontext()), coordinator.booth_probe_scope(character, policy):
                with coordinator.lease(character, purpose=KIND):
                    check()
                    baseline = reader.read_manual_ownership()
                    _validate_snapshot(baseline, profile, request)
                    if any(win['name'] == 'Add Item to Booth' for win in gui.windows()):
                        raise ValueError('Existing price dialog prevents one-shot drag')
                    grids = _grids(gui, baseline)
                    layout = SharedLayoutRevision(target, windows=gui.windows, gui_size=gui.viewport_size)
                    revision = layout.stable()
                    def fresh(*, modal=None):
                        check(); layout.assert_current(revision)
                        snapshot = reader.read_manual_ownership()
                        _validate_snapshot(snapshot, profile, request)
                        if (any(snapshot[key] != baseline[key] for key in STOCK_FIELDS)
                                or _grids(gui, snapshot) != grids
                                or gui.model(25, booth_vtable) != model
                                or unpack(gui.session, model+0x4c, '<I')[0] != baseline['own_booth_uid']):
                            raise ValueError('Exact stock, owned model, or grid changed during probe')
                        present = any(win['name'] == 'Add Item to Booth' for win in gui.windows())
                        if modal is not None and present != modal:
                            raise ValueError('Price dialog state changed before input')
                        return snapshot
                    def stage(name, payload=None):
                        nonlocal attempted
                        journal.step(request_id, name, 'before_action', payload)
                        attempted = True
                    def point(logical):
                        result = tuple(round(value*physical/logical_size) for value, physical, logical_size
                                       in zip(logical, revision.client_size, revision.gui_size))
                        if any(not 1 < v < maximum-2 for v, maximum in zip(result, revision.client_size)):
                            raise ValueError('Input candidate is outside the native client')
                        return result
                    source, destination = point(grids['source']), point(grids['destination'])
                    fresh(modal=False)
                    journal.step(request_id, 'baseline', 'verified', {'snapshot': baseline, 'grids': grids})
                    stage('drag_pointer', {'source': source, 'destination': destination})
                    def drag_guard():
                        fresh(modal=False)
                    def drag_press():
                        stage('drag_press'); drag_guard()
                        _hovered_window(gui, grids['inventory_window'])
                    def drag_release():
                        stage('drag_release'); drag_guard()
                        _hovered_window(gui, grids['booth_child'])
                    foreground_drag(target, source, destination, revision.client_size,
                                    before_press=drag_press, before_release=drag_release,
                                    layout_guard=drag_guard)
                    # Only observe while waiting for the first gesture's effect.
                    wait_until = time.monotonic()+2
                    while not any(win['name'] == 'Add Item to Booth' for win in gui.windows()):
                        check()
                        if time.monotonic() >= wait_until:
                            raise ValueError('Drag result not verified; no repeat is permitted')
                        time.sleep(.03)
                    revision = layout.stable()
                    snapshot = fresh(modal=True)
                    window, controls, text = _modal(gui, model, snapshot)
                    if text != '':
                        raise ValueError('New exact-item price buffer must be empty')
                    binding = (window, controls)
                    def dialog_guard(label, *, require_text=None):
                        current = fresh(modal=True)
                        win, points, value = _modal(gui, model, current)
                        if (win, points) != binding or require_text is not None and value != require_text:
                            raise ValueError('Exact price dialog binding or sentinel changed')
                        gui.assert_hovered(win, label)
                        return value
                    def click(label, require_text):
                        stage(label+'_pointer')
                        def hovered():
                            until = time.monotonic()+.5
                            while True:
                                try:
                                    dialog_guard(label, require_text=require_text)
                                    return
                                except HoverNotReady:
                                    if time.monotonic() >= until:
                                        raise
                                    check(); time.sleep(.02)
                        def press():
                            stage(label+'_press')
                            dialog_guard(label, require_text=require_text)
                        foreground_click(target, *point(controls[label]), revision.client_size,
                                         require_foreground=True, before_press=hovered,
                                         before_mouse_down=press,
                                         layout_guard=lambda: layout.assert_current(revision))
                    click('##Amount', '')
                    _type_sentinel(target, lambda: dialog_guard('##Amount'), stage)
                    until = time.monotonic()+2
                    while dialog_guard('##Amount') != '123,456':
                        if time.monotonic() >= until:
                            raise ValueError('Sentinel amount not verified; leaving the exact dialog held')
                        time.sleep(.03)
                    journal.step(request_id, 'sentinel', 'verified', {'buffer': '123,456', 'uid': ITEM['uid']})
                    click('Cancel', '123,456')
                    until = time.monotonic()+2
                    while any(win['name'] == 'Add Item to Booth' for win in gui.windows()):
                        check()
                        if time.monotonic() >= until:
                            raise ValueError('Cancellation not verified; no repeat is permitted')
                        time.sleep(.03)
                    revision = layout.stable()
                    after = fresh(modal=False)
                    if unpack(gui.session, model+0x50, '<I')[0] != 0:
                        raise ValueError('Canceled dialog retained a selected UID')
                    # A second complete memory observation brackets settlement.
                    settled = fresh(modal=False)
                    if any(settled[key] != after[key] for key in STOCK_FIELDS):
                        raise ValueError('Ownership changed during cancellation settlement')
                    journal.transition(request_id, 'verified', {
                        'uid': ITEM['uid'], 'sentinel_verified': '123,456', 'cancel_verified': True,
                        'stock_unchanged': True, 'after': after, 'listing_submitted': False,
                        'input_qualified': False, 'refill_input_ready': False})
    except BaseException as error:
        # No recovery gesture is safe after an uncertain drag, click or key.
        # A prepared crash receipt remains pending and cannot be replayed.
        result = {'input_attempted': attempted, 'needs_attention': attempted,
                  'reason': str(error), 'listing_submitted': False, 'replay_allowed': False}
        try:
            journal.transition(request_id, 'uncertain' if attempted else 'aborted', result)
            if attempted:
                journal.set(character, 'attention', {'kind': KIND, 'transaction_id': request_id,
                    'note': '1078 no-submit booth probe needs manual reconciliation: '+str(error)})
        except (ValueError, OSError):
            pass  # The durable prepared receipt still holds all future probes.


def _type_sentinel(target, guard, stage):
    """Six Unicode digits only: no Enter, shortcut, paste or process writes."""
    from conquest.foreground import Input, InputUnion, KeyboardInput
    from conquest.mouse_priority import guarded_send
    from conquest.win32 import bind
    keys = bind(target.backend.user, 'GetAsyncKeyState', [c.c_int], c.c_short)
    send = guarded_send(bind(target.backend.user, 'SendInput', [w.UINT, c.POINTER(Input), c.c_int], w.UINT))
    if any(keys(key)&0x8000 for key in (0x10, 0x11, 0x12, 0x7B)):
        raise CaptureUnavailable('A physical modifier or Stop key is held')
    if guard() != '':
        raise ValueError('Sentinel entry requires a freshly verified empty amount')
    for index, digit in enumerate('123456'):
        stage('sentinel_digit_'+str(index+1))
        guard()
        if any(keys(key)&0x8000 for key in (0x10, 0x11, 0x12, 0x7B)):
            raise CaptureUnavailable('A physical modifier or Stop key interrupted sentinel entry')
        try:
            event = Input(type=1, data=InputUnion(ki=KeyboardInput(0, ord(digit), 4, 0, 0)))
            if send(1, c.byref(event), c.sizeof(Input)) != 1:
                raise OSError('Sentinel key input was incomplete')
        finally:
            event = Input(type=1, data=InputUnion(ki=KeyboardInput(0, ord(digit), 6, 0, 0)))
            if send(1, c.byref(event), c.sizeof(Input)) != 1:
                raise OSError('Sentinel key release was incomplete')
