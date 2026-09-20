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
    if time.time() < visit['deadline']:
        return visit
    from conquest import merchant_loop_acceptance as acceptance
    row=acceptance.state();cycle=row.get('active') or {}
    if not acceptance.trial_permitted(loop) or cycle.get('phase')!='town':
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


def _retry_records(cycle):
    history=cycle.get('service_retry_history',[])
    if not isinstance(history,list) or any(not isinstance(record,dict) for record in history):
        raise ValueError('Market service retry history is malformed')
    latest=cycle.get('service_retry')
    return [*history,*([latest] if isinstance(latest,dict) else [])]


def _terminal_retry_records(cycle):
    records=cycle.get('terminal_service_retries',[])
    if not isinstance(records,list) or any(not isinstance(record,dict) for record in records):
        raise ValueError('Terminal Market service retry history is malformed')
    return records


def _deferred_attempts(visit):
    return isinstance(visit,dict) and all(isinstance(attempt,dict)
        and attempt.get('outcome')=='deferred_before_input' for attempt in visit.get('attempts',[]))


def _retry_visit_matches(saved, visit):
    return isinstance(saved,dict) and all(saved.get(key)==visit.get(key)
        for key in ('visit_id','retry_of','started_at','deadline','town_visit_id','farmer_profile_id'))


def _known_visit_ids(cycle, visit):
    result={visit.get('visit_id')}
    for record in _retry_records(cycle):
        for name in ('previous_visit','visit'):
            saved=record.get(name,{})
            if isinstance(saved,dict):result.add(saved.get('visit_id'))
    return result-{None}


def _same_origin(origin, visit, *, visit_ids=()):
    return bool(isinstance(origin,dict) and (origin.get('visit_id') in set(visit_ids)|{visit['visit_id']} or
                visit.get('town_visit_id') and origin.get('town_visit_id')==visit['town_visit_id']))


def _unadmitted_journals(ui, visit, *, visit_ids=()):
    from conquest.merchants import delivery_operation, delivery_route, delivery_reservation
    delivery_operation.guard_reload()
    route=read_json(delivery_route.STATE)
    if route.get('active') or route.get('cleanup_pending') or any(_same_origin(r,visit,visit_ids=visit_ids) for r in
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
        if any(_same_origin(origin,visit,visit_ids=visit_ids) for origin in origins):
            raise ValueError('Market service already has a source admission')
    with ui.runtime.journal.db() as db:
        reservations=[json.loads(r[0]) for r in db.execute('SELECT state FROM delivery_reservations')]
    if any(r.get('phase') not in delivery_reservation.TERMINAL or _same_origin(r.get('intent',{}),visit,visit_ids=visit_ids)
           for r in reservations):
        raise ValueError('Market service has receiver reservation history')


def _idle(ui, visit, body, row, *, visit_ids=(), window_request_id=None):
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
    if (window_request_id is None and (window.get('visit_id') in set(visit_ids)|{visit['visit_id']}
            or window.get('phase') in ('preparing','working'))):
        raise ValueError('Market service already reserved an input window')
    statuses=ui.runtime.status()
    if any(s.get('pending') or s.get('input_active') or s.get('manual_input_fence')
           or (s.get('refill') or {}).get('pending')
           or ui.coordinator.manual_session_blocked(name) for name,s in statuses.items()):
        raise ValueError('Market service retry requires idle merchants')
    return statuses


def _terminal_idle(ui, visit, body, row, request_id):
    """The expired window may remain only as this exact closed receipt."""
    statuses=_idle(ui,visit,body,row,visit_ids=(),window_request_id=request_id)
    from conquest.merchants.handoff import WorkWindows
    window=WorkWindows().state()
    if (window.get('request_id')!=request_id or window.get('visit_id')!=visit['visit_id']
            or window.get('town_visit_id') not in (None,visit.get('town_visit_id'))
            or window.get('deadline')!=visit.get('deadline')
            or (window.get('phase') in ('preparing','working')
                and (not isinstance(window.get('deadline'),(int,float))
                     or time.time()<window['deadline']))
            or (window.get('phase') not in ('preparing','working')
                and not isinstance(window.get('finished_at'),(int,float)))):
        raise ValueError('Terminal delivery retry requires its exact closed Market window')
    return statuses


def _canonical_terminal(ui, visit, cycle):
    """Return the one current no-transfer receipt, rejecting all ambiguity."""
    from conquest.merchants import delivery_operation,delivery_route,delivery_reservation
    from conquest.merchants.delivery import exact_items
    admissions=cycle.get('admissions')
    if not isinstance(admissions,list) or not admissions or any(not isinstance(row,dict) for row in admissions):
        raise ValueError('Terminal delivery retry requires an acceptance admission')
    if len({row.get('request_id') for row in admissions})!=len(admissions):
        raise ValueError('Acceptance delivery admission history is ambiguous')
    route=read_json(delivery_route.STATE)
    if route.get('active') or route.get('cleanup_pending'):
        raise ValueError('Terminal delivery retry requires a settled route operation')
    delivery_operation.guard_reload()
    wanted=exact_items([cycle['item']]);operations=[]
    request_ids={row.get('request_id') for row in admissions}
    route_history=[record for record in route.get('operations',[])+route.get('receipts',[])
                   if isinstance(record,dict) and _same_origin(record,visit)]
    if (any(record.get('request_id') not in request_ids for record in route_history)
            or len({record.get('request_id') for record in route_history})!=len(route_history)):
        raise ValueError('Acceptance route history is ambiguous')
    if not route_history or route_history[-1].get('request_id')!=admissions[-1].get('request_id'):
        raise ValueError('Latest acceptance route operation differs from its admission')
    source_journal=delivery_operation.Journal(delivery_operation.JOURNAL)
    with source_journal.db() as db:
        source_history=[dict(row) for row in db.execute(
            "SELECT id,phase,before_json FROM transactions WHERE kind='farmer_delivery'")]
        admissions_history=[row[0] for row in db.execute('SELECT phase FROM delivery_admissions')]
    if any(phase=='admitted' for phase in admissions_history):
        raise ValueError('A source delivery admission remains unresolved')
    same_source=[row for row in source_history
                 if _same_origin(json.loads(row['before_json']),visit)]
    if (any(row['id'] not in request_ids or row['phase'] not in ('aborted','verified','operator_overridden')
            for row in same_source) or len({row['id'] for row in same_source})!=len(same_source)):
        raise ValueError('Acceptance source history is unresolved or ambiguous')
    with ui.runtime.journal.db() as db:
        receiver_history=[json.loads(row[0]) for row in db.execute('SELECT state FROM delivery_reservations')]
    if any(record.get('phase') not in delivery_reservation.TERMINAL for record in receiver_history):
        raise ValueError('A receiver delivery reservation remains unresolved')
    same_receiver=[record for record in receiver_history
                   if _same_origin(record.get('intent',{}),visit)]
    if (any(record.get('request_id') not in request_ids
            or record.get('phase') not in ('no_transfer_reconciled','partial_aborted_reconciled','verified')
            for record in same_receiver)
            or len({record.get('request_id') for record in same_receiver})!=len(same_receiver)):
        raise ValueError('Acceptance receiver history is unresolved or ambiguous')
    for admission in admissions:
        key=admission.get('request_id')
        if not isinstance(key,str) or not key:
            raise ValueError('Acceptance delivery admission is malformed')
        receipt=delivery_operation.status(source_journal,key)
        operation=next((record for record in route.get('operations',[])
                        if isinstance(record,dict) and record.get('request_id')==key),None)
        if (not receipt or not operation or receipt.get('phase')!='aborted'
                or receipt.get('outcome')!='no_transfer' or receipt.get('next_action')!='retry_delivery'
                or receipt.get('cleanup_pending') or operation.get('outcome')!='no_transfer'
                or operation.get('next_action')!='retry_delivery' or operation.get('items')
                or exact_items(operation.get('remaining',[]))!=wanted
                or exact_items(receipt.get('items',[]))!=wanted
                or exact_items(receipt.get('remaining',[]))!=wanted
                or not isinstance(receipt.get('proof_digest'),str)
                or operation.get('proof_digest')!=receipt['proof_digest']
                or any(receipt.get(name)!=admission.get(name) for name in
                       ('visit_id','town_visit_id','farmer_profile_id'))):
            raise ValueError('Acceptance delivery lacks a canonical no-transfer receipt')
        with ui.runtime.journal.db() as db:
            row=db.execute('SELECT state FROM delivery_reservations WHERE character=? AND request_id=?',
                           (receipt['character'],key)).fetchone()
        receiver=json.loads(row[0]) if row else {}
        if (receiver.get('phase')!='no_transfer_reconciled'
                or (receiver.get('disposition') or {}).get('outcome')!='no_transfer'
                or (receiver.get('disposition') or {}).get('proof_digest')!=receipt['proof_digest']):
            raise ValueError('Receiver no-transfer receipt differs from the source operation')
        operations.append((admission,receipt,operation))
    admission,receipt,operation=operations[-1]
    if admission.get('merchant')!=receipt.get('character'):
        raise ValueError('Latest acceptance admission differs from its source operation')
    return admission,receipt,operation


def _terminal_retry(ui, body, row, cycle, visit, visit_store):
    from conquest import merchant_loop_acceptance as acceptance
    from conquest.merchants import delivery_bridge,delivery_journey
    from conquest.merchants.delivery import exact_items
    admission,receipt,operation=_canonical_terminal(ui,visit,cycle)
    controller={'pid':body['route_pid'],'started_at':body['route_started_at']}
    records=[*_retry_records(cycle),*_terminal_retry_records(cycle)]
    provenance=[record for record in records
                if record.get('visit',{}).get('visit_id')==admission.get('visit_id')]
    admission_controller=admission.get('controller');operation_controller=operation.get('controller')
    if admission_controller!=operation_controller:
        raise ValueError('Latest failed admission controller differs from its route operation')
    if admission_controller is not None:
        if not isinstance(admission_controller,dict):
            raise ValueError('Latest failed admission controller is malformed')
        if (provenance and (len(provenance)!=1
                or not isinstance(provenance[0].get('controller'),dict)
                or provenance[0]['controller']!=admission_controller)):
            raise ValueError('Latest failed admission retry provenance differs from its route controller')
        prior_controller=admission_controller
    else:
        if len(provenance)!=1 or not isinstance(provenance[0].get('controller'),dict):
            raise ValueError('Legacy failed admission lacks a sealed retry controller')
        prior_controller=provenance[0]['controller']
    if prior_controller==controller:
        raise ValueError('Terminal delivery retry requires a fresh native town controller')
    previous=next((record for record in _terminal_retry_records(cycle)
                  if record.get('controller')==controller
                  and record.get('previous_visit',{}).get('visit_id')==body['visit_id']),None)
    if previous:
        if visit==previous.get('visit'):
            return {'visit':visit}
        if visit==previous.get('previous_visit'):
            write_json(visit_store.path,previous['visit'])
            return {'visit':previous['visit']}
        raise ValueError('Terminal service retry acknowledgement conflicts with the sealed visit')
    if any(record.get('controller')==controller for record in records):
        raise ValueError('Market service retry requires a fresh native town controller')
    if any(record.get('source_request_id')==receipt['request_id'] for record in _terminal_retry_records(cycle)):
        raise ValueError('This canonical no-transfer operation already used its retry budget')
    if (visit.get('phase')!='active' or visit.get('visit_id')!=body['visit_id']
            or type(visit.get('deadline')) not in (int,float) or time.time()<visit['deadline']):
        raise ValueError('Terminal delivery retry requires its exact expired Market visit')
    journey=read_json(delivery_journey.JOURNAL)
    if (journey.get('phase')!='market' or journey.get('acceptance_scope')!=acceptance.journey_scope()
            or any(journey.get(name) for name in ('deposit_pending','receipts','scroll_withdrawal',
                'scroll_withdrawal_receipt','scroll_delivery_receipts','loose_meteor_pending'))):
        raise ValueError('Terminal delivery retry requires unchanged acceptance journey ownership')
    statuses=_terminal_idle(ui,visit,body,row,receipt['request_id'])
    control=ui.app.control.snapshot()
    source=delivery_bridge.dispatch(ui,{'action':'delivery-source'})['farmer']
    inventory=acceptance.source_checked(source,row);wanted=exact_items([cycle['item']])
    if source.get('map_id')!=1036 or any(inventory.get(uid)!=value for uid,value in wanted.items()):
        raise ValueError('Terminal delivery retry requires the exact carried item in fresh Market memory')
    merchant=statuses.get(admission['merchant'],{});snapshot=merchant.get('snapshot') or {}
    if (not acceptance.merchant_allowed(admission['merchant'],merchant,snapshot)
            or snapshot.get('identity')!=admission.get('merchant_identity')
            or snapshot.get('character_uid')!=admission.get('merchant_uid')
            or snapshot.get('trade') or snapshot.get('request')):
        raise ValueError('Terminal delivery retry requires an idle original acceptance merchant')
    if ui.app.control.snapshot()!=control:
        raise ValueError('Farmer control changed while checking the terminal service retry')
    from conquest.merchants.handoff import WorkWindows
    windows=WorkWindows();windows.finish('terminal_no_transfer_reconciled')
    closed=windows.state()
    if (closed.get('request_id')!=receipt['request_id'] or closed.get('visit_id')!=visit['visit_id']
            or closed.get('phase')!='terminal_no_transfer_reconciled'
            or not isinstance(closed.get('finished_at'),(int,float))):
        raise ValueError('Terminal delivery window did not close durably')
    retry={'terminal_retry':True,'source_request_id':receipt['request_id'],
           'source_proof_digest':receipt['proof_digest'],'previous_visit':deepcopy(visit),
           'controller':controller,'run_id':row['run_id'],'cycle_id':cycle['cycle_id'],
           'item':deepcopy(cycle['item'])}
    retry['visit']={**visit,'visit_id':uuid.uuid4().hex,'started_at':time.time(),
                    'attempts':[],'retry_of':visit['visit_id'],
                    'retry_of_operation_id':receipt['request_id'],
                    'retry_of_proof_digest':receipt['proof_digest']}
    retry['visit']['deadline']=retry['visit']['started_at']+service_visit.MARKET_SECONDS
    def seal(current):
        if current!=row:
            raise ValueError('Acceptance changed while sealing its terminal service retry')
        current['active']['terminal_service_retries']=[*_terminal_retry_records(cycle),deepcopy(retry)]
        return current
    acceptance.update('terminal_no_transfer_service_retry',seal)
    if read_json(visit_store.path)!=visit:
        raise ValueError('Market visit changed while sealing its terminal service retry')
    write_json(visit_store.path,retry['visit'])
    return {'visit':retry['visit']}


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
    if (not acceptance.trial_permitted() or cycle.get('phase')!='town'
            or row.get('run_id')!=body['run_id'] or cycle.get('cycle_id')!=body['cycle_id']
            or getattr(ui.app.selected_route,'id',None)!=row.get('route_id')
            or visit.get('farmer_profile_id')!=row.get('farmer_profile_id')
            or not cycle.get('town_visit_id')
            or visit.get('town_visit_id')!=cycle.get('town_visit_id')
            or TownVisit().active_id()!=cycle.get('town_visit_id')):
        raise ValueError('Market service retry requires the exact unadmitted acceptance cycle')
    if cycle.get('admissions'):
        return _terminal_retry(ui,body,row,cycle,visit,visit_store)
    if saved and saved.get('previous_visit',{}).get('visit_id')==body['visit_id']:
        # The SQLite seal precedes the visit-file write.  A lost bridge reply
        # reads the same sealed deadline; a crash before the file write only
        # completes that write and never creates another budget.
        if visit==saved.get('visit'):
            return {'visit':visit}
        if visit==saved.get('previous_visit'):
            write_json(visit_store.path,saved['visit'])
            return {'visit':saved['visit']}
        raise ValueError('Market service retry acknowledgement conflicts with the sealed visit')
    history=cycle.get('service_retry_history',[])
    records=_retry_records(cycle)
    if (visit.get('phase')!='active' or visit.get('visit_id')!=body['visit_id']
            or type(visit.get('deadline')) not in (int,float) or time.time()<visit['deadline']
            or any(a.get('outcome')!='deferred_before_input' for a in visit.get('attempts',[]))
            or saved and not _retry_visit_matches(saved.get('visit'),visit)
            or any(not _deferred_attempts(record.get('previous_visit'))
                   or not _deferred_attempts(record.get('visit')) for record in records)):
        raise ValueError('Market service retry requires an expired untouched visit')
    controller={'pid':body['route_pid'],'started_at':body['route_started_at']}
    if any(record.get('controller')==controller for record in records):
        raise ValueError('Market service retry requires a fresh native town controller')
    visit_ids=_known_visit_ids(cycle,visit)
    journey=read_json(delivery_journey.JOURNAL)
    if (journey.get('phase')!='market' or journey.get('acceptance_scope')!=acceptance.journey_scope()
            or any(journey.get(name) for name in ('deposit_pending','receipts','scroll_withdrawal',
                'scroll_withdrawal_receipt','scroll_delivery_receipts','loose_meteor_pending'))):
        raise ValueError('Market service retry requires unchanged acceptance journey ownership')
    statuses=_idle(ui,visit,body,row,visit_ids=visit_ids)
    control=ui.app.control.snapshot()
    _unadmitted_journals(ui,visit,visit_ids=visit_ids)
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
    _idle(ui,visit,body,row,visit_ids=visit_ids)
    # Seal the only allowed new deadline before replacing the visit file. A
    # crash between these writes resumes this exact record without more time.
    retry={'previous_visit':deepcopy(visit),'farmer_identity':source['identity'],
        'item':deepcopy(cycle['item']),'run_id':row['run_id'],'cycle_id':cycle['cycle_id'],
        'controller':controller,
        'visit':{**visit,'visit_id':uuid.uuid4().hex,'started_at':time.time(),
                 'attempts':[],'retry_of':visit['visit_id']}}
    retry['visit']['deadline']=retry['visit']['started_at']+service_visit.MARKET_SECONDS
    archived=deepcopy(history)
    if saved:
        previous=deepcopy(saved);previous['visit']=deepcopy(visit)
        archived.append(previous)
    def seal(current):
        if current!=row:
            raise ValueError('Acceptance changed while sealing its service retry')
        current['active']['service_retry_history']=archived
        current['active']['service_retry']=deepcopy(retry)
        return current
    acceptance.update('pre_admission_service_retry',seal)
    if read_json(visit_store.path)!=visit:
        raise ValueError('Market visit changed while sealing its service retry')
    write_json(visit_store.path,retry['visit'])
    return {'visit':retry['visit']}
