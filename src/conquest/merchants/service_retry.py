"""One durable, pre-admission recovery for an exact acceptance delivery.

Ordinary Market visits and any visit that admitted work retain their deadline.
The bridge owns this check; a route cannot provide its own ownership evidence.
"""
from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import time
import uuid

from conquest.discord_notify import read_json, write_json
from conquest.character_context import state_path
from conquest.merchants import service_visit

ROUTE_STATUS=Path(state_path('reports/overnight/status.json'))
ROUTE_STOP=Path(state_path('.runtime/overnight.stop'))


def route_retry(loop, visit, send):
    if time.time() < visit['deadline'] or visit.get('retry_of'):
        return visit
    from conquest import merchant_loop_acceptance as acceptance
    row=acceptance.state();cycle=row.get('active') or {}
    if not acceptance.trial_permitted(loop) or cycle.get('phase')!='town' or cycle.get('admissions'):
        return visit
    loop.check_stop()
    loop.record('merchant_service_retry_check',activity='Checking the unsubmitted acceptance delivery before retry')
    result=send({'action':'delivery-service-retry','visit_id':visit['visit_id'],
                 'run_id':row['run_id'],'cycle_id':cycle['cycle_id'],
                 'route_pid':loop.state['pid'],'route_started_at':loop.state['started_at']})
    fresh=read_json(service_visit.MarketVisit().path)
    if result.get('visit')!=fresh or fresh.get('retry_of')!=visit['visit_id']:
        raise ValueError('Market service retry has no matching durable visit')
    loop.record('merchant_service_retry',visit_id=fresh['visit_id'],previous_visit_id=visit['visit_id'],
                activity='Retrying the exact unsubmitted acceptance delivery with one bounded service window')
    return fresh


def _same_origin(origin, visit):
    return bool(origin.get('visit_id')==visit['visit_id'] or
                visit.get('town_visit_id') and origin.get('town_visit_id')==visit['town_visit_id'])


def _unadmitted_journals(ui, visit):
    from conquest.merchants import delivery_operation, delivery_route, delivery_reservation
    delivery_operation.guard_reload()
    route=read_json(delivery_route.STATE)
    if route.get('active') or route.get('cleanup_pending') or any(_same_origin(r,visit) for r in
            route.get('operations',[])+route.get('receipts',[])):
        raise ValueError('Market service already has delivery input history')
    # Read both admission and transaction histories, including terminal rows.
    # A rejected/settled submission does not earn another service budget.
    if delivery_operation.JOURNAL.exists():
        with closing(sqlite3.connect(delivery_operation.JOURNAL.resolve().as_uri()+'?mode=ro',uri=True)) as db:
            admissions=list(db.execute('SELECT phase,origin_json FROM delivery_admissions'))
            if any(r[0]=='admitted' for r in admissions):
                raise ValueError('Market service has an unresolved source admission')
            origins=[json.loads(r[1]) for r in admissions]
            transactions=list(db.execute("SELECT phase,before_json FROM transactions WHERE kind='farmer_delivery'"))
            if any(r[0] not in ('verified','aborted','operator_overridden') for r in transactions):
                raise ValueError('Market service has an unresolved source transaction')
            origins += [json.loads(r[1]) for r in transactions]
        if any(_same_origin(origin,visit) for origin in origins):
            raise ValueError('Market service already has a source admission')
    with ui.runtime.journal.db() as db:
        reservations=[json.loads(r[0]) for r in db.execute('SELECT state FROM delivery_reservations')]
    if any(r.get('phase') not in delivery_reservation.TERMINAL or _same_origin(r.get('intent',{}),visit)
           for r in reservations):
        raise ValueError('Market service has receiver reservation history')


def _idle(ui, visit, body, row):
    from conquest.discord_notify import process_alive
    route=read_json(ROUTE_STATUS)
    if (ROUTE_STOP.exists() or route.get('phase')!='restocking' or route.get('route')!=row['route_id']
            or route.get('town_visit_id')!=visit['town_visit_id']
            or route.get('pid')!=body['route_pid'] or route.get('started_at')!=body['route_started_at']
            or not 0<=time.time()-route.get('updated_at',0)<=5 or process_alive(route.get('pid')) is not True):
        raise ValueError('Market service retry requires the exact active native town controller')
    control=ui.app.control.snapshot()
    if (control.get('enabled') or control.get('paused') or ui.coordinator.stopped or ui.coordinator.owner
            or ui.coordinator.manual_session_blocked('Farmer') or ui.app.mouse_priority.active()
            or getattr(ui.app,'closing',False) or getattr(ui.app,'thread',None) and ui.app.thread.is_alive()
            or getattr(ui,'grant',None) or getattr(getattr(ui,'grant_fence',None),'active',None)
            or any(getattr(ui.runtime,name,None) for name in ('handoff','delivery_window','refill_window'))
            or getattr(ui,'delivery_admissions',None)
            or any(worker.is_alive() for worker in getattr(ui,'delivery_workers',{}).values())):
        raise ValueError('Market service retry requires stopped, unowned native input')
    from conquest.merchants.handoff import WorkWindows
    window=WorkWindows().state()
    if window.get('visit_id')==visit['visit_id'] or window.get('phase') in ('preparing','working'):
        raise ValueError('Market service already reserved an input window')
    statuses=ui.runtime.status()
    if any(s.get('pending') or s.get('input_active') or s.get('manual_input_fence')
           or (s.get('refill') or {}).get('pending')
           or ui.coordinator.manual_session_blocked(name) for name,s in statuses.items()):
        raise ValueError('Market service retry requires idle merchants')
    return statuses


def dispatch(ui, body):
    if set(body)!={'action','visit_id','run_id','cycle_id','route_pid','route_started_at'} or body['action']!='delivery-service-retry':
        raise ValueError('Unsupported Market service retry')
    if not ui.coordinator.lock.acquire(blocking=False):
        raise ValueError('Market service retry waits for native input release')
    try:
        with ui.runtime.lock:
            return _retry(ui,body)
    finally:
        ui.coordinator.lock.release()


def _retry(ui, body):
    from conquest import merchant_loop_acceptance as acceptance
    from conquest.merchants import delivery_bridge, delivery_journey
    from conquest.merchants.delivery import exact_items
    from conquest.town_visit import TownVisit
    row=acceptance.state();cycle=row.get('active') or {}
    visit_store=service_visit.MarketVisit();visit=read_json(visit_store.path)
    saved=cycle.get('service_retry')
    if (not acceptance.trial_permitted() or cycle.get('phase')!='town' or cycle.get('admissions')
            or row.get('run_id')!=body['run_id'] or cycle.get('cycle_id')!=body['cycle_id']
            or getattr(ui.app.selected_route,'id',None)!=row.get('route_id')
            or visit.get('farmer_profile_id')!=row.get('farmer_profile_id')
            or not cycle.get('town_visit_id')
            or visit.get('town_visit_id')!=cycle.get('town_visit_id')
            or TownVisit().active_id()!=cycle.get('town_visit_id')):
        raise ValueError('Market service retry requires the exact unadmitted acceptance cycle')
    if saved and saved['visit']['retry_of']==body['visit_id'] and visit==saved['visit']:
        # Lost acknowledgement: read the original sealed result, never extend it.
        return {'visit':visit}
    if (visit.get('phase')!='active' or visit.get('visit_id')!=body['visit_id'] or visit.get('retry_of')
            or type(visit.get('deadline')) not in (int,float) or time.time()<visit['deadline']
            or any(a.get('outcome')!='deferred_before_input' for a in visit.get('attempts',[]))
            or saved and saved.get('previous_visit')!=visit):
        raise ValueError('Market service retry requires an expired untouched visit')
    journey=read_json(delivery_journey.JOURNAL)
    if (journey.get('phase')!='market' or journey.get('acceptance_scope')!=acceptance.journey_scope()
            or any(journey.get(name) for name in ('deposit_pending','receipts','scroll_withdrawal',
                'scroll_withdrawal_receipt','scroll_delivery_receipts','loose_meteor_pending'))):
        raise ValueError('Market service retry requires unchanged acceptance journey ownership')
    statuses=_idle(ui,visit,body,row)
    control=ui.app.control.snapshot()
    _unadmitted_journals(ui,visit)
    source=delivery_bridge.dispatch(ui,{'action':'delivery-source'})['farmer']
    inventory=acceptance.source_checked(source,row)
    wanted=exact_items([cycle['item']])
    if source.get('map_id')!=1036 or any(inventory.get(uid)!=value for uid,value in wanted.items()):
        raise ValueError('Market service retry requires the exact carried item in fresh Market memory')
    if not any(acceptance.merchant_allowed(name,status,status.get('snapshot') or {})
               for name,status in statuses.items()):
        raise ValueError('Market service retry requires a currently qualified acceptance merchant')
    if ui.app.control.snapshot()!=control:
        raise ValueError('Farmer control changed while checking the service retry')
    _idle(ui,visit,body,row)
    # Seal the only allowed new deadline before replacing the visit file. A
    # crash between these writes resumes this exact record without more time.
    retry=saved or {'previous_visit':deepcopy(visit),'farmer_identity':source['identity'],
        'item':deepcopy(cycle['item']),'run_id':row['run_id'],'cycle_id':cycle['cycle_id'],
        'controller':{'pid':body['route_pid'],'started_at':body['route_started_at']},
        'visit':{**visit,'visit_id':uuid.uuid4().hex,'started_at':time.time(),
                 'attempts':[],'retry_of':visit['visit_id']}}
    if not saved:retry['visit']['deadline']=retry['visit']['started_at']+service_visit.MARKET_SECONDS
    def seal(current):
        if current!=row:
            raise ValueError('Acceptance changed while sealing its service retry')
        current['active']['service_retry']=deepcopy(retry)
        return current
    acceptance.update('pre_admission_service_retry',seal)
    if read_json(visit_store.path)!=visit:
        raise ValueError('Market visit changed while sealing its service retry')
    write_json(visit_store.path,retry['visit'])
    return {'visit':retry['visit']}
