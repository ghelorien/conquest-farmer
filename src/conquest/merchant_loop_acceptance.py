"""Machine-local, bounded native hunt/delivery acceptance; never owns input.

The flag adds one early-return obligation, not an alternate gameplay driver.
Existing durable trade, banking, refill and town-return proofs remain authority.
"""
from contextlib import closing
from copy import deepcopy
import json
import hashlib
from pathlib import Path
import sqlite3
import time
import uuid

from conquest.character_context import current, farmer_name, state_path
from conquest.merchants.delivery import eligible, exact_items

STATE = Path(state_path('.runtime/farmer-loop-acceptance.sqlite3'))
PICKUPS = Path(state_path('reports/desktop-farming/pickups.jsonl'))
MERCHANT_CAPABILITIES = ('trade_request', 'trade', 'booth_input', 'inventory_panel')


def profile_id():
    context = current()
    return context.profile.id if context else farmer_name()


def process(identity):
    keys = ('pid', 'creation_time_100ns', 'path')
    if (not isinstance(identity, dict)
            or any(type(identity.get(k)) is not int or identity[k] <= 0 for k in keys[:2])
            or not isinstance(identity.get('path'), str) or not identity['path']):
        raise ValueError('Acceptance requires an exact farmer process identity')
    return {key: identity[key] for key in keys}


def state():
    if not STATE.exists():
        return {'enabled': False, 'phase': 'not_configured'}
    with closing(sqlite3.connect(STATE.resolve().as_uri()+'?mode=ro', uri=True)) as db:
        row = db.execute('SELECT value FROM state WHERE id=1').fetchone()
    if not row:
        return {'enabled': False, 'phase': 'not_configured'}
    result = json.loads(row[0])
    if result.get('schema_version') != 1 or type(result.get('enabled')) is not bool:
        raise ValueError('Acceptance evidence is malformed; no new cycle can start')
    return result


def update(event, change):
    """Serialize app enable/disable with route checkpoints across processes."""
    STATE.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(STATE, timeout=5)) as db, db:
        db.execute('PRAGMA synchronous=FULL')
        db.execute('CREATE TABLE IF NOT EXISTS state(id INTEGER PRIMARY KEY, value TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, at REAL, event TEXT, value TEXT)')
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT value FROM state WHERE id=1').fetchone()
        old = json.loads(row[0]) if row else {'enabled': False, 'phase': 'not_configured'}
        new = change(deepcopy(old))
        if new is None or new == old:
            return old
        new['updated_at'] = time.time()
        encoded = json.dumps(new, sort_keys=True)
        db.execute('INSERT OR REPLACE INTO state VALUES(1,?)', (encoded,))
        db.execute('INSERT INTO events(at,event,value) VALUES(?,?,?)', (time.time(), event, encoded))
        return json.loads(encoded)


def source_checked(source, saved=None):
    now = time.time()
    if (source.get('character') != farmer_name() or source.get('server') != 'America'
            or type(source.get('character_uid')) is not int or source['character_uid'] <= 0
            or type(source.get('timestamp')) not in (int, float) or not 0 <= now-source['timestamp'] <= 5
            or source.get('hp', 0) <= 0 or source.get('trade') or source.get('request')):
        raise ValueError('Acceptance requires fresh living idle farmer memory')
    process(source.get('identity'))
    inventory = exact_items(source['inventory'])
    if saved and (saved['farmer_profile_id'] != profile_id()
            or any(source.get(key) != saved['farmer'].get(key)
                   for key in ('identity', 'character_uid', 'character', 'server'))):
        raise ValueError('Acceptance farmer profile, process or character changed')
    return inventory


def deliverable_item(item):
    from conquest.valuables import DRAGONBALL_TYPES
    plus=item.get('plus')
    return (eligible(item) and item.get('type_id') not in DRAGONBALL_TYPES
            and not (type(plus) is int and plus >= 2))


def merchant_live(name, status, snapshot, *, require_capacity=True):
    """This bounded loop needs trade/refill, not login or recovery controls."""
    from conquest.merchants.capacity import available_slots
    return bool(status.get('enabled') is True and status.get('refill', {}).get('enabled') is True
        and all(status.get('qualification', {}).get(capability) is True for capability in MERCHANT_CAPABILITIES)
        and isinstance(status.get('profile_id'), str) and status['profile_id']
        and not status.get('pending') and not status.get('manual_input_fence')
        and not status.get('input_active') and not status.get('error') and not status.get('needs_attention')
        and snapshot.get('character') == name and snapshot.get('server') == 'America'
        and type(snapshot.get('character_uid')) is int and snapshot['character_uid'] > 0
        and snapshot.get('hp', 0) > 0 and snapshot.get('map_id') == 1036
        and snapshot.get('booth_open') is True and type(snapshot.get('own_booth_uid')) is int
        and snapshot['own_booth_uid'] > 0 and not snapshot.get('trade') and not snapshot.get('request')
        and 0 <= time.time()-snapshot.get('timestamp', 0) <= 5
        and (available_slots(snapshot) > 0 if require_capacity else available_slots(snapshot) >= 0))


def merchant_qualification(ui, name, snapshot):
    driver=ui.runtime.controllers[name].driver
    adapter=driver.observer.adapter
    if (snapshot['identity'] != adapter.identity or not isinstance(adapter.expected_sha256,str)
            or not adapter.expected_sha256):
        raise ValueError('Acceptance merchant process or build does not match its attached driver')
    for capability in MERCHANT_CAPABILITIES:driver.require_qualified(capability)
    path=Path(driver.qualification)
    encoded=path.read_bytes();data=json.loads(encoded)
    if (data.get('client_sha256') != adapter.expected_sha256 or data.get('character') != name
            or data.get('server') != 'America' or not data.get('evidence')
            or not all(data.get('capabilities',{}).get(capability) is True for capability in MERCHANT_CAPABILITIES)):
        raise ValueError('Acceptance merchant qualification changed during observation')
    return {'path':str(path.resolve()),'sha256':hashlib.sha256(encoded).hexdigest(),
            'client_sha256':adapter.expected_sha256}


def qualification_unchanged(evidence):
    try:
        return bool(evidence.get('client_sha256')
            and hashlib.sha256(Path(evidence['path']).read_bytes()).hexdigest()==evidence['sha256'])
    except (KeyError,OSError,TypeError):return False


def configure(ui, body):
    action = body.get('action')
    if action == 'farmer-loop-acceptance-status' and set(body) == {'action'}:
        return state()
    if action == 'farmer-loop-acceptance-abort':
        return abort(ui, body)
    if action in ('farmer-loop-acceptance-override-preview','farmer-loop-acceptance-override'):
        return operator_override(ui, body)
    allowed = {'action', 'enabled', 'farmer_profile_id'}
    if body.get('enabled') is True:
        allowed |= {'request_id', 'cycles'}
    if (action != 'farmer-loop-acceptance' or set(body) != allowed
            or type(body.get('enabled')) is not bool or body.get('farmer_profile_id') != profile_id()):
        raise ValueError('Unsupported farmer acceptance command or profile')
    if not body['enabled']:
        def disable(old):
            if not old.get('enabled'):return None
            if old.get('active'):
                raise ValueError('Acceptance has an unresolved cycle; Farming Off always stops input, but reconcile its native delivery/refill/return before disabling this evidence hold')
            old.update(enabled=False, phase='disabled', disabled_at=time.time())
            return old
        return update('disabled', disable)
    key = body['request_id']
    if (type(body.get('cycles')) is not int or body['cycles'] != 3
            or not isinstance(key, str) or not 1 <= len(key) <= 100):
        raise ValueError('Acceptance requires three cycles and a stable request ID')
    old = state()
    if old.get('request_id') == key:return old  # Lost enable ack never resets a baseline.
    if old.get('enabled'):
        raise ValueError('Disable the existing acceptance run before starting another')
    control = ui.app.control.snapshot()
    if (control.get('enabled') or control.get('paused') or ui.coordinator.stopped
            or ui.coordinator.owner or ui.coordinator.manual_session_blocked('Farmer')
            or ui.app.mouse_priority.active() or not ui.safe_to_yield()):
        raise ValueError('Acceptance must be armed with Farming Off and no input or pause holds')
    from conquest.merchants.delivery_operation import guard_reload
    guard_reload()
    from conquest.merchants.delivery_journey import pending as journey_pending
    from conquest.meteor_banking import pending as meteor_pending
    from conquest.storage_overflow import pending as overflow_pending
    if journey_pending() or meteor_pending() or overflow_pending():
        raise ValueError('Reconcile native town transactions before arming acceptance')
    from conquest.merchants.delivery_bridge import dispatch
    source = dispatch(ui, {'action': 'delivery-source'})['farmer']
    inventory = source_checked(source)
    from conquest.merchants.farmer_trade import FarmerTradeDriver
    from conquest.merchants.farmer_qualification import qualification_path
    FarmerTradeDriver(ui).require_qualified()
    qualification = qualification_path(ui.app.observer, migrate=False)
    qualification_evidence = {'path': str(qualification.resolve()),
        'sha256': hashlib.sha256(qualification.read_bytes()).hexdigest(),
        'client_sha256': ui.app.observer.adapter.expected_sha256}
    from conquest.merchants.farmer_preferences import permits_new_delivery
    permits_new_delivery()
    merchants = {}
    for name, status in ui.runtime.status().items():
        snap = status.get('snapshot') or {}
        if merchant_live(name,status,snap):
            process(snap.get('identity'))
            evidence=merchant_qualification(ui,name,snap)
            merchants[str(name)] = {'profile_id': status.get('profile_id'),
                'identity': snap['identity'], 'character_uid': snap['character_uid'],
                'own_booth_uid':snap['own_booth_uid'],'qualification':evidence}
    if not merchants:
        raise ValueError('Acceptance requires an exact ready merchant with trading and refill enabled, qualified booth input, and no manual or transaction holds')
    route = ui.app.selected_route
    if route is None:raise ValueError('Choose a native farmer route before acceptance')
    from conquest.banking import policy as banking_policy
    from conquest.discord_notify import read_json
    from conquest.meteor_banking import POLICY as TRAVEL_POLICY
    bank_policy=banking_policy()
    travel=read_json(TRAVEL_POLICY).get('origins',{}).get(str(route.restock_map_id),{})
    outbound,inbound=travel.get('outbound',{}),travel.get('return',{})
    if (not bank_policy.get('enabled') or not bank_policy.get('deposit_after_shopping',True)
            or not outbound.get('verified') or not inbound.get('verified')
            or (outbound.get('source_map'),outbound.get('destination_map'),
                inbound.get('source_map'),inbound.get('destination_map')) !=
               (route.restock_map_id,1036,1036,route.restock_map_id)):
        raise ValueError('Acceptance requires enabled native banking and a verified round-trip merchant route')
    from conquest.town_visit import TownVisit
    if TownVisit().active_id():
        raise ValueError('Complete the current required town return before arming acceptance')
    def enable(previous):
        if previous.get('request_id') == key:return None
        if previous.get('enabled'):raise ValueError('Acceptance changed while arming; recheck')
        return {'schema_version': 1, 'enabled': True, 'phase': 'armed',
                'request_id': key, 'run_id': uuid.uuid4().hex, 'target_cycles': 3,
                'farmer_profile_id': profile_id(), 'farmer': source,
                'route_id': route.id, 'hunt_map_id': route.map_id,
                'qualification': qualification_evidence, 'rollout_promoted': False,
                'merchants': merchants, 'cycle_baseline_at': time.time(),
                'baseline_uids': sorted(inventory), 'started_at': time.time(),
                'cycles': [], 'active': None}
    return update('enabled', enable)


def cycle_pending():
    row = state()
    return bool(row.get('enabled') and row.get('active')
                and row['active']['phase'] in ('triggered', 'town', 'delivered'))


def journey_scope():
    row = state();cycle = row.get('active')
    if not row.get('enabled') or not cycle:return None
    return {'run_id': row['run_id'], 'cycle_id': cycle['cycle_id'], 'item': cycle['item']}


def trial_permitted(loop=None):
    """A bounded local trial is not global rollout or a qualification bypass."""
    row = state();cycle = row.get('active')
    if (not row.get('enabled') or not cycle or cycle['phase'] not in ('town', 'delivered')
            or row.get('farmer_profile_id') != profile_id() or len(row['cycles']) >= row['target_cycles']):
        return False
    if loop is not None and (loop.route.id != row['route_id']
            or process(loop.identity) != process(row['farmer']['identity'])):
        return False
    qualification = row.get('qualification') or {}
    try:
        return hashlib.sha256(Path(qualification['path']).read_bytes()).hexdigest() == qualification['sha256']
    except (KeyError, OSError):
        return False


def trial_delivery_permitted(ui, key, character, uids, origin):
    if not trial_permitted():return False
    row = state();cycle = row['active']
    admission = next((a for a in cycle['admissions'] if a['request_id'] == key), None)
    statuses=ui.runtime.status()
    merchant=statuses.get(character,{})
    return bool(admission and cycle['phase'] == 'town'
        and merchant_allowed(character,merchant,merchant.get('snapshot') or {})
        and character == admission['merchant'] and uids == [cycle['item']['uid']]
        and origin.get('town_visit_id') == cycle.get('town_visit_id')
        and origin.get('farmer_profile_id') == row['farmer_profile_id']
        and origin.get('visit_id') == admission.get('visit_id')
        and ui.app.observer.adapter.identity == row['farmer']['identity']
        and ui.app.observer.adapter.expected_sha256 == row['qualification']['client_sha256'])


def merchant_allowed(name, status, snapshot):
    row = state();cycle = row.get('active')
    if not row.get('enabled') or not cycle:return True
    saved = row['merchants'].get(str(name))
    try:
        return bool(saved and merchant_live(name,status,snapshot,require_capacity=cycle['phase']=='town')
            and status.get('profile_id') == saved['profile_id']
            and snapshot.get('identity') == saved['identity']
            and snapshot.get('character_uid') == saved['character_uid']
            and snapshot.get('own_booth_uid') == saved.get('own_booth_uid')
            and qualification_unchanged(saved.get('qualification') or {}))
    except (ValueError,KeyError,TypeError):return False


def hunt_provenance(row, item):
    """Only durable native pickup/verified-kill evidence can trigger a trial."""
    from conquest.town_visit import kill_checkpoint
    baseline = row['cycle_baseline_at'];now = time.time()
    checkpoint = kill_checkpoint(now=now, after_cursor=0, after_time=baseline)
    kill = checkpoint.get('resume_kill')
    if not checkpoint.get('available') or not kill or not baseline <= kill['time'] <= now:return None
    try:
        with PICKUPS.open(encoding='utf-8') as stream:
            pickups = [json.loads(line) for line in stream if line.strip()]
    except (OSError, ValueError):return None
    pickup = next((p for p in reversed(pickups)
        if p.get('inventory_uid') == item['uid'] and p.get('type_id') == item['type_id']
        and p.get('plus') == item.get('plus') and p.get('increase', 0) > 0
        and p.get('map_id') == row['hunt_map_id'] and baseline <= p.get('timestamp', 0) <= now
        and p.get('source') in (None, 'ground_pickup', 'inventory_gain')), None)
    return ({'pickup': pickup, 'verified_kill': kill, 'kill_checkpoint': checkpoint}
            if pickup and kill['time'] <= pickup['timestamp'] else None)


def _live_health(row, health):
    data = health.get('embedded_controls', {});control = data.get('control', {})
    life = data.get('life') or {}
    if process(health.get('target')) != process(row['farmer']['identity']):
        raise ValueError('Acceptance process changed; no cycle can continue')
    return (control.get('enabled') is True and not control.get('paused')
            and data.get('manual_mouse') is False and data.get('manual_input_fence') is False
            and life.get('dead_candidate') is False and life.get('current_hp', 0) > 0
            and life.get('map_id') == row['hunt_map_id']
            and 0 <= time.time()-data.get('observed_at', 0) <= 1)


def observe_hunting(loop, health, *, send=None):
    row = state()
    if not row.get('enabled'):return False
    if row['farmer_profile_id'] != profile_id() or row['route_id'] != loop.route.id:
        raise ValueError('Acceptance route or farmer profile changed')
    if not _live_health(row, health):return False
    from conquest.merchants.bridge import request
    from conquest.merchants.memory import TransitObservationChanged
    send = send or request
    active = row.get('active')
    try:
        source = send({'action': 'delivery-source'})['farmer']
    except TransitObservationChanged:
        # A memory reader observed movement while assembling the *pre-cycle*
        # farmer snapshot.  This authorizes no input and has no ownership or
        # transaction meaning.  Defer one native tick only if the durable run
        # is still exactly the same untouched armed state, or the same fully
        # settled cycle awaiting its first resumed hunt observation. A
        # concurrent trigger or any other active service phase must instead be
        # reconciled by its owner.
        current=state()
        same_run=(current.get('enabled') and current.get('run_id')==row.get('run_id')
                and current.get('farmer_profile_id')==row.get('farmer_profile_id')
                and current.get('route_id')==row.get('route_id'))
        current_active=current.get('active') or {}
        same_awaiting=bool(active and active.get('phase')=='awaiting_hunt'
            and current_active.get('cycle_id')==active.get('cycle_id')
            and current_active.get('phase')=='awaiting_hunt')
        if same_run and ((current.get('phase')=='armed' and current.get('active') is None)
                         or same_awaiting):
            return False
        raise
    inventory = source_checked(source, row)
    from conquest.banking import urgent_valuables
    if urgent_valuables(source['inventory']):
        return False
    if source['map_id'] != row['hunt_map_id']:
        raise ValueError('Acceptance farmer left the hunt map during observation')
    if active and active['phase'] == 'awaiting_hunt':
        visit = loop.town_visit.state()
        if (visit.get('phase') == 'complete' and visit.get('town_visit_id') == active['town_visit_id']
                and visit.get('farmer_profile_id') == row['farmer_profile_id']
                and visit.get('hunt_map_id') == row['hunt_map_id']
                and process(visit.get('return_target')) == process(row['farmer']['identity'])
                and (visit.get('first_verified_resume_kill') or {}).get('time', 0) >= active['service_finished_at']):
            def complete(current):
                if not current.get('enabled') or current.get('active', {}).get('cycle_id') != active['cycle_id']:return None
                cycle = current['active']
                if cycle['phase'] != 'awaiting_hunt':return None
                cycle.update(phase='completed', town_return=visit, completed_at=time.time())
                current['cycles'].append(cycle);current['active'] = None
                current['baseline_uids'] = sorted(set(current['baseline_uids']) | set(inventory))
                current['cycle_baseline_at'] = time.time()
                done = len(current['cycles']) >= current['target_cycles']
                current.update(enabled=not done, phase='completed' if done else 'armed')
                return current
            finished = update('cycle_completed', complete)
            loop.record('merchant_acceptance_cycle_completed', run_id=finished['run_id'],
                        completed_cycles=len(finished['cycles']), target_cycles=finished['target_cycles'])
        return False
    if active:return active['phase'] in ('triggered', 'town', 'delivered')
    candidates = [i for i in source['inventory'] if i['uid'] not in row['baseline_uids'] and deliverable_item(i)]
    if not candidates:return False
    selected = sorted(candidates, key=lambda i: (-(i['plus'] if type(i.get('plus')) is int else 0), i['uid']))[0]
    provenance = hunt_provenance(row, selected)
    if provenance is None:return False
    def trigger(current):
        if not current.get('enabled') or current.get('run_id') != row['run_id'] or current.get('active'):return None
        current['active'] = {'cycle_id': uuid.uuid4().hex, 'phase': 'triggered', 'item': selected,
                             'triggered_at': time.time(), 'hunt_evidence': source,
                             'provenance': provenance, 'admissions': []}
        current['phase'] = 'running'
        return current
    result = update('new_item_return_required', trigger)
    return bool(result.get('enabled') and result.get('active'))


def town_started(loop, visit, source):
    row = state()
    if not row.get('enabled') or not row.get('active'):return False
    source_checked(source, row)
    def begin(current):
        if not current.get('enabled') or current.get('run_id') != row['run_id']:return None
        cycle = current['active']
        if cycle.get('town_visit_id') not in (None, visit['town_visit_id']):
            raise ValueError('Acceptance town visit changed')
        cycle['town_visit_id'] = visit['town_visit_id']
        if cycle['phase'] == 'triggered':cycle['phase'] = 'town'
        return current
    update('town_started', begin)
    return True


def town_input_boundary():
    def mark(row):
        if not row.get('enabled') or not row.get('active'):return None
        row['active'].setdefault('native_town_input_maybe_started_at',time.time())
        return row
    update_if_active('native_town_input_boundary',mark)


def plan_items(farmer, items):
    row = state();cycle = row.get('active')
    if not row.get('enabled') or not cycle or cycle['phase'] not in ('town', 'delivered'):return items
    source_checked(farmer, row)
    if cycle['phase'] == 'delivered':return []
    wanted = [cycle['item']]
    if items is not None and exact_items(items) != exact_items(wanted):
        raise ValueError('Acceptance cannot substitute another delivery payload')
    return wanted


def admitted(active):
    def save(row):
        cycle = row.get('active')
        if not row.get('enabled') or not cycle or cycle['phase'] != 'town':return None
        if (active.get('town_visit_id') != cycle.get('town_visit_id')
                or active.get('farmer_profile_id') != row['farmer_profile_id']
                or exact_items(active['items']) != exact_items([cycle['item']])):
            raise ValueError('Native delivery does not match the acceptance cycle')
        process(active.get('merchant_identity'))
        previous = next((a for a in cycle['admissions'] if a['request_id'] == active['request_id']), None)
        if previous:
            if previous != active:raise ValueError('Acceptance delivery admission changed')
            return None
        cycle['admissions'].append(deepcopy(active));return row
    update_if_active('delivery_admitted', save)


def update_if_active(event, change):
    if not state().get('enabled'):return None
    return update(event, change)


def settled(receipt):
    def save(row):
        cycle = row.get('active')
        if not row.get('enabled') or not cycle or cycle['phase'] != 'town':return None
        admitted = next((a for a in cycle['admissions'] if a['request_id'] == receipt.get('request_id')), None)
        if not admitted:return None
        if (receipt.get('outcome') != 'transferred' or not receipt.get('proof_digest')
                or receipt.get('next_action') != 'release_route' or receipt.get('cleanup_pending')
                or receipt.get('phase', 'verified') != 'verified'
                or receipt.get('merchant') != admitted['merchant']
                or receipt.get('town_visit_id') != cycle['town_visit_id']
                or receipt.get('verified_at', 0) < admitted['started_at']
                or exact_items(receipt.get('items', [])) != exact_items([cycle['item']])):
            return None
        cycle.update(phase='delivered', delivery=deepcopy(receipt));return row
    update_if_active('delivery_verified', save)


def reconcile_route_receipts():
    if not cycle_pending():return
    from conquest.discord_notify import read_json
    from conquest.merchants.delivery_route import STATE as ROUTE_STATE
    for receipt in read_json(ROUTE_STATE).get('receipts', []):settled(receipt)


def pending_delivery_matches(native):
    """Only this cycle's unchanged durable admission may resume its transfer."""
    row=state();cycle=row.get('active') or {}
    return bool(row.get('enabled') and row.get('farmer_profile_id')==profile_id()
        and cycle.get('phase') in ('town','delivered')
        and any(admission==native for admission in cycle.get('admissions',[])))


def refill_observed(statuses):
    def save(row):
        cycle = row.get('active')
        if not row.get('enabled') or not cycle or cycle['phase'] != 'delivered':return None
        receipt = cycle['delivery'];merchant = statuses.get(receipt['merchant'], {})
        refill = merchant.get('refill') or {};snapshot = merchant.get('snapshot') or {}
        admission = next(a for a in cycle['admissions'] if a['request_id'] == receipt['request_id'])
        if (not merchant_allowed(receipt['merchant'],merchant,snapshot)
                or refill.get('pending') is not False or refill.get('status') not in ('completed', 'no_stock', 'booth_full')
                or refill.get('source_delivery_operation_id') != receipt['request_id']
                or refill.get('last_completed_check_at', 0) < receipt['verified_at']
                or snapshot.get('identity') != admission['merchant_identity']
                or snapshot.get('hp', 0) <= 0 or snapshot.get('map_id') != 1036
                or not 0 <= time.time()-snapshot.get('timestamp', 0) <= 5
                or snapshot.get('trade') or snapshot.get('request') or merchant.get('pending')
                or merchant.get('manual_input_fence')):
            return None
        item = cycle['item'];wanted = exact_items([item])[item['uid']]
        bag, booth = exact_items(snapshot['inventory']), exact_items(snapshot.get('booth', []))
        if item['uid'] in bag and item['uid'] in booth:return None
        disposition = ('listed' if booth.get(item['uid']) == wanted else
                       'queued' if bag.get(item['uid']) == wanted else None)
        if disposition is None:return None
        cycle['refill'] = {'observed_at': time.time(), 'state': deepcopy(refill),
                           'snapshot': deepcopy(snapshot), 'item_disposition': disposition}
        return row
    update_if_active('merchant_refill_verified', save)


def refill_complete():
    cycle=state().get('active') or {}
    return bool(cycle.get('refill'))


def refill_source(key, character):
    row=state();cycle=row.get('active') or {}
    return (key if row.get('enabled') and cycle.get('phase')=='delivered'
            and cycle.get('delivery',{}).get('request_id')==key
            and cycle['delivery'].get('merchant')==character else None)


def finish_town(loop, *, send=None):
    from conquest.merchants.bridge import request
    send = send or request
    reconcile_route_receipts()
    refill_observed(send({'action': 'status'}).get('characters', {}))
    def finish(row):
        if not row.get('enabled') or not row.get('active'):return None
        cycle = row['active']
        if cycle['phase'] != 'delivered' or not cycle.get('refill'):
            raise ValueError('Acceptance cycle lacks exact delivery and completed merchant refill evidence; review before continuing')
        cycle.update(phase='awaiting_hunt', service_finished_at=time.time());return row
    update_if_active('town_service_complete', finish)


def verify_carried_or_delivered(source):
    row = state();cycle = row.get('active')
    if not row.get('enabled') or not cycle:return False
    inventory = source_checked(source, row)
    if cycle['phase'] == 'delivered':return False
    item = cycle['item']
    if inventory.get(item['uid']) != exact_items([item])[item['uid']]:
        raise ValueError('Acceptance item is not carried and has no matching bilateral receipt; no withdrawal or trade replay allowed')
    return True


def abort(ui, body):
    """Input-free abort only before town input or after all service is terminal."""
    if (set(body)!={'action','farmer_profile_id','run_id'}
            or body['farmer_profile_id']!=profile_id()):
        raise ValueError('Acceptance abort requires this farmer and exact run ID')
    row=state()
    if row.get('run_id')!=body['run_id']:raise ValueError('Acceptance run changed; recheck before aborting')
    if row.get('phase')=='aborted':return row
    cycle=row.get('active')
    pre_input_town=bool(cycle and cycle['phase']=='town' and not cycle.get('admissions')
                       and not cycle.get('native_town_input_maybe_started_at'))
    if not row.get('enabled') or not cycle or (cycle['phase'] not in ('triggered','awaiting_hunt') and not pre_input_town):
        raise ValueError('Only a pre-input trigger or fully settled service can be aborted safely')
    statuses=restoration_checks(ui)
    from conquest.merchants.delivery_bridge import dispatch
    source=dispatch(ui,{'action':'delivery-source'})['farmer']
    inventory=source_checked(source,row)
    if cycle['phase']=='triggered' or pre_input_town:
        if cycle.get('admissions') or (cycle['phase']=='triggered' and cycle.get('town_visit_id')):
            raise ValueError('Triggered acceptance already has native input admission')
        item=cycle['item']
        if inventory.get(item['uid'])!=exact_items([item])[item['uid']]:
            raise ValueError('Pre-input acceptance item ownership changed; no abort inferred')
    elif not cycle.get('delivery') or not cycle.get('refill'):
        raise ValueError('Acceptance service lacks terminal bilateral/refill evidence')
    def save(current):
        if (not current.get('enabled') or current.get('run_id')!=row['run_id']
                or current.get('active')!=cycle):
            raise ValueError('Acceptance evidence changed during abort; recheck')
        current['aborted_cycle']={**cycle,'phase':'aborted','aborted_at':time.time(),
            'previous_phase':cycle['phase'],'farmer_recheck':source,
            'reason':'Operator stopped the temporary acceptance run; cycle not counted'}
        current.update(enabled=False,phase='aborted',active=None)
        return current
    return update('aborted',save)


def restoration_checks(ui):
    control=ui.app.control.snapshot()
    if (control.get('enabled') or control.get('paused') or ui.coordinator.owner
            or ui.coordinator.manual_session_blocked('Farmer') or ui.app.mouse_priority.active()
            or not ui.safe_to_yield()):
        raise ValueError('Stop Farming and wait for all native input to release before aborting acceptance')
    from conquest.merchants.delivery_operation import guard_reload
    from conquest.merchants.delivery_journey import pending as journey_pending
    from conquest.meteor_banking import pending as meteor_pending
    from conquest.storage_overflow import pending as overflow_pending
    guard_reload()
    if journey_pending() or meteor_pending() or overflow_pending():
        raise ValueError('Reconcile native town work before aborting acceptance')
    statuses=ui.runtime.status()
    if any(s.get('pending') or s.get('refill',{}).get('pending') or s.get('input_active')
           for s in statuses.values()):
        raise ValueError('Merchant transactions or refill remain unresolved')
    return statuses


def _digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def operator_override(ui, body):
    """Restore only the temporary mode; never resolve another native journal."""
    preview=body.get('action')=='farmer-loop-acceptance-override-preview'
    allowed={'action','farmer_profile_id','run_id'}
    if not preview:allowed|={'operator_confirmed','incident_digest'}
    if set(body)!=allowed or body.get('farmer_profile_id')!=profile_id():
        raise ValueError('Acceptance override requires an exact farmer, run and preview digest')
    row=state()
    if row.get('run_id')!=body['run_id']:raise ValueError('Acceptance run changed')
    if not preview and row.get('phase')=='operator_overridden':
        if body.get('operator_confirmed') is True and row['operator_override']['incident_digest']==body.get('incident_digest'):
            return row
        raise ValueError('Acceptance was overridden with different confirmation')
    if not row.get('enabled') or not row.get('active'):
        raise ValueError('No unresolved acceptance cycle is available')
    statuses=restoration_checks(ui)
    from conquest.merchants.delivery_bridge import dispatch
    source=dispatch(ui,{'action':'delivery-source'})['farmer']
    source_checked(source,row)
    warehouse={'available':False,'ownership_inference':'none'}
    observer=ui.app.observer
    try:
        with observer.lock:
            stored=observer.town_trade({'action':'warehouse-items','rich':True})
        warehouse={'available':True,'items':exact_items(stored['items']),
                   'capacity':stored['capacity'],'ownership_inference':'read_only_current_stock'}
    except (OSError,ValueError):pass  # No panel is opened to make evidence available.
    ownership={'farmer':{key:source[key] for key in ('character','character_uid','identity','silver')},
               'inventory':exact_items(source['inventory']),'warehouse':warehouse}
    incident=_digest({key:value for key,value in row.items() if key not in ('updated_at','override_preview')})
    evidence={'incident_digest':incident,'ownership_digest':_digest(ownership),
              'observed_at':time.time(),'ownership':ownership,
              'historical_outcome':'unknown_or_deferred','native_journals_unchanged':True}
    if preview:
        def remember(current):
            if _digest({k:v for k,v in current.items() if k not in ('updated_at','override_preview')})!=incident:
                raise ValueError('Acceptance changed during preview')
            current['override_preview']=evidence;return current
        update('override_preview',remember)
        return evidence
    saved=row.get('override_preview') or {}
    if (body.get('operator_confirmed') is not True or body.get('incident_digest')!=incident
            or saved.get('incident_digest')!=incident
            or not 0<=time.time()-saved.get('observed_at',0)<=30
            or saved.get('ownership_digest')!=evidence['ownership_digest']):
        raise ValueError('Confirm a fresh unchanged acceptance override preview')
    def save(current):
        if _digest({k:v for k,v in current.items() if k not in ('updated_at','override_preview')})!=incident:
            raise ValueError('Acceptance changed before override')
        current['overridden_cycle']=deepcopy(current['active'])
        current['operator_override']=evidence
        current.update(enabled=False,phase='operator_overridden',active=None)
        return current
    return update('operator_overridden',save)
