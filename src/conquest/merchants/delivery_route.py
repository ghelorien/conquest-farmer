"""Merchant delivery stage for required Market storage visits.

Warehouse callers retain ownership of fallback and the verified return trip.
An uncertain submitted trade never falls through to warehouse input.
"""
from conquest.merchants.capacity import available_slots
from conquest.character_context import farmer_name
from conquest.character_context import state_path
from pathlib import Path
import time
import uuid
import ctypes

from conquest.discord_notify import read_json,write_json
from conquest.merchants.bridge import request
from conquest.merchants.delivery import plan_deliveries
from conquest.merchants.handoff import WorkWindows
from conquest.merchants.farmer_preferences import enabled as transfers_enabled
from conquest.merchants.farmer_identity import route_character

POLICY=Path('profiles/merchant-deliveries.json')
STATE=Path(state_path('reports/banking/merchant-route.json'))


def pending():return bool(read_json(STATE).get('active'))


def check_stop(loop):
    loop.check_stop()
    if ctypes.windll.user32.GetAsyncKeyState(0x7a)&0x8000:
        from conquest.overnight import OvernightStopped
        raise OvernightStopped('Merchant delivery paused with F11')


def receipt_for(uid,type_id):
    return next((r for r in read_json(STATE).get('receipts',[])
        if any(i['uid']==uid and i['type_id']==type_id for i in r['items'])),None)


def warehouse_exhausted(loop,stored,remaining,*,send=request):
    """A full bank is terminal unless all loot is safe and another store is ready.

    Delivery was already attempted before warehouse fallback. Carried leftovers
    cannot be excused by generic merchant space (notably storage-only DBs).
    This read-only check does not authorize a transfer or turn on rollout.
    """
    if len(stored['items'])<stored['capacity']:return False
    if remaining:return True
    policy=read_json(POLICY)
    if not policy.get('enabled') or not policy.get('parity_verified') or not transfers_enabled(route_character(loop)):return True
    from conquest.merchants.delivery import validate_snapshot
    from conquest.merchants.journal import CHARACTERS
    try:
        if not send({'action':'delivery-readiness'}).get('qualified'):return True
        states=send({'action':'status'}).get('characters',{})
        for name in CHARACTERS:
            if not states.get(name,{}).get('ready'):continue
            try:
                pair=send({'action':'delivery-pair','character':name})
                farmer,merchant=pair['farmer'],pair['merchant']
                now=time.time()
                validate_snapshot(farmer,farmer_name(),now)
                inventory=validate_snapshot(merchant,name,now)
                if (not merchant.get('booth_open') or merchant.get('trade') or merchant.get('request')
                        or farmer.get('trade') or farmer.get('request')
                        or available_slots(merchant)<=0):continue
                path=loop.terrain.travel_path(tuple(farmer['position']),tuple(merchant['position']))
                if (path and tuple(path[0])==tuple(farmer['position'])
                        and tuple(path[-1])==tuple(merchant['position'])):
                    return False
            except (ValueError,OSError,KeyError,TypeError):continue
    except (ValueError,OSError,KeyError,TypeError):pass
    return True


def candidates(loop,send,*,excluded=()):
    status=send({'action':'status'})
    states=[];farmer=None
    for name,state in status.get('characters',{}).items():
        if not state.get('ready') or name in excluded:continue
        try:
            pair=send({'action':'delivery-pair','character':name})
            f,m=pair['farmer'],pair['merchant']
            if f['map_id']!=1036 or m['map_id']!=1036:continue
            # The tie-break distance is measured on checked terrain, not an
            # unchecked straight-line distance through Market booths.
            path=loop.terrain.travel_path(tuple(f['position']),tuple(m['position']))
            distance=sum(max(abs(a[0]-b[0]),abs(a[1]-b[1])) for a,b in zip(path,path[1:]))
            states.append({'character':name,'ready':True,'snapshot':m,
                           'verified_travel_distance':distance})
            farmer=f
        except (ValueError,OSError):
            continue
    if farmer is None:return []
    plans=plan_deliveries(farmer,states)['deliveries']
    by_name={s['character']:s['snapshot'] for s in states}
    for plan in plans:plan['position']=by_name[plan['merchant']]['position']
    return plans


def settle(loop,send,state,*,start=False):
    """Only a newly persisted route operation may start native input once."""
    active=state['active'];key=active['request_id']
    command={'action':'delivery-start','request_id':key,'character':active['merchant'],
             'uids':[i['uid'] for i in active['items']],'items':active['items']}
    if start:
        try:send(command)
        except (ValueError,OSError):
            # A lost acknowledgement or a definitive admission rejection is
            # settled from its durable receipt; never repeat the start call.
            pass
    else:
        result=send({'action':'delivery-status','request_id':key})
        if result.get('receipt') is None:
            raise ValueError('Delivery submission has no receipt; reconcile before any storage input')
        if not result.get('running'):
            send({'action':'delivery-reconcile','request_id':key})
    until=time.monotonic()+30
    attempted_reconcile=not start
    cleanup_attempted=False
    while True:
        check_stop(loop)
        try:result=send({'action':'delivery-status','request_id':key})
        except (ValueError,OSError):
            if time.monotonic()>=until:raise
            time.sleep(.1);continue
        receipt=result.get('receipt')
        if not result.get('running'):
            if (not receipt or receipt.get('request_id')!=key or receipt.get('character')!=active['merchant']
                    or sorted(receipt.get('uids',[]))!=sorted(command['uids'])):
                raise ValueError('Merchant delivery needs reconciliation; valuables remain protected')
            from conquest.merchants.delivery import exact_items
            if exact_items(receipt.get('items',[]))!=exact_items(active['items']):
                raise ValueError('Merchant delivery item evidence changed; reconciliation required')
            if any(active.get(field) is not None and receipt.get(field)!=active[field]
                   for field in ('visit_id','town_visit_id','farmer_profile_id')):
                raise ValueError('Merchant delivery visit provenance changed; reconciliation required')
            action=receipt.get('next_action')
            if action=='cleanup_trade_modal' and not cleanup_attempted:
                cleanup_attempted=True
                send({'action':'delivery-cleanup','request_id':key})
                continue
            if action in ('finalize_receiver_receipt','reconcile_bilateral_ownership') and not attempted_reconcile:
                attempted_reconcile=True
                send({'action':'delivery-reconcile','request_id':key})
                continue
            if (receipt.get('phase') not in ('verified','aborted') or receipt.get('cleanup_pending')
                    or action not in ('release_route','retry_delivery','replan_remaining_delivery')):
                raise ValueError('Merchant delivery needs reconciliation; valuables remain protected')
            outcome=receipt.get('outcome')
            if outcome not in ('transferred','no_transfer','retryable_before_input','deferred'):
                raise ValueError('Merchant delivery has no authoritative disposition')
            delivered=receipt.get('delivered') or []
            if outcome=='transferred' and exact_items(delivered)!=exact_items(active['items']):
                raise ValueError('Transferred batch lacks exact ownership evidence')
            record={**active,'items':delivered,'remaining':receipt.get('remaining') or [],
                    'outcome':outcome,'proof_digest':receipt.get('proof_digest'),
                    'next_action':action,'verified_at':time.time()}
            state.setdefault('operations',[]).append(record)
            if delivered:state.setdefault('receipts',[]).append(record)
            state['active']=None
            write_json(STATE,state)
            loop.record('merchant_delivery_verified' if delivered else 'merchant_delivery_deferred',
                        request_id=key,merchant=active['merchant'],items=delivered,outcome=outcome,
                        activity='Valuables delivered and verified in both inventories' if delivered else
                                 'Trade made no transfer; selecting another safe destination')
            return record
        if time.monotonic()>=until:
            raise ValueError('Merchant delivery still running; reconcile before resuming storage')
        time.sleep(.1)


def refill_remainder(loop,send,key,deadline,proof,revision):
    """Use only time remaining in this grant; never create a second budget."""
    if time.time()>=deadline:return False
    from conquest.merchants.handoff import resumable
    from conquest.safe_reload import clear_observation
    check_stop(loop)
    health=loop.health()
    if not resumable(health,proof,revision) or not clear_observation(health):return False
    result=send({'action':'delivery-refill','request_id':key})
    if not result.get('refill'):return False
    if result.get('expires_at')!=deadline:
        raise ValueError('Merchant refill cannot extend the delivery work budget')
    while time.time()<deadline:
        check_stop(loop)
        health=loop.health()
        if not resumable(health,proof,revision) or not clear_observation(health):break
        states=send({'action':'status'}).get('characters',{})
        if not any(s.get('refill',{}).get('enabled') and s['refill'].get('pending') for s in states.values()):break
        time.sleep(min(.2,max(0,deadline-time.time())))
    return True


def approach_merchant(loop,plan,send,*,deadline=None):
    """World distance ranks candidates; the driver shares the arrival proof."""
    from conquest.merchants.approach import ingress_position,positions
    from conquest.travel_progress import TravelStalled
    used=[]
    correction_deadline=time.time()+15
    if deadline is not None:correction_deadline=min(correction_deadline,deadline)
    for attempt in range(4):
        check_stop(loop)
        if time.time()>=correction_deadline:return False
        probe=send({'action':'delivery-target','character':plan['merchant']})
        if time.time()>=correction_deadline:return False
        if probe.get('merchant_position')!=plan['position']:
            return False
        if probe.get('ready'):return True
        if attempt==3:return False
        if probe.get('reason')=='recipient_absent':
            ingress=ingress_position(loop.terrain,probe,used=used,deadline=correction_deadline)
            candidates=[ingress] if ingress is not None else []
        else:
            candidates=positions(loop.terrain,probe,used=used,deadline=correction_deadline)
        if not candidates:return False
        target=candidates[0];used.append(target)
        loop.record('merchant_repositioning',merchant=plan['merchant'],attempt=attempt+1,
                    reason=probe.get('reason'),destination=target,
                    activity=f"Repositioning for a visible trade target: {plan['merchant']}")
        previous=getattr(loop,'market_service_deadline',None)
        loop.market_service_deadline=(min(previous,correction_deadline)
                                      if isinstance(previous,(int,float)) else correction_deadline)
        try:loop.travel(target,arrival_radius=0,activity=f"Approaching verified trade view of {plan['merchant']}")
        except TravelStalled:
            loop.record('merchant_approach_deferred',merchant=plan['merchant'],
                        activity='Merchant approach stalled; selecting another safe destination')
            return False
        finally:loop.market_service_deadline=previous
    return False


def market_storage(loop,*,send=request):
    previous=getattr(loop,'market_service_deadline',None)
    try:return _market_storage(loop,send=send)
    finally:loop.market_service_deadline=previous


def _market_storage(loop,*,send=request):
    state=read_json(STATE)
    if state.get('cleanup_pending'):
        raise ValueError('Reconciled partial delivery still needs its empty trade window closed')
    # Reconcile an earlier submission even after the rollout is disabled.
    # Disabling policy never authorizes abandoning an in-flight transaction.
    if state.get('active'):settle(loop,send,state)
    policy=read_json(POLICY)
    if not policy.get('enabled') or not policy.get('parity_verified') or not transfers_enabled(route_character(loop)):return []
    if loop.living()['embedded_controls']['life']['map_id']!=1036:return []
    try:
        if not send({'action':'delivery-readiness'}).get('qualified'):return []
        plans=candidates(loop,send)
    except (ValueError,OSError):return []
    receipts=[]
    if not plans:return receipts
    from conquest.merchants.service_visit import MarketVisit,parent_visit
    visit=MarketVisit().begin(parent=parent_visit())
    deadline=visit['deadline'];deferred_merchants=set()
    loop.market_service_deadline=deadline
    # Inventory is bounded to forty slots. Re-plan after every receipt so
    # listings, capacity changes and split deliveries cannot reuse stale plans.
    for _ in range(40):
        if not transfers_enabled(route_character(loop)):return receipts
        if time.time()>=deadline:
            loop.record('merchant_service_deferred',visit_id=visit['visit_id'],
                        activity='Market service budget used; storing remaining valuables safely')
            return receipts
        plans=[p for p in plans if p['merchant'] not in deferred_merchants]
        if not plans:return receipts
        plan=plans[0]
        check_stop(loop)
        for window in ('Dialog','Inventory'):
            try:loop.town('service-close-panel',window=window)
            except ValueError as error:
                if not any(note in str(error) for note in ('not active','absent')):raise
        if not approach_merchant(loop,plan,send,deadline=deadline):
            MarketVisit().attempt(plan['merchant'],plan['position'],'deferred_before_input')
            deferred_merchants.add(plan['merchant'])
            plans=candidates(loop,send,excluded=deferred_merchants)
            continue
        fresh=candidates(loop,send,excluded=deferred_merchants)
        if not fresh:return receipts
        current=fresh[0]
        if (current['merchant']!=plan['merchant'] or current['position']!=plan['position']
                or current['merchant_identity']!=plan['merchant_identity']):
            plans=fresh
            continue
        if deadline-time.time()<5+3*len(current['items']):
            loop.record('merchant_service_deferred',visit_id=visit['visit_id'],
                        activity='Insufficient time for another verified trade; using safe storage')
            return receipts
        from conquest.safe_reload import clear_observation
        health=loop.health();control=health['embedded_controls']['control']
        if control['enabled'] or control.get('paused') or not clear_observation(health):
            raise ValueError('Merchant delivery requires a memory-verified safe stopped farmer')
        key='route-delivery:'+uuid.uuid4().hex
        windows=WorkWindows()
        if not windows.reserve(key,town=True,visit=visit):return receipts
        requested=False
        try:
            # Record ownership before issuing the request so an uncertain
            # response still revokes the possible window in finally.
            requested=True
            send({'action':'delivery-window','request_id':key})
            deadline=windows.started()
            send({'action':'handoff-grant','request_id':key,'revision':control['revision'],
                  'expires_at':deadline,'safe':True,'scope':'market_visit','visit_id':visit['visit_id']})
            # Persist before the first bridge submission, including uncertain
            # HTTP results. Restart recovery never blindly resubmits input.
            state['active']={'request_id':key,'merchant':current['merchant'],
                             'items':current['items'],'started_at':time.time(),
                             'visit_id':visit['visit_id'],'farmer_profile_id':visit['farmer_profile_id']}
            state['active']['town_visit_id']=visit.get('town_visit_id')
            write_json(STATE,state)
            result=settle(loop,send,state,start=True)
            MarketVisit().attempt(current['merchant'],current['position'],result['outcome'])
            if result['items']:receipts.append(result)
            if result['outcome']!='transferred':deferred_merchants.add(current['merchant'])
            # Transfer the remaining batch before consuming the visit on listings.
            remaining=candidates(loop,send,excluded=deferred_merchants)
            if not remaining and result['outcome']=='transferred':
                refill_remainder(loop,send,key,deadline,{'target':health.get('target')},control['revision'])
            windows.finish('completed')
        finally:
            if requested:
                until=time.monotonic()+12
                while not send({'action':'handoff-release','request_id':key}).get('released'):
                    if time.monotonic()>=until:
                        raise ValueError('Merchant input has not released; farmer remains stopped')
                    time.sleep(.1)
            check_stop(loop)
        from conquest.merchants.handoff import resumable
        after=loop.health()
        if (not resumable(after,{'target':health.get('target')},control['revision'])
                or after['embedded_controls'].get('manual_mouse')):
            from conquest.overnight import OvernightStopped
            raise OvernightStopped('Manual control changed during merchant delivery')
        loop.focus(after)
        plans=candidates(loop,send,excluded=deferred_merchants)
    raise ValueError('Merchant capacity keeps changing; defer further delivery input')
