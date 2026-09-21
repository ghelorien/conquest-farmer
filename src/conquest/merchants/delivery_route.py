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
from conquest.merchants.bridge import MerchantRejected,request
from conquest.merchants.delivery import plan_deliveries
from conquest.merchants.handoff import WorkWindows
from conquest.merchants.farmer_preferences import enabled as transfers_enabled,rollout_enabled
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
    if not rollout_enabled(route_character(loop),policy=policy):return True
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


def candidates(loop,send,*,excluded=(),items=None):
    if items==[]:return []
    status=send({'action':'status'})
    states=[];farmer=None
    for name,state in status.get('characters',{}).items():
        if not state.get('ready') or name in excluded:continue
        try:
            pair=send({'action':'delivery-pair','character':name})
            f,m=pair['farmer'],pair['merchant']
            from conquest.merchant_loop_acceptance import merchant_allowed
            if not merchant_allowed(name,state,m):continue
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
    from conquest.merchant_loop_acceptance import plan_items
    items=plan_items(farmer,items)
    if items==[]:return []
    reserved=()
    if items is not None:
        from conquest.merchants.delivery import exact_items
        wanted=exact_items(items);current=exact_items(farmer['inventory'])
        if any(current.get(uid)!=value for uid,value in wanted.items()):
            raise ValueError('Exact requested delivery items changed before merchant planning')
        reserved=set(current)-set(wanted)
    plans=plan_deliveries(farmer,states,reserved=reserved)['deliveries']
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
            if (receipt.get('phase') not in ('verified','aborted','operator_overridden') or receipt.get('cleanup_pending')
                    or action not in ('release_route','retry_delivery','replan_remaining_delivery')):
                raise ValueError('Merchant delivery needs reconciliation; valuables remain protected')
            outcome=receipt.get('outcome')
            if outcome not in ('transferred','no_transfer','retryable_before_input','deferred','operator_overridden'):
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
            from conquest.merchant_loop_acceptance import settled
            settled(record)
            loop.record('merchant_delivery_verified' if delivered else 'merchant_delivery_deferred',
                        request_id=key,merchant=active['merchant'],items=delivered,outcome=outcome,
                        activity='Valuables delivered and verified in both inventories' if delivered else
                                 'Delivery incident closed; checking the next safe destination')
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
        from conquest.merchant_loop_acceptance import refill_observed
        refill_observed(states)
        if not any(s.get('refill',{}).get('enabled') and s['refill'].get('pending') for s in states.values()):break
        time.sleep(min(.2,max(0,deadline-time.time())))
    return True


def approach_merchant(loop,plan,send,*,deadline=None):
    """World distance ranks candidates; the driver shares the arrival proof."""
    from conquest.merchants.approach import bounded_position,ingress_position,positions,within_delivery_probe_range
    from conquest.travel_progress import TravelStalled
    # ``used`` excludes only final standing candidates.  A bounded visible
    # hop is not itself a standing candidate and must not be turned into a
    # terrain obstacle after it stalls.
    used=[]
    failed_legs=set()
    # Keep the final fifteen seconds of a Market visit for the qualified
    # transaction and release.  Approach legs get their own short deadline so
    # one obsolete target cannot consume that budget.
    correction_deadline=time.time()+40
    if deadline is not None:correction_deadline=min(correction_deadline,deadline-15)
    # Every movement leg is a checked visible jump.  Reobserve after it rather
    # than walking back to a stale intermediate standing tile.
    moves=0
    previous_leg_source=None
    # Scene-control observations are read-only volatility, not movement
    # attempts.  Keep their count finite while limiting actual repositioning.
    for observation in range(48):
        check_stop(loop)
        if time.time()>=correction_deadline:return False
        probe=send({'action':'delivery-target','character':plan['merchant']})
        if time.time()>=correction_deadline:return False
        source=tuple(probe.get('farmer_position',()))
        if len(source)!=2:return False
        if previous_leg_source is not None:
            if source!=previous_leg_source:moves+=1
            previous_leg_source=None
        if probe.get('merchant_position')!=plan['position']:
            return False
        if (probe.get('ready') and within_delivery_probe_range(
                probe.get('farmer_position'), probe.get('merchant_position'))):
            return True
        if moves>=12:return False
        if probe.get('reason')=='recipient_scene_changed':
            # A delivery-target probe is read-only.  Do not route or submit
            # input from a scene that changed during that observation; give a
            # fresh scene a bounded chance to settle instead.
            loop.record('merchant_target_deferred',merchant=plan['merchant'],attempt=observation+1,
                        reason='recipient_scene_changed',
                        activity='Trade target scene changed; retrying the memory observation')
            time.sleep(min(.1,max(0,correction_deadline-time.time())))
            continue
        if probe.get('reason')=='recipient_absent':
            ingress=ingress_position(loop.terrain,probe,used=used,deadline=correction_deadline)
            candidates=[ingress] if ingress is not None else []
        else:
            candidates=positions(loop.terrain,probe,used=used,deadline=correction_deadline)
        if not candidates:return False
        selected=target=None
        # A failed bounded hop used to be appended to ``used``.  The selector
        # only understands final standing candidates, so the same top-ranked
        # candidate could recompute that exact failed first hop forever.  Keep
        # the failure tied to the observed source and try the next freshly
        # qualified endpoint instead; this is deliberately not path avoidance.
        for candidate in candidates:
            if time.time()>=correction_deadline:return False
            possible=bounded_position(loop.terrain,probe,candidate,used=used,
                                      deadline=correction_deadline)
            if possible is None or (source,tuple(possible)) in failed_legs:
                used.append(candidate)
                continue
            selected,target=candidate,tuple(possible)
            break
        if target is None:return False
        short_leg=max(abs(a-b) for a,b in zip(target,source))<=2
        occupied=set(map(tuple,probe.get('occupied_tiles',())))
        occupied.add(tuple(probe['merchant_position']))
        occupied.discard(source)
        loop.record('merchant_repositioning',merchant=plan['merchant'],attempt=observation+1,
                    reason=probe.get('reason'),destination=target,
                    activity=f"Repositioning for a visible trade target: {plan['merchant']}")
        previous=getattr(loop,'market_service_deadline',None)
        leg_deadline=min(correction_deadline,time.time()+4)
        loop.market_service_deadline=(min(previous,leg_deadline)
                                      if isinstance(previous,(int,float)) else leg_deadline)
        try:loop.travel(target,arrival_radius=0 if short_leg else 2,avoid=occupied,
                        activity=f"Approaching verified trade view of {plan['merchant']}")
        except TravelStalled:
            loop.record('merchant_approach_deferred',merchant=plan['merchant'],
                        activity='Merchant approach stalled; reobserving a safe destination')
            failed_legs.add((source,target))
            used.append(selected)
            previous_leg_source=source
            continue
        finally:loop.market_service_deadline=previous
        # Leave the still-unreached final standing tile eligible for the fresh
        # next probe.  It has not failed merely because this bounded hop made
        # verified progress.
        previous_leg_source=source
    return False


def market_storage(loop,*,send=request,items=None,on_admitted=None):
    previous=getattr(loop,'market_service_deadline',None)
    try:return _market_storage(loop,send=send,items=items,on_admitted=on_admitted)
    finally:loop.market_service_deadline=previous


def _market_storage(loop,*,send=request,items=None,on_admitted=None):
    state=read_json(STATE)
    selected=None if items is None else list(items)
    def remaining(record):
        nonlocal selected
        if selected is not None:
            delivered={i['uid'] for i in record.get('items',[])}
            selected=[i for i in selected if i['uid'] not in delivered]
    def plans_for(excluded=()):return candidates(loop,send,excluded=excluded,items=selected)
    if state.get('cleanup_pending'):
        raise ValueError('Reconciled partial delivery still needs its empty trade window closed')
    # Reconcile an earlier submission even after the rollout is disabled.
    # Disabling policy never authorizes abandoning an in-flight transaction.
    if state.get('active'):remaining(settle(loop,send,state))
    policy=read_json(POLICY)
    from conquest.merchant_loop_acceptance import trial_permitted
    if (not rollout_enabled(route_character(loop),policy=policy) and not trial_permitted(loop)
            or not transfers_enabled(route_character(loop))):return []
    if loop.living()['embedded_controls']['life']['map_id']!=1036:return []
    try:
        if not send({'action':'delivery-readiness'}).get('qualified'):return []
        plans=plans_for()
    except (ValueError,OSError):return []
    receipts=[]
    if not plans:return receipts
    from conquest.merchants.service_visit import MarketVisit,parent_visit
    visit=MarketVisit().begin(parent=parent_visit())
    from conquest.merchants.service_retry import route_retry
    visit=route_retry(loop,visit,send)
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
            plans=plans_for(deferred_merchants)
            continue
        fresh=plans_for(deferred_merchants)
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
        release_required=False
        try:
            # Record ownership before issuing the request so an uncertain
            # response still revokes the possible window in finally.
            release_required=True
            try:send({'action':'delivery-window','request_id':key})
            except MerchantRejected:
                # A parsed bridge application rejection proves this action
                # did not publish the key.  Unknown/lost responses remain
                # release-required so they cannot leave input authorized.
                release_required=False
                raise
            deadline=windows.started()
            send({'action':'handoff-grant','request_id':key,'revision':control['revision'],
                  'expires_at':deadline,'safe':True,'scope':'market_visit','visit_id':visit['visit_id']})
            # Persist before the first bridge submission, including uncertain
            # HTTP results. Restart recovery never blindly resubmits input.
            state['active']={'request_id':key,'merchant':current['merchant'],
                             'merchant_identity':current['merchant_identity'],
                             'merchant_uid':current['merchant_uid'],
                             'items':current['items'],'started_at':time.time(),
                             'controller':{'pid':loop.state['pid'],'started_at':loop.state['started_at']},
                             'visit_id':visit['visit_id'],'farmer_profile_id':visit['farmer_profile_id']}
            state['active']['town_visit_id']=visit.get('town_visit_id')
            write_json(STATE,state)
            from conquest.merchant_loop_acceptance import admitted
            admitted(dict(state['active']))
            if on_admitted is not None:on_admitted(dict(state['active']))
            result=settle(loop,send,state,start=True)
            MarketVisit().attempt(current['merchant'],current['position'],result['outcome'])
            if result['items']:receipts.append(result)
            remaining(result)
            if result['outcome']!='transferred':deferred_merchants.add(current['merchant'])
            # Transfer the remaining batch before consuming the visit on listings.
            remaining_plans=plans_for(deferred_merchants)
            if not remaining_plans and result['outcome']=='transferred':
                refill_remainder(loop,send,key,deadline,{'target':health.get('target')},control['revision'])
            windows.finish('completed')
        finally:
            if release_required:
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
        plans=plans_for(deferred_merchants)
    raise ValueError('Merchant capacity keeps changing; defer further delivery input')
