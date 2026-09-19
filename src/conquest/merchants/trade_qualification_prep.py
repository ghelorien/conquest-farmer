"""One-shot, app-owned positioning for a supervised delivery qualification.

This deliberately stops before opening a trade.  It protects all carried
valuables except one operator-selected ordinary +1, takes the already-qualified
town-to-Market route, and approaches the selected merchant using fresh memory.
"""
from pathlib import Path
import ctypes
import hashlib
import json
import os
import threading
import time
import uuid

from conquest.character_context import installation_path, state_path
from conquest.discord_notify import read_json, write_json
from conquest.merchants.bridge import request
from conquest.merchants.delivery import eligible, exact_items
from conquest.merchants.delivery_bridge import pair
from conquest.merchants.journal import character_name
from conquest.town_trade import stash_candidate


JOURNAL = Path(state_path('reports/merchants/trade-qualification-prep.json'))
TERMINAL = {'completed', 'operator_overridden'}
PENDING = {'prepared', 'warehouse_opening', 'deposit_pending', 'banked',
           'withdraw_pending', 'outbound_pending', 'market', 'approaching'}
_START_LOCK = threading.RLock()


def _durable(state):
    write_json(JOURNAL, state)
    with JOURNAL.open('r+b') as stream:
        stream.flush()
        os.fsync(stream.fileno())


def _read():
    try:
        value = json.loads(JOURNAL.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as error:
        raise ValueError('Trade qualification prep evidence is unreadable; reconcile before input') from error
    if not isinstance(value, dict):
        raise ValueError('Trade qualification prep evidence is unreadable; reconcile before input')
    return value


def pending():
    try:
        state = _read()
    except ValueError:
        return True
    return bool(state and state.get('phase') not in TERMINAL)


def _save(state, phase=None, **fields):
    state.update(fields)
    if phase is not None:
        state['phase'] = phase
    state['updated_at'] = time.time()
    _durable(state)


def _archive(state):
    if state is None:
        return
    raw = json.dumps(state, sort_keys=True, separators=(',', ':')).encode('utf-8')
    digest = hashlib.sha256(raw).hexdigest()
    path = JOURNAL.parent/'trade-qualification-prep-audit'/f'{digest}.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open('xb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        if path.read_bytes() != raw:
            raise ValueError('Trade qualification prep archive differs from its receipt')


def _selected(source, uid):
    if type(uid) is not int or uid <= 0:
        raise ValueError('Select one exact carried +1 equipment UID')
    matches = [item for item in source['inventory'] if item.get('uid') == uid]
    if len(matches) != 1:
        raise ValueError('The selected qualification item is no longer carried')
    item = matches[0]
    kind = item.get('type_id')
    if (not eligible(item) or type(kind) is not int or not 100000 <= kind < 600000
            or kind % 10 == 9 or type(item.get('plus')) is not int or item['plus'] != 1
            or item.get('gem1') != 0 or item.get('gem2') != 0
            or item.get('quantity') != 1):
        raise ValueError('Qualification requires one unbound, unsocketed, non-Super +1 equipment item')
    return item


def _same_participant(current, saved, role):
    if (current.get('character') != saved.get('character')
            or current.get('character_uid') != saved.get('character_uid')
            or current.get('server') != saved.get('server')
            or current.get('identity') != saved.get('identity')):
        raise ValueError(f'{role} process or character identity changed; prep cannot resume')


def _bank_match(item, stored):
    """Compare fields retained by the warehouse reader, including quantity."""
    amount = item.get('amount', item.get('quantity'))
    return (stored.get('uid') == item.get('uid')
            and stored.get('type_id') == item.get('type_id')
            and stored.get('plus') == item.get('plus')
            and (amount is None or stored.get('amount', stored.get('quantity')) == amount))


def _validate_request(ui, character, uid, *, send=request):
    character = character_name(character)
    from conquest.merchants.farmer_preferences import permits_new_delivery
    from conquest.merchants.farmer_identity import ui_character
    permits_new_delivery(ui_character(ui))
    if ui.coordinator.stopped:
        raise ValueError('Global Stop is active')
    if ui.coordinator.manual_session_blocked('Farmer'):
        raise ValueError('A manual visitor session holds farmer input')
    control = ui.app.control.snapshot()
    if control.get('enabled') or control.get('paused'):
        raise ValueError('Trade qualification prep requires Farming Off and no pause hold')
    if (getattr(ui.app, 'thread', None) and ui.app.thread.is_alive()
            or getattr(ui, 'delivery_probe_thread', None) and ui.delivery_probe_thread.is_alive()
            or any(worker.is_alive() for worker in getattr(ui, 'delivery_workers', {}).values())):
        raise ValueError('Wait for current farmer or delivery work to finish')
    if getattr(ui, 'calibrating', None):
        raise ValueError('Wait for calibration to finish')
    from conquest.protected_withdrawal import pending as withdrawal_pending
    from conquest.merchants.delivery_operation import pending as operation_pending
    from conquest.merchants.delivery_route import pending as route_pending
    from conquest.merchants.delivery_journey import pending as journey_pending
    from conquest.meteor_banking import pending as meteor_pending
    from conquest.storage_overflow import pending as overflow_pending
    if (withdrawal_pending() or operation_pending() or route_pending()
            or journey_pending() or meteor_pending() or overflow_pending()
            or any(ui.runtime.journal.pending(name) for name in ui.runtime.controllers)):
        raise ValueError('Reconcile existing automated transactions before supervised prep')
    from conquest.merchants.delivery_probe import previous_probe
    previous_probe()
    if not ui.safe_to_yield():
        raise ValueError('Farmer input is not safely released')
    source = send({'action': 'delivery-source'})['farmer']
    receiver = ui.runtime.observers.get(character)
    if receiver is None:
        raise ValueError('Selected merchant is not attached')
    with receiver.lock:
        merchant = ui.runtime.controllers[character].driver.read()
    selected = _selected(source, uid)
    if source.get('map_id') not in (1002, 1011, 1036):
        raise ValueError('Prep must start in a supported town or Market')
    if merchant.get('map_id') != 1036 or merchant.get('trade') or merchant.get('request'):
        raise ValueError('Selected merchant must be idle in Market')
    return character, control, source, merchant, selected


class PrepLoop:
    """Reuse OvernightLoop's checked movement without its farming lifecycle."""

    def __init__(self, ui, state):
        from conquest.overnight import OvernightLoop
        from conquest.routes import RouteLibrary
        from conquest.navigation import read_terrain
        route_id = state['route_id']
        self._base = OvernightLoop
        self.ui, self.prep_state = ui, state
        self.route = RouteLibrary().load(route_id)
        self.terrain = read_terrain(installation_path(r'C:\Program Files\Classic Conquer 2.0'),
                                    state['origin'])
        self.deadline = None
        self.info = self.care = self.stepper = None
        self.identity = None
        self.phase = 'restocking'
        self.state = {'started_at': state['started_at']}
        self.output = JOURNAL.parent
        self.town_visit = None
        self.market_service_deadline = None

    refresh = lambda self: self._base.refresh(self)
    health = lambda self: self._base.health(self)
    focus = lambda self, health: self._base.focus(self, health)
    living = lambda self: self._base.living(self)
    town = lambda self, action, **fields: self._base.town(self, action, **fields)
    travel = lambda self, destination, **fields: self._base.travel(self, destination, **fields)
    _travel = lambda self, destination, **fields: self._base._travel(self, destination, **fields)

    def check_stop(self):
        control = self.ui.app.control.snapshot()
        if (self.ui.closed or self.ui.app.closing or self.ui.coordinator.stopped
                or control.get('enabled') or control.get('paused')
                or control.get('revision') != self.prep_state['control_revision']
                or self.ui.app.mouse_priority.active()
                or ctypes.windll.user32.GetAsyncKeyState(0x7a) & 0x8000
                or ctypes.windll.user32.GetAsyncKeyState(0x7b) & 0x8000):
            from conquest.overnight import OvernightStopped
            raise OvernightStopped('Trade qualification prep stopped by control or input ownership change')
        self.ui.coordinator.check()

    def record(self, event, **fields):
        # Keep operational breadcrumbs inside this dedicated receipt.  Do not
        # impersonate or overwrite the ordinary overnight route status.
        row = {'time': time.time(), 'event': event, **fields}
        events = self.prep_state.setdefault('events', [])
        events.append(row)
        del events[:-100]
        _save(self.prep_state)


def _reserve_window(ui, key):
    with ui.coordinator.lock:
        ui.coordinator.check()
        if (ui.coordinator.owner or getattr(ui, 'grant', None)
                or getattr(ui.runtime, 'delivery_window', None)
                or getattr(ui.runtime, 'refill_window', None)
                or getattr(ui.runtime, 'handoff', None)
                or getattr(ui.runtime, 'refilling', None)):
            raise ValueError('Another merchant or farmer work window is active')
        ui.runtime.delivery_window = key
        ui.runtime.handoff = key


def _release_window(ui, key):
    deadline = time.monotonic()+5
    while True:
        with ui.coordinator.lock:
            if ui.coordinator.owner is None:
                if getattr(ui.runtime, 'delivery_window', None) == key:
                    ui.runtime.delivery_window = None
                if getattr(ui.runtime, 'handoff', None) == key:
                    ui.runtime.handoff = None
                return
        if time.monotonic() >= deadline:
            raise ValueError('Farmer input did not release; prep work window remains active')
        time.sleep(.05)


def _reconcile_deposit(loop, state):
    pending_item = state.get('pending_item')
    if not pending_item:
        return False
    bag = loop.town('supplies')['items']
    stored = loop.town('warehouse-items')['items']
    carried = [item for item in bag if item.get('uid') == pending_item['uid']]
    banked = [item for item in stored if item.get('uid') == pending_item['uid']]
    if not carried and len(banked) == 1 and _bank_match(pending_item, banked[0]):
        state.setdefault('banked_uids', []).append(pending_item['uid'])
        _save(state, 'warehouse_opening', pending_item=None)
        return True
    if len(carried) == 1 and not banked and _bank_match(pending_item, carried[0]):
        return False
    raise ValueError('Pending warehouse deposit has ambiguous ownership; no input repeated')


def _bank_nonselected(loop, state, *, send=request):
    from conquest.banking import open_warehouse, close_warehouse, transfer
    if state['phase'] == 'prepared':
        _save(state, 'warehouse_opening')
    open_warehouse(loop)
    if state.get('phase') == 'deposit_pending':
        _reconcile_deposit(loop, state)
    while True:
        bag = loop.town('supplies')['items']
        selected = [item for item in bag if item.get('uid') == state['selected_uid']]
        if len(selected) != 1:
            raise ValueError('Selected +1 is no longer carried; prep stopped')
        candidates = [item for item in bag
                      if item.get('uid') != state['selected_uid'] and stash_candidate(item)]
        if not candidates:
            break
        item = candidates[0]
        _save(state, 'deposit_pending', pending_item=item)
        try:
            receipt = loop.town('warehouse-deposit', uid=item['uid'])
        except (OSError, ValueError):
            # The durable pending boundary remains.  A same-request retry
            # decides from fresh bag/bank ownership before any repeat.
            raise
        if receipt.get('verified_in_warehouse') is not True:
            raise ValueError('Warehouse deposit receipt is unverified')
        _reconcile_deposit(loop, state)
    money = loop.town('warehouse-money')
    fare = state['route']['outbound']['fare']
    if money['silver'] < fare:
        amount = fare-money['silver']
        if amount > money['stored_silver']:
            raise ValueError('Warehouse lacks the verified Market fare')
        before = {'silver': money['silver'], 'stored_silver': money['stored_silver']}
        _save(state, 'withdraw_pending', withdraw={'amount': amount, 'before': before})
        transfer(loop, 'withdraw', amount)
        fresh = loop.town('warehouse-money')
        if (fresh['silver'] != before['silver']+amount
                or fresh['stored_silver'] != before['stored_silver']-amount):
            raise ValueError('Market-fare withdrawal needs reconciliation')
        _save(state, 'warehouse_opening', withdraw=None)
    close_warehouse(loop)
    fresh = send({'action': 'delivery-source'})['farmer']
    _same_participant(fresh, state['farmer'], 'Farmer')
    item = _selected(fresh, state['selected_uid'])
    if exact_items([item]) != exact_items([state['selected_item']]):
        raise ValueError('Selected +1 changed during banking')
    if any(item.get('type_id') == 1088001 for item in fresh['inventory']):
        raise ValueError('Loose Meteors remain carried; Market departure blocked')
    extras = [item for item in fresh['inventory']
              if item['uid'] != state['selected_uid'] and eligible(item)]
    if extras:
        raise ValueError('More than one delivery-eligible item remains carried')
    _save(state, 'banked')


def _reconcile_withdraw(loop, state):
    intent = state.get('withdraw') or {}
    amount, before = intent.get('amount'), intent.get('before') or {}
    current = loop.town('warehouse-money')
    if (type(amount) is not int or amount <= 0
            or set(before) != {'silver', 'stored_silver'}):
        raise ValueError('Fare withdrawal receipt is incomplete')
    if (current['silver'] == before['silver']+amount
            and current['stored_silver'] == before['stored_silver']-amount):
        _save(state, 'warehouse_opening', withdraw=None)
        return
    if (current['silver'] == before['silver']
            and current['stored_silver'] == before['stored_silver']):
        from conquest.banking import transfer
        transfer(loop, 'withdraw', amount)
        _save(state, 'warehouse_opening', withdraw=None)
        return
    raise ValueError('Fare withdrawal ownership changed; no transfer repeated')


def _run(ui, state, *, send=request):
    key = state['work_window']
    _reserve_window(ui, key)
    try:
        loop = PrepLoop(ui, state)
        loop.refresh()
        source, merchant = pair(ui, state['character'], farmer_preflight=True)
        _same_participant(source, state['farmer'], 'Farmer')
        _same_participant(merchant, state['merchant'], 'Merchant')
        world = source['map_id']
        phase = state['phase']
        if phase == 'withdraw_pending':
            if world != state['origin']:
                raise ValueError('Fare withdrawal location changed; no transfer repeated')
            from conquest.banking import open_warehouse
            open_warehouse(loop)
            _reconcile_withdraw(loop, state)
            phase = state['phase']
        if phase in ('prepared', 'warehouse_opening', 'deposit_pending'):
            if world != state['origin']:
                raise ValueError('Banking phase left its original town; no input repeated')
            _bank_nonselected(loop, state, send=send)
            phase = state['phase']
        if phase == 'banked':
            if state['origin'] == 1036:
                _save(state, 'market', leg_before=None)
                phase = 'market'
            else:
                from conquest.meteor_banking import trip
                def before_submit():
                    bag = loop.town('supplies')
                    _save(state, 'outbound_pending',
                          leg_before={'silver': bag['silver'], 'items': bag['items']})
                trip(loop, state['route']['outbound'], before_submit=before_submit)
                world = 1036
                phase = state['phase']
        if phase == 'outbound_pending':
            world = loop.living()['embedded_controls']['life']['map_id']
            if world != 1036:
                raise ValueError('Market fare result is uncertain; no fare repeated')
            from conquest.merchants.delivery_journey import verify_arrival
            verify_arrival(loop, state, 'outbound')
            _save(state, 'market', leg_before=None)
            phase = 'market'
        if phase in ('market', 'approaching'):
            from conquest.navigation import read_terrain
            if loop.terrain.map_id != 1036:
                loop.terrain = read_terrain(
                    installation_path(r'C:\Program Files\Classic Conquer 2.0'), 1036)
            source, merchant = pair(ui, state['character'])
            _same_participant(source, state['farmer'], 'Farmer')
            _same_participant(merchant, state['merchant'], 'Merchant')
            item = _selected(source, state['selected_uid'])
            if exact_items([item]) != exact_items([state['selected_item']]):
                raise ValueError('Selected +1 changed before merchant approach')
            if any(i.get('type_id') == 1088001 for i in source['inventory']):
                raise ValueError('Loose Meteors appeared in Market; no merchant approach')
            target = send({'action': 'delivery-target', 'character': state['character']})
            plan = {'merchant': state['character'], 'position': target['merchant_position']}
            _save(state, 'approaching', merchant_position=target['merchant_position'])
            from conquest.merchants.delivery_route import approach_merchant
            if not approach_merchant(loop, plan, send, deadline=time.time()+30):
                raise ValueError('Could not reach a memory-actionable position near the merchant')
            source, merchant = pair(ui, state['character'])
            _same_participant(source, state['farmer'], 'Farmer')
            _same_participant(merchant, state['merchant'], 'Merchant')
            item = _selected(source, state['selected_uid'])
            if exact_items([item]) != exact_items([state['selected_item']]):
                raise ValueError('Selected +1 changed after merchant approach')
            if any(i.get('type_id') == 1088001 for i in source['inventory']):
                raise ValueError('Loose Meteors appeared before qualification')
            target = send({'action': 'delivery-target', 'character': state['character']})
            if not target.get('ready'):
                raise ValueError('Merchant is not memory-actionable after approach')
            _save(state, 'completed', completed_at=time.time(),
                  farmer_position=source['position'], merchant_position=merchant['position'])
    finally:
        _release_window(ui, key)


def start(ui, character, *, selected_uid, send=request):
    """Start or resume the exact journaled prep request in the app process."""
    with _START_LOCK:
        running = getattr(ui, 'trade_qualification_prep_thread', None)
        if running and running.is_alive():
            raise ValueError('Trade qualification prep is already running')
        character, control, source, merchant, selected = _validate_request(
            ui, character, selected_uid, send=send)
        old = _read()
        if old and old.get('phase') not in TERMINAL:
            if old.get('character') != character or old.get('selected_uid') != selected_uid:
                raise ValueError('Resume or reconcile the existing trade qualification prep first')
            _same_participant(source, old['farmer'], 'Farmer')
            _same_participant(merchant, old['merchant'], 'Merchant')
            state = old
            state['control_revision'] = control['revision']
            state.pop('error', None)
            state.pop('error_type', None)
            _durable(state)
        else:
            from conquest.meteor_banking import POLICY
            route = read_json(POLICY).get('origins', {}).get(str(source['map_id']))
            if source['map_id'] == 1036:
                route = {'outbound': {'verified': True, 'source_map': 1036,
                                      'destination_map': 1036, 'fare': 0}}
            if (not route or not route.get('outbound', {}).get('verified')
                    or route['outbound'].get('source_map') != source['map_id']
                    or route['outbound'].get('destination_map') != 1036):
                raise ValueError('No verified memory-qualified route from this town to Market')
            _archive(old)
            route_id = getattr(getattr(ui.app, 'selected_route', None), 'id', None)
            if not route_id:
                raise ValueError('Choose the farmer route before supervised prep')
            state = {'phase': 'prepared', 'request_id': 'trade-prep:'+uuid.uuid4().hex,
                     'work_window': 'trade-prep:'+uuid.uuid4().hex,
                     'character': character, 'selected_uid': selected_uid,
                     'selected_item': selected, 'farmer': source, 'merchant': merchant,
                     'origin': source['map_id'], 'route': route, 'route_id': route_id,
                     'control_revision': control['revision'], 'started_at': time.time(),
                     'banked_uids': [], 'events': []}
            _durable(state)
        def work():
            try:
                _run(ui, state, send=send)
            except Exception as error:
                _save(state, error=str(error), error_type=type(error).__name__)
        thread = threading.Thread(target=work, daemon=True, name='trade-qualification-prep')
        ui.trade_qualification_prep_thread = thread
        thread.start()
        return {'started': True, 'request_id': state['request_id'], 'phase': state['phase'],
                'character': character, 'selected_uid': selected_uid}


def status(ui):
    state = _read()
    running = getattr(ui, 'trade_qualification_prep_thread', None)
    return {'running': bool(running and running.is_alive()), 'state': state}
