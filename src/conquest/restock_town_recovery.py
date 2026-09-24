"""One-shot continuation of a restock interrupted during Meteor return travel.

The operator claim is read-only with respect to the game.  It requires the
original game-process identity from independent supervision and a proved
pre-dialogue movement failure.  The native route consumes the claim before
resuming the skipped banking tail, so an uncertain tail can never replay.
"""
import hashlib
import json
from pathlib import Path
import time

from conquest.character_context import state_path
from conquest.discord_notify import read_json
from conquest.town_visit import _process_identity


EVENTS = Path(state_path('reports/overnight/events.jsonl'))
MONEY = Path(state_path('reports/banking/transfers.jsonl'))
FIELDS = ('uid', 'type_id', 'amount', 'plus', 'gem1', 'gem2', 'bound', 'quantity')


def _save_tail(visit, row):
    from conquest.meteor_banking import _durable_json
    _durable_json(visit.path, row)


def _native_tail_safe(loop, target, map_id):
    loop.check_stop()
    health=loop.health();data=health.get('embedded_controls') or {}
    life=data.get('life') or {};control=data.get('control') or {}
    if (not _process_identity(target) or health.get('target')!=target or loop.identity!=target
            or health.get('profile_id')!=loop.town_visit.profile
            or life.get('map_id')!=map_id or life.get('dead_candidate') is not False
            or life.get('current_hp',0)<=0 or control.get('enabled') is not False
            or control.get('paused') or data.get('manual_input_fence') or data.get('manual_mouse')
            or not 0<=time.time()-data.get('observed_at',0)<=1 or _other_holds()):
        raise ValueError('Interrupted restock continuation lacks fresh safe original ownership')


def capture_pre_admission_tail(loop):
    """Bind the exact historical grant rejection before native Meteor recovery."""
    import sqlite3
    from conquest.meteor_banking import JOURNAL
    from conquest.merchants.delivery_operation import JOURNAL as deliveries
    from conquest.merchants.service_visit import MarketVisit
    from conquest.merchants.bridge import request
    visit=loop.town_visit;row=visit.state()
    if row.get('town_work_completed_at'):return False
    prior=row.get('pre_admission_restock_tail')
    if prior:
        resumed=read_json(JOURNAL);market=read_json(MarketVisit().path)
        if (prior.get('phase')!='captured' or row.get('phase')!='town_work'
                or row.get('reasons')!=['restock']
                or any(resumed.get(k)!=prior['meteor'].get(k) for k in
                       ('started_at','scroll_uid','meteor_uids','origin','after'))
                or any(market.get(k)!=prior['market'].get(k) for k in
                       ('visit_id','town_visit_id','farmer_profile_id','started_at','deadline'))):
            raise ValueError('Captured restock continuation changed before Meteor recovery')
        _native_tail_safe(loop,prior['target'],1036)
        return True
    meteor=read_json(JOURNAL)
    if (row.get('phase')!='town_work' or row.get('reasons')!=['restock']
            or meteor.get('phase')!='storing_scroll'):return False
    history=row.get('history') or [];previous=history[-1] if history else {}
    target=previous.get('return_target');session=(row.get('baseline') or {}).get('session_id')
    historical=previous.get('resumed_hunting') or {}
    kills=read_json(state_path('reports/desktop-farming/kill-session.json'))
    if (previous.get('phase')!='complete' or not _process_identity(target)
            or not previous.get('completed_at',float('inf'))<row['required_at']
            or not session or historical.get('session_id')!=session
            or kills.get('started_at')!=session
            or row.get('route_id')!=loop.route.id or row.get('hunt_map_id')!=loop.route.map_id
            or historical.get('cursor',float('inf'))>=row['baseline'].get('cursor',0)
            or meteor.get('origin')!=loop.route.restock_map_id
            or meteor.get('exchange_verified') is not True
            or not row['required_at']<=meteor.get('started_at',0)
            or meteor.get('return_submitted_at') or meteor.get('receipts')
            or meteor.get('user_confirmed_scroll_consumption') or meteor.get('user_confirmed_scroll_transfer')):
        raise ValueError('Interrupted restock lacks its original process and exchange chain')
    _native_tail_safe(loop,target,1036)
    events=[e for e in _rows(EVENTS) if e.get('town_visit_id')==row['town_visit_id']]
    failure=next((e for e in reversed(events) if e.get('event')=='failed'
                  and e.get('detail')=='Unqualified merchant client fingerprint'
                  and e.get('error_type')=='conquest.merchants.bridge.MerchantRejected'),{})
    frames={(Path(f.get('file','')).name,f.get('function'),f.get('line'))
            for f in failure.get('failure_trace',[])}
    if (failure.get('event')!='failed' or failure.get('detail')!='Unqualified merchant client fingerprint'
            or failure.get('error_type')!='conquest.merchants.bridge.MerchantRejected'
            or ('delivery_route.py','_market_storage',396) not in frames
            or not {('banking.py','after_shopping'),('meteor_banking.py','resume'),
                    ('meteor_banking.py','market_bank')} <= {(a,b) for a,b,c in frames}
            or failure.get('time',0)<meteor['started_at']):
        raise ValueError('Restock continuation is not the exact pre-admission grant rejection')
    # Restart bookkeeping and this exact read-only capture rejection cannot
    # hide the original grant failure. Every later gameplay event remains held.
    for event in events:
        if event.get('time',0)<=failure['time']:continue
        if event.get('event') in ('started','stopped'):continue
        trace=event.get('failure_trace') or []
        last=trace[-1] if trace else {}
        if (event.get('event')=='failed' and event.get('error_type')=='builtins.ValueError'
                and event.get('detail')=='Merchant input or manual ownership still holds the interrupted trip'
                and Path(last.get('file','')).name=='restock_town_recovery.py'
                and last.get('function')=='capture_pre_admission_tail'):
            continue
        raise ValueError('Gameplay or an unrecognized failure followed the interrupted restock')
    market=read_json(MarketVisit().path)
    if (market.get('phase')!='active' or market.get('town_visit_id')!=row['town_visit_id']
            or market.get('parent_visit_id')!=row['town_visit_id']
            or market.get('farmer_profile_id')!=visit.profile or market.get('attempts')
            or not market.get('started_at',0)<=failure['time']<=market.get('deadline',0)<time.time()):
        raise ValueError('Original exhausted Market visit is unavailable')
    if deliveries.exists():
        with sqlite3.connect(deliveries.resolve().as_uri()+'?mode=ro',uri=True) as db:
            if (db.execute('SELECT 1 FROM delivery_admissions WHERE created>=? LIMIT 1',(row['required_at'],)).fetchone()
                    or db.execute('SELECT 1 FROM transactions WHERE created>=? LIMIT 1',(row['required_at'],)).fetchone()):
                raise ValueError('A delivery was admitted during this interrupted restock')
    status=request({'action':'status'})
    manual=request({'action':'manual-status'}).get('farmer') or {}
    from conquest.merchants.handoff import qualified_listing_request
    pending=status.get('handoff_requested')
    if (status.get('input_owner') is not None
            or pending is not None and qualified_listing_request(status) is None
            or status.get('handoff_granted') is not False or manual.get('session')
            or manual.get('input_fenced')):
        raise ValueError('Merchant input or manual ownership still holds the interrupted trip')
    bag=loop.town('supplies')
    if _ownership(bag)!=_ownership(meteor['after']):
        raise ValueError('Post-exchange ownership changed before recovery')
    row['pre_admission_restock_tail']={'target':target,'failure':failure,'market':market,
        'meteor':meteor,'bag':bag,'captured_at':time.time(),'phase':'captured'}
    _save_tail(visit,row)
    return True


def resume_pre_admission_tail(loop):
    """Finish only the unreturned cash/panel tail, never replay purchases."""
    from conquest import banking,meteor_banking
    from conquest.merchants.service_visit import MarketVisit
    from conquest.merchants.handoff import service_window
    from conquest.town_trade import stash_candidate
    from conquest.overnight import needs_town,supply_counts
    from conquest.town_visit import checkpoint_verified_tail
    visit=loop.town_visit;row=visit.state();claim=row.get('pre_admission_restock_tail')
    if not claim or row.get('town_work_completed_at'):return False
    if claim.get('phase')!='captured':
        raise ValueError('Interrupted restock cash tail was attempted; reconcile before any replay')
    if row.get('phase')!='town_work' or row.get('reasons')!=['restock']:
        raise ValueError('Interrupted restock visit changed')
    _native_tail_safe(loop,claim['target'],loop.route.restock_map_id)
    meteor=read_json(meteor_banking.JOURNAL);original=claim['meteor']
    market=read_json(MarketVisit().path)
    if (meteor.get('phase')!='completed' or meteor.get('exchange_verified') is not True
            or any(meteor.get(k)!=original.get(k) for k in ('started_at','scroll_uid','meteor_uids','origin','after'))
            or not original['started_at']<=meteor.get('market_verified_at',0)<=meteor.get('completed_at',0)
            or any(market.get(k)!=claim['market'].get(k) for k in
                   ('visit_id','town_visit_id','farmer_profile_id','started_at','deadline'))
            or market.get('phase')!='departed'):
        raise ValueError('Existing Meteor return or original Market budget is unverified')
    receipts=meteor.get('receipts') or [];stored={r.get('stored') for r in receipts}
    original_items={i['uid']:i for i in claim['bag']['items']}
    if (len(stored)!=len(receipts) or original['scroll_uid'] not in stored
            or any(r.get('verified_in_warehouse') is not True
                   or r.get('stored') not in original_items
                   or r.get('type_id')!=original_items[r['stored']]['type_id'] for r in receipts)):
        raise ValueError('Meteor storage receipts are incomplete')
    expected=dict(claim['bag']);expected['items']=[i for i in expected['items'] if i['uid'] not in stored]
    fare=read_json(meteor_banking.POLICY)['origins'][str(meteor['origin'])]['return']['fare']
    expected['silver']-=fare
    loop.adopt_ammunition();bag=loop.town('supplies')
    if (_ownership(bag)!=_ownership(expected) or any(stash_candidate(i) for i in bag['items'])
            or needs_town(supply_counts(bag,loop.route),loop.route)):
        raise ValueError('Returned restock ownership or supplies changed')
    bank=banking.open_warehouse(loop)
    _native_tail_safe(loop,claim['target'],loop.route.restock_map_id)
    if _ownership(loop.town('supplies'))!=_ownership(expected) or bank['silver']!=expected['silver']:
        raise ValueError('Cash tail ownership changed before input')
    claim.update(phase='cash_attempted',cash_before=bank,attempted_at=time.time())
    _save_tail(visit,row)  # Durable before any possible money submission.
    excess=bank['silver']-banking.transport_reserve();receipt=None
    if excess>0:receipt=banking.transfer(loop,'deposit',excess)
    elif excess<0 and bank['stored_silver']:
        receipt=banking.transfer(loop,'withdraw',min(-excess,bank['stored_silver']))
    if (excess>0 or excess<0 and bank['stored_silver']) and (
            not isinstance(receipt,dict) or receipt.get('verified') is not True):
        raise ValueError('Interrupted restock cash transfer lacks a verified receipt')
    claim.update(phase='cash_verified',cash_receipt=receipt,verified_at=time.time())
    _save_tail(visit,row)
    banking.close_warehouse(loop)
    loop.town('close',window='Shop')
    service_window(loop,town=True)
    _native_tail_safe(loop,claim['target'],loop.route.restock_map_id)
    bag=loop.town('supplies')
    if any(stash_candidate(i) for i in bag['items']) or needs_town(supply_counts(bag,loop.route),loop.route):
        raise ValueError('Restock continuation still needs storage or supplies')
    checkpoint_verified_tail(loop,'restock');visit.complete_town_work('restock')
    loop.cycles+=1
    loop.record('restock_complete',supplies=supply_counts(bag,loop.route),
                activity='Finished verified interrupted restock tail; returning to hunt')
    return True


def _rows(path, *, allow_missing=False):
    if allow_missing and not path.exists():
        return
    if not path.is_file():
        raise ValueError('Required restock recovery audit is unavailable')
    with path.open(encoding='utf-8') as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def _ownership(bag):
    if (not isinstance(bag, dict) or not isinstance(bag.get('items'), list)
            or type(bag.get('silver')) is not int or type(bag.get('capacity')) is not int):
        raise ValueError('Fresh ownership observation is incomplete')
    items = sorted((tuple(item.get(field) for field in FIELDS) for item in bag['items']),
                   key=lambda item: item[0])
    if (any(type(item[0]) is not int or item[0] <= 0 for item in items)
            or len({item[0] for item in items}) != len(items)):
        raise ValueError('Fresh item identities are invalid')
    return {'items': items, 'equipped_ammo': bag.get('equipped_ammo'),
            'silver': bag['silver'], 'capacity': bag['capacity']}


def _digest(ownership):
    return hashlib.sha256(json.dumps(ownership, sort_keys=True,
                                  separators=(',', ':')).encode('utf-8')).hexdigest()


def _remaining_after_market_bank(meteor):
    from conquest.meteor_banking import SCROLL
    receipts = meteor.get('receipts')
    after = meteor.get('after')
    if (meteor.get('phase') != 'returning' or meteor.get('origin') != 1011
            or meteor.get('exchange_verified') is not True
            or not isinstance(receipts, list) or not isinstance(after, dict)
            or type(meteor.get('scroll_uid')) is not int
            or type(meteor.get('market_verified_at')) not in (int, float)
            or meteor.get('return_submitted_at')
            or meteor.get('user_confirmed_scroll_consumption')
            or meteor.get('user_confirmed_scroll_transfer')):
        raise ValueError('Meteor return is not a proved pre-submission movement recovery')
    stored = {receipt.get('stored'): receipt for receipt in receipts
              if receipt.get('verified_in_warehouse') is True}
    scroll = meteor['scroll_uid']
    if (stored.get(scroll, {}).get('type_id') != SCROLL
            or len(stored) != len(receipts)
            or any(type(uid) is not int or uid <= 0 for uid in stored)):
        raise ValueError('Market storage lacks exact item receipts')
    expected = dict(after)
    expected['items'] = [item for item in after.get('items', [])
                         if item.get('uid') not in stored]
    if len(after.get('items', [])) - len(expected['items']) != len(stored):
        raise ValueError('Market receipts do not match the pre-storage bag')
    return _ownership(expected)


def _other_holds():
    from conquest.merchants.delivery_journey import pending as journey
    from conquest.merchants.delivery_route import pending as trade
    from conquest.merchants.delivery_operation import pending as delivery
    from conquest.protected_withdrawal import pending as withdrawal
    from conquest.storage_overflow import pending as overflow
    from conquest.storage_halt import active as storage_halt
    return any((journey(), trade(), delivery(), bool(withdrawal()),
                overflow(), storage_halt()))


def claim_interrupted_return(visit, *, info_path, visit_id, original_target, reference):
    """Review the exact existing failure and bind a one-time continuation.

    `original_target` is not inferred from the current game.  The operator
    must supply its independently retained process-creation identity.
    """
    from conquest.meteor_banking import JOURNAL
    from conquest.worker import request
    from conquest.merchants.bridge import request as merchant
    from conquest.discord_notify import process_alive

    row = visit.state()
    route = read_json(state_path('reports/overnight/status.json'))
    health = request(info_path, 'health')
    controls = health.get('embedded_controls') or {}
    life = controls.get('life') or {}
    target = health.get('target')
    merchant_status = merchant({'action': 'status'})
    manual = merchant({'action': 'manual-status'}).get('farmer') or {}
    observation = manual.get('observation') or {}
    if (not isinstance(reference, str) or not reference.strip()
            or row.get('phase') != 'town_work' or row.get('reasons') != ['restock']
            or row.get('town_visit_id') != visit_id
            or route.get('phase') not in ('stopped', 'needs_attention', 'failed')
            or type(route.get('pid')) is not int
            or process_alive(route['pid']) is not False
            or merchant_status.get('input_owner') is not None
            or merchant_status.get('handoff_requested') is not None
            or merchant_status.get('handoff_granted') is not False
            or not _process_identity(original_target) or target != original_target
            or health.get('profile_id') != visit.profile
            or life.get('map_id') != 1036 or life.get('dead_candidate') is not False
            or life.get('current_hp', 0) <= 0
            or controls.get('control', {}).get('enabled') is not False
            or controls.get('external_execution') is not False
            or controls.get('manual_input_fence') or controls.get('manual_mouse')
            or not 0 <= time.time() - controls.get('observed_at', 0) <= 1
            or manual.get('session') or manual.get('input_fenced')
            or observation.get('available') is not True
            or observation.get('windows_absent') is not True
            or not 0 <= time.time() - observation.get('observed_at', 0) <= 5
            or _other_holds()):
        raise ValueError('Interrupted restock is not explicitly qualified')
    meteor = read_json(JOURNAL)
    if (type(meteor.get('started_at')) not in (int, float)
            or meteor['started_at'] < row['required_at']):
        raise ValueError('Meteor journal does not belong to this restock')
    expected = _remaining_after_market_bank(meteor)
    bag = request(info_path, 'town', {'action': 'supplies'})
    if _ownership(bag) != expected:
        raise ValueError('Farmer ownership changed after Market storage')
    failures = []
    post_failure_input = []
    for event in _rows(EVENTS):
        if event.get('town_visit_id') != visit_id:
            continue
        at = event.get('time')
        if type(at) not in (int, float):
            continue
        if (event.get('event') == 'failed' and at >= meteor['market_verified_at']
                and event.get('error_type') == 'conquest.travel_progress.TravelStalled'):
            trace = [(Path(frame.get('file', '')).name, frame.get('function'))
                     for frame in event.get('failure_trace', [])]
            if (('banking.py', 'after_shopping') in trace
                    and ('meteor_banking.py', 'trip') in trace
                    and ('overnight.py', '_travel') in trace):
                failures.append(event)
        if (at > meteor['market_verified_at']
                and event.get('event') in ('purchase', 'sale', 'silver_deposit',
                                           'silver_withdraw', 'valuable_stored',
                                           'merchant_journey_started')):
            post_failure_input.append(event)
    if (len(failures) != 1 or failures[0]['time'] < meteor['market_verified_at']
            or post_failure_input):
        raise ValueError('Return movement failure or later transaction is ambiguous')
    if any(type(row.get('time')) not in (int, float)
           or row['time'] >= meteor['started_at'] for row in _rows(MONEY, allow_missing=True)):
        raise ValueError('Money transfer after Meteor departure needs reconciliation')
    return visit.claim_restock_tail_recovery(visit_id=visit_id, target=target,
        evidence={'reference': reference, 'interrupted_at': failures[0]['time'],
                  'meteor_started_at': meteor['started_at'],
                  'market_verified_at': meteor['market_verified_at'],
                  'scroll_uid': meteor['scroll_uid'],
                  'post_storage_ownership_digest': _digest(expected)})


def require_claimed_identity_before_meteor(loop):
    """Do not move a replacement client before the claimed return is checked."""
    row = loop.town_visit.state()
    if not row.get('restock_recovery_claim') or row.get('town_work_completed_at'):
        return
    health = loop.health()
    target = health.get('target')
    life = (health.get('embedded_controls') or {}).get('life') or {}
    if (row.get('phase') != 'town_work' or row.get('reasons') != ['restock']
            or not _process_identity(target)
            or target != row.get('restock_recovery_target')
            or target != loop.identity
            or life.get('map_id') != 1036
            or life.get('dead_candidate') is not False
            or life.get('current_hp', 0) <= 0):
        raise ValueError('Claimed Meteor return process or location changed; no travel input issued')


def resume_claimed(loop):
    """After native Meteor return, run only the skipped banking/restock tail."""
    from conquest.banking import after_shopping, close_warehouse, open_warehouse
    from conquest.merchants.farmer_preferences import enabled as delivery_enabled
    from conquest.merchants.handoff import service_window
    from conquest.meteor_banking import JOURNAL, METEOR
    from conquest.overnight import needs_town, supply_counts
    from conquest.town_trade import stash_candidate

    visit = loop.town_visit
    row = visit.state()
    claim = row.get('restock_recovery_claim')
    if not claim or row.get('phase') != 'town_work' or row.get('reasons') != ['restock']:
        return False
    if row.get('town_work_completed_at'):
        if not row.get('restock_recovery_attempted_at'):
            raise ValueError('Completed restock recovery lacks its one-shot marker')
        return False
    if row.get('restock_recovery_attempted_at'):
        raise ValueError('Restock recovery tail was attempted; reconcile before any retry')
    health = loop.health()
    controls = health.get('embedded_controls') or {}
    life = controls.get('life') or {}
    target = health.get('target')
    meteor = read_json(JOURNAL)
    if (target != row.get('restock_recovery_target') or target != loop.identity
            or not _process_identity(target) or life.get('map_id') != 1011
            or life.get('dead_candidate') is not False or life.get('current_hp', 0) <= 0
            or meteor.get('phase') != 'completed'
            or meteor.get('scroll_uid') != claim.get('scroll_uid')
            or meteor.get('market_verified_at') != claim.get('market_verified_at')
            or not meteor.get('completed_at') or _other_holds()
            or delivery_enabled()):
        raise ValueError('Restock recovery process, Meteor receipt, or input policy changed')
    loop.adopt_ammunition()
    bag = loop.town('supplies')
    if (_digest(_ownership(bag)) != claim.get('post_storage_ownership_digest')
            or any(stash_candidate(item) for item in bag['items'])
            or needs_town(supply_counts(bag, loop.route), loop.route)):
        raise ValueError('Restock supplies or ownership changed; no tail input issued')
    loop.stop_farm()
    open_warehouse(loop)
    stored = loop.town('warehouse-items')
    fresh = loop.town('supplies')
    if (_digest(_ownership(fresh)) != claim['post_storage_ownership_digest']
            or sum(item['type_id'] == METEOR and item.get('amount') == item.get('limit') == 1
                   for item in fresh['items'] + stored['items']) >= 10):
        raise ValueError('Fresh warehouse stock would start another Meteor trip')
    from conquest.capture import CaptureUnavailable
    controls = (loop.health().get('embedded_controls') or {})
    if controls.get('manual_input_fence') or controls.get('manual_mouse'):
        raise CaptureUnavailable('Manual visitor session holds farmer input')
    # Opening the panel is non-transactional.  Consume the claim before the
    # first possible deposit, withdrawal, merchant handoff, or new journal.
    visit.start_restock_tail_once(target=target)
    if not after_shopping(loop):
        raise ValueError('Required restock banking tail is disabled')
    bag = loop.town('supplies')
    if (any(stash_candidate(item) for item in bag['items'])
            or needs_town(supply_counts(bag, loop.route), loop.route)
            or _other_holds()):
        raise ValueError('Restock banking tail did not settle')
    close_warehouse(loop)
    service_window(loop, town=True)
    if (_other_holds()
            or (loop.health().get('embedded_controls') or {}).get('manual_input_fence')):
        raise ValueError('Merchant handoff after restock did not settle')
    visit.complete_town_work('restock')
    loop.record('restock_recovery_complete',
                activity='Verified interrupted restock tail completed; returning to hunt')
    return True
