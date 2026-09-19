"""One-shot, app-owned positioning for a supervised delivery qualification.

This deliberately stops before opening a trade.  It protects all carried
valuables except one operator-selected ordinary +1, takes the already-qualified
town-to-Market route, and approaches the selected merchant using fresh memory.
"""
from pathlib import Path
import ctypes
import hashlib
import json
import math
import os
import tempfile
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
           'withdraw_pending', 'withdraw_submitted', 'outbound_pending', 'market', 'approaching'}
_START_LOCK = threading.RLock()
CANDIDATE = Path(state_path('reports/merchants/trade-layout-candidate.json'))
CANDIDATE_MAX_AGE = 30*24*60*60


def _write_durable(path, state):
    path = Path(path)
    write_json(path, state)
    with path.open('r+b') as stream:
        stream.flush()
        os.fsync(stream.fileno())


def _durable(state):
    _write_durable(JOURNAL, state)


def _read():
    intent = Path(str(JOURNAL)+'.override-intent.json')
    if intent.exists():
        from conquest.recovery_override import read_recovered
        read_recovered(JOURNAL)
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
    def verify_and_sync():
        with path.open('r+b') as stream:
            if stream.read() != raw:
                raise ValueError('Trade qualification prep archive differs from its receipt')
            stream.flush();os.fsync(stream.fileno())
    if path.exists():
        verify_and_sync()
        return
    fd, name = tempfile.mkstemp(prefix=digest+'.', suffix='.tmp', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'wb') as stream:
            if stream.write(raw) != len(raw):
                raise OSError('Incomplete trade qualification prep archive write')
            stream.flush();os.fsync(stream.fileno())
        try:os.link(temporary, path)
        except FileExistsError:pass
        verify_and_sync()
    finally:
        temporary.unlink(missing_ok=True)


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


def _payload_is_safe(source, state):
    """Require the saved +1 to be the only protected/deliverable payload."""
    _same_participant(source, state['farmer'], 'Farmer')
    selected = _selected(source, state['selected_uid'])
    if exact_items([selected]) != exact_items([state['selected_item']]):
        raise ValueError('Selected +1 changed during supervised prep')
    extras = [item for item in source['inventory']
              if item['uid'] != state['selected_uid']
              and (stash_candidate(item) or eligible(item))]
    if any(item.get('type_id') == 1088001 for item in source['inventory']):
        raise ValueError('Loose Meteors remain carried; merchant route blocked')
    if extras:
        raise ValueError('More than one protected or delivery-eligible item remains carried')
    return selected


def _candidate_profile(observer):
    """Load the probe's build-pinned read-only projection evidence strictly."""
    try:
        raw = CANDIDATE.read_bytes()
        profile = json.loads(raw.decode('utf-8'))
    except (OSError, UnicodeError, ValueError) as error:
        raise ValueError('Trade layout candidate is unreadable') from error
    expected = {'at', 'client_sha256', 'target_mode', 'recipient', 'input_qualified',
                'evidence_reports', 'remaining'}
    if not isinstance(profile, dict) or set(profile) != expected:
        raise ValueError('Trade layout candidate schema changed')
    target = profile.get('target_mode')
    recipient = profile.get('recipient')
    from conquest.merchants.trade_controls import TRADE_MODE_RVA, TRADE_MODE_VALUE
    if (type(profile.get('at')) not in (int, float) or not math.isfinite(profile['at'])
            or profile['at'] <= 0 or profile['at'] > time.time()+300
            or type(profile.get('input_qualified')) is not bool
            or not isinstance(target, dict) or set(target) != {'rva', 'value'}
            or any(type(target.get(key)) is not int for key in ('rva', 'value'))
            or target != {'rva': TRADE_MODE_RVA, 'value': TRADE_MODE_VALUE}
            or not isinstance(recipient, dict)
            or set(recipient) != {'vtable_rva', 'uid_offset', 'name_offset',
                                  'position_offset', 'draw_offset', 'draw_format',
                                  'name_format', 'name_capacity'}
            or any(type(recipient.get(key)) is not int for key in
                   ('vtable_rva', 'uid_offset', 'name_offset', 'position_offset',
                    'draw_offset', 'name_capacity'))
            or recipient.get('draw_format') != 'i32'
            or recipient.get('name_format') != 'inline_utf8'
            or not isinstance(profile.get('evidence_reports'), list)
            or not all(isinstance(value, str) and value for value in profile['evidence_reports'])
            or not isinstance(profile.get('remaining'), list)
            or not all(isinstance(value, str) for value in profile['remaining'])):
        raise ValueError('Trade layout candidate schema changed')
    if profile.get('client_sha256') != observer.adapter.expected_sha256:
        raise ValueError('Trade layout build changed')
    if time.time()-profile['at'] > CANDIDATE_MAX_AGE:
        raise ValueError('Trade layout candidate is stale')
    return raw, profile


def _projection_observation(observer, profile, farmer, merchant):
    """Observe actionability, life and anchor without acquiring input ownership."""
    from conquest.merchants.farmer_trade import RecipientAbsent, recipient_actionability
    try:
        actionability = recipient_actionability(observer, profile, merchant, farmer=farmer)
        occupied = actionability['recipient'].get('occupied_tiles', [])
    except RecipientAbsent as error:
        actionability = None
        occupied = error.occupied_tiles
    from conquest.memory_life import read_life
    from conquest.scene_input import memory_player_anchor
    life = read_life(observer.adapter, observer.health_layout, observer.character)
    if list(life.position) != farmer['position'] or life.map_id != 1036:
        raise ValueError('Farmer moved during prep target projection')
    anchor = list(memory_player_anchor(observer, life))
    return actionability, list(map(list, occupied)), anchor


def _fresh_market_pair(farmer, merchant):
    now = time.time()
    for snapshot in (farmer, merchant):
        observed = snapshot.get('timestamp')
        if type(observed) not in (int, float) or not 0 <= now-observed <= 5:
            raise ValueError('Fresh Market participant memory is required')
        if snapshot.get('map_id') != 1036:
            raise ValueError('Both prep participants must remain in Market')


def target_projection(ui, character):
    """Read-only target projection solely for an already-journaled Market prep.

    This deliberately does not construct FarmerTradeDriver: that driver's
    final farmer_delivery capability is the evidence this staged exercise is
    intended to qualify.
    """
    character = character_name(character)
    state = _read()
    if (not state or state.get('phase') not in ('market', 'approaching')
            or state.get('character') != character):
        raise ValueError('No matching Market trade qualification prep is active')
    incident = hashlib.sha256(json.dumps(state, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    observer = ui.app.observer
    candidate_raw, profile = _candidate_profile(observer)
    size = list(observer.operations.target.snapshot()['client_size'])
    from conquest.merchants.memory import GuiReader
    gui = list(GuiReader(observer.adapter).viewport_size())
    if len(size) != 2 or len(gui) != 2 or any(type(value) is not int or value <= 0 for value in size+gui):
        raise ValueError('Trade projection dimensions are unavailable')
    profile = {**profile, 'client_size': size, 'gui_size': gui}

    farmer, merchant = pair(ui, character)
    _same_participant(farmer, state['farmer'], 'Farmer')
    _same_participant(merchant, state['merchant'], 'Merchant')
    _fresh_market_pair(farmer, merchant)
    if farmer.get('trade') or farmer.get('request') or merchant.get('trade') or merchant.get('request'):
        raise ValueError('Prep target projection requires idle trade participants')
    _payload_is_safe(farmer, state)
    first, first_occupied, first_anchor = _projection_observation(
        observer, profile, farmer, merchant)

    fresh_farmer, fresh_merchant = pair(ui, character)
    _same_participant(fresh_farmer, state['farmer'], 'Farmer')
    _same_participant(fresh_merchant, state['merchant'], 'Merchant')
    _fresh_market_pair(fresh_farmer, fresh_merchant)
    if (fresh_farmer.get('position') != farmer.get('position')
            or fresh_merchant.get('position') != merchant.get('position')):
        raise ValueError('Prep participants moved during target projection')
    if fresh_farmer.get('trade') or fresh_farmer.get('request') or fresh_merchant.get('trade') or fresh_merchant.get('request'):
        raise ValueError('Prep trade state changed during target projection')
    _payload_is_safe(fresh_farmer, state)
    second, second_occupied, second_anchor = _projection_observation(
        observer, profile, fresh_farmer, fresh_merchant)
    if first != second or first_occupied != second_occupied or first_anchor != second_anchor:
        raise ValueError('Trade recipient actionability changed during target projection')

    current = _read()
    current_incident = hashlib.sha256(json.dumps(current, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    try:
        candidate_still = CANDIDATE.read_bytes()
    except OSError as error:
        raise ValueError('Trade layout candidate changed during projection') from error
    if current_incident != incident or candidate_still != candidate_raw:
        raise ValueError('Prep or trade layout candidate changed during projection')

    occupied = [farmer['position'], *first_occupied]
    occupied = list(map(list, dict.fromkeys(map(tuple, occupied))))
    if first is None:
        return {'schema_version': 1, 'ready': False, 'actionable': False,
                'reason': 'recipient_absent', 'character': farmer['character'],
                'farmer_position': farmer['position'], 'merchant': merchant['character'],
                'merchant_position': merchant['position'], 'point': None,
                'viewport': gui, 'client_size': size, 'anchor': first_anchor,
                'occupied_tiles': occupied}
    return {'schema_version': 1, 'ready': first['actionable'], 'reason': first['reason'],
            'character': farmer['character'], 'farmer_position': farmer['position'],
            'merchant': merchant['character'], 'merchant_position': merchant['position'],
            'point': first['recipient']['point'], 'viewport': gui, 'client_size': size,
            'anchor': first_anchor, 'occupied_tiles': occupied, **first}


def _validate_request(ui, character, uid):
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
    # This function runs inside MerchantBridge's single request thread.  Read
    # the app-owned observers directly; a localhost self-call would deadlock.
    source, merchant = pair(ui, character, farmer_preflight=True)
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


def _recovery_available(ui):
    if (getattr(ui, 'trade_qualification_prep_thread', None)
            and ui.trade_qualification_prep_thread.is_alive()):
        raise ValueError('Wait for trade qualification prep to stop before rechecking')
    if (getattr(ui, 'delivery_probe_thread', None) and ui.delivery_probe_thread.is_alive()
            or any(worker.is_alive() for worker in getattr(ui, 'delivery_workers', {}).values())):
        raise ValueError('Wait for delivery input to finish before rechecking prep')


def _fresh_recovery_evidence(ui, state):
    """Read-only current ownership used only for explicit disposition/replan."""
    from conquest.merchants.manual_sessions import canonical_ownership
    from conquest.recovery_override import evidence_digest
    character = character_name(state.get('character'))
    source, merchant = pair(ui, character, farmer_preflight=True)
    now = time.time()
    for snapshot in (source, merchant):
        if not 0 <= now-snapshot.get('timestamp', 0) <= 5:
            raise ValueError('Fresh memory evidence is required for prep recovery')
    proof = {'farmer': canonical_ownership(source, require_closed=False),
             'merchant': canonical_ownership(merchant, require_closed=False),
             'farmer_map': source['map_id'], 'merchant_map': merchant['map_id']}
    warehouse = {'available': False, 'money_available': False}
    observer = ui.app.observer
    with observer.lock:
        try:money_before = observer.town_trade({'action': 'warehouse-money'})
        except (ValueError, OSError, KeyError, TypeError) as error:
            money_before = None;warehouse['money_reason'] = type(error).__name__
        try:snapshot = observer.town_trade({'action': 'warehouse-items', 'rich': True})
        except (ValueError, OSError, KeyError, TypeError) as error:
            snapshot = None;warehouse['reason'] = type(error).__name__
        try:money_after = observer.town_trade({'action': 'warehouse-money'})
        except (ValueError, OSError, KeyError, TypeError) as error:
            money_after = None;warehouse['money_reason'] = type(error).__name__
    if snapshot is not None:
        items = list(snapshot['items'])
        exact_items(items)  # binding, gems and quantities must all be rich.
        warehouse.update(available=True, capacity=snapshot['capacity'], items=items)
    if money_before is not None and money_after is not None:
        if money_before != money_after:
            raise ValueError('Warehouse silver changed during recovery observation')
        warehouse.update(money_available=True, silver=money_after['silver'],
                         stored_silver=money_after['stored_silver'],
                         amount_field=money_after.get('amount'))
    proof['warehouse'] = warehouse
    return {'observed_at': now, 'farmer': source, 'merchant': merchant,
            'proof': proof, 'ownership_digest': evidence_digest(proof),
            'farmer_identity_matches': source.get('identity') == state.get('farmer', {}).get('identity'),
            'merchant_identity_matches': merchant.get('identity') == state.get('merchant', {}).get('identity')}


def recheck(ui):
    """Preview an unresolved prep incident without issuing any gameplay input."""
    from conquest.recovery_override import evidence_digest
    _recovery_available(ui)
    state = _read()
    if not state or state.get('phase') not in PENDING:
        raise ValueError('No unresolved trade qualification prep is available')
    incident = evidence_digest(state)
    fresh = _fresh_recovery_evidence(ui, state)
    if evidence_digest(_read()) != incident:
        raise ValueError('Trade qualification prep changed during recheck')
    preview = {'incident_digest': incident, 'evidence_digest': fresh['ownership_digest'],
               'fresh_evidence': fresh, 'original_phase': state['phase']}
    _write_durable(Path(str(JOURNAL)+'.recheck.json'), preview)
    return preview


def operator_override(ui, *, operator_confirmed=False, confirmation_reference=None,
                      incident_digest=None, operator=None):
    """Close uncertain prep input without claiming a deposit, withdrawal or fare."""
    from conquest.recovery_override import evidence_digest, operator_override as close
    _recovery_available(ui)
    if (operator_confirmed is not True or not isinstance(incident_digest, str)
            or not incident_digest or confirmation_reference != incident_digest):
        raise ValueError('Confirm the exact previewed trade-prep incident digest')
    state = _read()
    if state and state.get('phase') == 'operator_overridden':
        prior = state.get('operator_override') or {}
        if (prior.get('confirmation_reference') == confirmation_reference
                and prior.get('original_evidence_digest') == incident_digest):
            return {'phase': 'operator_overridden', 'incident_digest': incident_digest,
                    'historical_outcome': 'unknown', 'replan_required': True}
        raise ValueError('Trade prep was already overridden with different confirmation')
    if not state or state.get('phase') not in PENDING or evidence_digest(state) != incident_digest:
        raise ValueError('Trade qualification prep changed; recheck before overriding')
    preview = read_json(Path(str(JOURNAL)+'.recheck.json'))
    if (preview.get('incident_digest') != incident_digest
            or not 0 <= time.time()-preview.get('fresh_evidence', {}).get('observed_at', 0) <= 30):
        raise ValueError('A fresh trade-prep recheck is required before overriding')
    fresh = _fresh_recovery_evidence(ui, state)
    if fresh['ownership_digest'] != preview.get('evidence_digest'):
        raise ValueError('Current ownership changed; recheck trade prep before overriding')
    result = close(JOURNAL, pending_phases=PENDING, operator_confirmed=True,
        confirmation_reference=confirmation_reference, incident_digest=incident_digest,
        operator=operator, fresh_evidence=fresh, incident='trade-qualification-prep')
    # Force crash-intent completion and strict parse before releasing the hold.
    result = _read()
    return {'phase': result['phase'], 'incident_digest': incident_digest,
            'historical_outcome': 'unknown', 'replan_required': True}


def _reconcile_deposit(loop, state, *, send=request):
    intent = state.get('deposit')
    if not isinstance(intent, dict):
        return False
    item = intent.get('item') or {}
    uid = item.get('uid')
    before_bag = intent.get('bag_before')
    before_bank = intent.get('warehouse_before')
    if (type(uid) is not int or not isinstance(before_bag, list)
            or not isinstance(before_bank, list)
            or type(intent.get('warehouse_capacity')) is not int):
        raise ValueError('Pending warehouse deposit evidence is incomplete')
    source = send({'action': 'delivery-source'})['farmer']
    _same_participant(source, state['farmer'], 'Farmer')
    warehouse = loop.town('warehouse-items', rich=True)
    if warehouse['capacity'] != intent['warehouse_capacity']:
        raise ValueError('Warehouse capacity changed during deposit reconciliation')
    old_bag, old_bank = exact_items(before_bag), exact_items(before_bank)
    new_bag, new_bank = exact_items(source['inventory']), exact_items(warehouse['items'])
    if uid not in old_bag or uid in old_bank or old_bag[uid] != exact_items([item])[uid]:
        raise ValueError('Pending deposit baseline does not own the exact selected item')
    expected_bag = dict(old_bag);del expected_bag[uid]
    expected_bank = dict(old_bank);expected_bank[uid] = old_bag[uid]
    if new_bag == expected_bag and new_bank == expected_bank:
        if uid not in state.setdefault('banked_uids', []):
            state['banked_uids'].append(uid)
        _save(state, 'warehouse_opening', deposit=None)
        return True
    if new_bag == old_bag and new_bank == old_bank:
        return False
    raise ValueError('Pending warehouse deposit changed ownership unexpectedly; no input repeated')


def _bank_nonselected(loop, state, *, send=request):
    from conquest.banking import open_warehouse, close_warehouse, transfer
    if state['phase'] == 'prepared':
        _save(state, 'warehouse_opening')
    open_warehouse(loop)
    if state.get('phase') == 'deposit_pending':
        _reconcile_deposit(loop, state, send=send)
    while True:
        source = send({'action': 'delivery-source'})['farmer']
        _same_participant(source, state['farmer'], 'Farmer')
        _selected(source, state['selected_uid'])
        candidates = [item for item in source['inventory']
                      if item.get('uid') != state['selected_uid'] and stash_candidate(item)]
        if not candidates:
            break
        item = candidates[0]
        warehouse = loop.town('warehouse-items', rich=True)
        if item['uid'] in exact_items(warehouse['items']):
            raise ValueError('Warehouse already contains the carried deposit UID')
        intent = {'item': item, 'bag_before': source['inventory'],
                  'warehouse_before': list(warehouse['items']),
                  'warehouse_capacity': warehouse['capacity']}
        _save(state, 'deposit_pending', deposit=intent)
        try:
            receipt = loop.town('warehouse-deposit', uid=item['uid'])
        except (OSError, ValueError):
            # The durable pending boundary remains.  A same-request retry
            # decides from fresh bag/bank ownership before any repeat.
            raise
        if receipt.get('verified_in_warehouse') is not True:
            raise ValueError('Warehouse deposit receipt is unverified')
        _reconcile_deposit(loop, state, send=send)
    money = loop.town('warehouse-money')
    fare = state['route']['outbound']['fare']
    if money['silver'] < fare:
        amount = fare-money['silver']
        if amount > money['stored_silver']:
            raise ValueError('Warehouse lacks the verified Market fare')
        before = {'silver': money['silver'], 'stored_silver': money['stored_silver']}
        _save(state, 'withdraw_pending', withdraw={'amount': amount, 'before': before})
        unchanged = loop.town('warehouse-money')
        if any(unchanged.get(key) != value for key, value in before.items()):
            raise ValueError('Warehouse money changed before withdrawal; no input issued')
        _save(state, 'withdraw_submitted')
        transfer(loop, 'withdraw', amount)
        fresh = loop.town('warehouse-money')
        if (fresh['silver'] != before['silver']+amount
                or fresh['stored_silver'] != before['stored_silver']-amount):
            raise ValueError('Market-fare withdrawal needs reconciliation')
        _save(state, 'warehouse_opening', withdraw=None)
    close_warehouse(loop)
    fresh = send({'action': 'delivery-source'})['farmer']
    _payload_is_safe(fresh, state)
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
        raise ValueError('Fare withdrawal outcome needs attention; no transfer repeated')
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
        if phase in ('withdraw_pending', 'withdraw_submitted'):
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
            source, merchant = pair(ui, state['character'], farmer_preflight=True)
            _same_participant(merchant, state['merchant'], 'Merchant')
            if source['map_id'] != state['origin']:
                raise ValueError('Banked prep changed maps before the saved fare; no input issued')
            _payload_is_safe(source, state)
            if state['origin'] == 1036:
                _save(state, 'market', leg_before=None)
                phase = 'market'
            else:
                from conquest.meteor_banking import trip
                def before_submit():
                    source, merchant = pair(ui, state['character'], farmer_preflight=True)
                    _same_participant(merchant, state['merchant'], 'Merchant')
                    if source['map_id'] != state['origin']:
                        raise ValueError('Farmer left the fare origin before submission')
                    _payload_is_safe(source, state)
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
            _payload_is_safe(source, state)
            def prep_send(body):
                if (body.get('action') == 'delivery-target'
                        and set(body) == {'action', 'character'}):
                    return send({'action': 'trade-qualification-prep-target',
                                 'character': body['character']})
                return send(body)
            target = prep_send({'action': 'delivery-target', 'character': state['character']})
            plan = {'merchant': state['character'], 'position': target['merchant_position']}
            _save(state, 'approaching', merchant_position=target['merchant_position'])
            from conquest.merchants.delivery_route import approach_merchant
            if not approach_merchant(loop, plan, prep_send, deadline=time.time()+30):
                raise ValueError('Could not reach a memory-actionable position near the merchant')
            source, merchant = pair(ui, state['character'])
            _same_participant(source, state['farmer'], 'Farmer')
            _same_participant(merchant, state['merchant'], 'Merchant')
            _payload_is_safe(source, state)
            target = prep_send({'action': 'delivery-target', 'character': state['character']})
            if not target.get('ready'):
                raise ValueError('Merchant is not memory-actionable after approach')
            source, merchant = pair(ui, state['character'])
            _same_participant(merchant, state['merchant'], 'Merchant')
            _payload_is_safe(source, state)
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
            ui, character, selected_uid)
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
            state.pop('attention_required', None)
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
                _save(state, error=str(error), error_type=type(error).__name__,
                      attention_required=True)
        thread = threading.Thread(target=work, daemon=True, name='trade-qualification-prep')
        ui.trade_qualification_prep_thread = thread
        thread.start()
        return {'started': True, 'request_id': state['request_id'], 'phase': state['phase'],
                'character': character, 'selected_uid': selected_uid}


def status(ui):
    state = _read()
    running = getattr(ui, 'trade_qualification_prep_thread', None)
    return {'running': bool(running and running.is_alive()), 'state': state}
