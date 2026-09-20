"""Required-town delivery trip with verified fares and warehouse fallback."""
from conquest.merchants.capacity import available_slots
from conquest.character_context import farmer_name
from conquest.character_context import installation_path, state_path
from pathlib import Path
import time
import uuid

from conquest.discord_notify import read_json,write_json
from conquest.merchants.bridge import request
from conquest.merchants.delivery import eligible,validate_snapshot
from conquest.merchants.journal import CHARACTERS
from conquest.merchants import delivery_route

JOURNAL=Path(state_path('reports/banking/merchant-journey.json'))


def pending():
    from conquest.recovery_override import read_recovered
    return read_recovered(JOURNAL).get('phase') in ('prepared','outbound_pending','market','return_pending')


def matching_scroll_recovery(locks,*,path=None):
    """Only the journey's single exact scroll hold may launch a recovery read."""
    if len(locks)!=1:return False
    state=read_json(path or JOURNAL);wanted=state.get('scroll_withdrawal') or {}
    row=locks[0];intent=row.get('intent') or {};anchor=intent.get('deposit_receipt') or {}
    from conquest.scroll_withdrawal import ScrollWithdrawal
    from conquest.character_context import current
    context=current()
    try:
        return (state.get('phase')=='market' and not state.get('scroll_preparation_done')
            and wanted.get('operation_id')==row.get('operation_id') and wanted.get('uid')==row.get('uid')
            and row.get('plan_id')==ScrollWithdrawal.plan_id(wanted['operation_id'])
            and intent.get('item',{}).get('uid')==wanted['uid']
            and intent.get('item',{}).get('type_id')==720027
            and anchor.get('kind')=='fresh_stored_meteor_scroll'
            and bool(anchor.get('farmer_profile_id'))
            and (context is None or anchor['farmer_profile_id']==context.profile.id))
    except (KeyError,TypeError,ValueError):return False


def settle_scroll_admission(state,result):
    old=state['scroll_withdrawal'];receipt=result.get('receipt') or {}
    if result.get('operation_id')!=old['operation_id'] or result.get('uid')!=old['uid']:
        raise ValueError('MeteorScroll withdrawal receipt belongs to another operation')
    if result.get('phase')=='no_transfer' and result.get('no_input_proven') is True:
        history=state.setdefault('scroll_admissions',[])
        history.append({'operation':old,'receipt':receipt,'no_input_proven':True})
        save(state,scroll_withdrawal=None)
        return False
    if (result.get('phase')!='withdrawn' or receipt.get('item',{}).get('uid')!=old['uid']
            or receipt.get('item',{}).get('type_id')!=720027
            or receipt.get('verified_in_inventory') is not True
            or receipt.get('verified_absent_from_warehouse') is not True):
        raise ValueError('MeteorScroll withdrawal requires read-only reconciliation before merchant input')
    save(state,scroll_preparation_done=True,scroll_withdrawal_receipt=receipt,scroll_warehouse_closed=False)
    return True


def reconcile_pending_scroll(loop):
    """The route startup exception performs only this authenticated memory read."""
    from conquest.protected_withdrawal import pending as withdrawal_pending
    locks=withdrawal_pending()
    if not matching_scroll_recovery(locks):return False
    state=read_json(JOURNAL);old=state['scroll_withdrawal']
    result=loop.town('warehouse-reconcile-scroll',**old)
    settle_scroll_admission(state,result)
    return True


def recheck(loop):
    return {'observed_at':time.time(), 'life':loop.living()['embedded_controls']['life'],
            'supplies':loop.town('supplies')}


def operator_override(loop, *, operator_confirmed=False, confirmation_reference=None, operator=None):
    from conquest.recovery_override import operator_override as close
    try:fresh=recheck(loop)
    except (ValueError,OSError,KeyError,TypeError) as error:
        fresh={'recheck_unavailable':type(error).__name__,
               'reason':'Fresh farmer memory unavailable; resume requires a fresh replan'}
    return close(JOURNAL,pending_phases=('prepared','outbound_pending','market','return_pending'),
                 operator_confirmed=operator_confirmed,confirmation_reference=confirmation_reference,
                 operator=operator,fresh_evidence=fresh,incident='merchant-journey')


def save(state,**fields):
    state.update(fields);write_json(JOURNAL,state)


def preflight(loop,send,*,require_inventory=True):
    from conquest.merchants.farmer_preferences import enabled,rollout_enabled
    from conquest.merchants.farmer_identity import route_character
    if not enabled(route_character(loop)):return False
    policy=read_json(delivery_route.POLICY)
    from conquest.merchant_loop_acceptance import trial_permitted
    if not rollout_enabled(route_character(loop),policy=policy) and not trial_permitted(loop):return False
    try:
        if not send({'action':'delivery-readiness'}).get('qualified'):return False
        states=send({'action':'status'}).get('characters',{})
        ready=False
        for name,state in states.items():
            if name not in CHARACTERS or not state.get('ready'):continue
            snap=state.get('snapshot')
            if not isinstance(snap,dict):continue
            try:validate_snapshot(snap,name,time.time())
            except (ValueError,KeyError,TypeError):continue
            if (snap.get('booth_open') and not snap.get('trade') and not snap.get('request')
                    and available_slots(snap)>0):ready=True
        if not ready:return False
        source=send({'action':'delivery-source'})['farmer']
        origin=loop.living()['embedded_controls']['life']['map_id']
        if (source.get('character')!=farmer_name() or source.get('server')!='America'
                or not source.get('identity') or type(source.get('character_uid')) is not int
                or source['character_uid']<=0
                or source.get('map_id')!=origin or not 0<=time.time()-source.get('timestamp',0)<=5
                or source.get('hp',0)<=0):return False
        return not require_inventory or any(eligible(i) for i in source['inventory'])
    except (ValueError,OSError,KeyError,TypeError):return False


def start(loop,*,send=request,stored_scroll_uid=None):
    """Called after shopping with the origin warehouse already open."""
    if stored_scroll_uid is not None and (type(stored_scroll_uid) is not int or stored_scroll_uid<=0):
        raise ValueError('Stored scroll delivery requires an exact positive item UID')
    if pending():
        if stored_scroll_uid is not None and read_json(JOURNAL).get('stored_scroll_uid')!=stored_scroll_uid:
            raise ValueError('A different merchant journey needs reconciliation')
        return resume(loop,send=send)
    if not preflight(loop,send,require_inventory=stored_scroll_uid is None):return False
    from conquest.meteor_banking import POLICY
    from conquest.banking import transport_reserve,transfer
    origin=loop.living()['embedded_controls']['life']['map_id']
    route=read_json(POLICY).get('origins',{}).get(str(origin))
    if origin==1036 or not route or not all(route.get(leg,{}).get('verified') for leg in ('outbound','return')):
        return False
    outbound,inbound=route['outbound'],route['return']
    if ((outbound.get('source_map'),outbound.get('destination_map'),inbound.get('source_map'),inbound.get('destination_map'))
            !=(origin,1036,1036,origin)
            or any(type(p.get('fare')) is not int or p['fare']<0 for p in (outbound,inbound))):
        return False
    bank=loop.town('warehouse-money')
    reserve=outbound['fare']+inbound['fare']+transport_reserve()
    needed=max(0,reserve-bank['silver'])
    if needed>bank['stored_silver']:return False
    if needed:transfer(loop,'withdraw',needed)
    state={'phase':'prepared','origin':origin,'route':route,'started_at':time.time(),'receipts':[]}
    from conquest.merchant_loop_acceptance import journey_scope
    scope=journey_scope()
    if scope is not None:state['acceptance_scope']=scope
    # A live acceptance cycle is scoped to one exact newly looted item.  A
    # stored scroll discovered during the same town visit is unrelated stock
    # and must remain queued for a later journey; prepare_market_scroll also
    # intentionally refuses to withdraw it while acceptance is active.
    if stored_scroll_uid is not None:
        if scope is None:
            state['stored_scroll_uid']=stored_scroll_uid
        else:
            state['deferred_stored_scroll_uid']=stored_scroll_uid
    write_json(JOURNAL,state)
    loop.record('merchant_journey_started',activity='Shopping complete; taking eligible loot to the Market merchants')
    return resume(loop,send=send)


def start_market(loop,stored_scroll_uid,*,send=request):
    """Finish one stored-scroll delivery while already safely parked in Market.

    No town funding or transit leg is admitted by this entry point. Empty
    eligible bag inventory is expected; exact storage ownership is read below.
    """
    if type(stored_scroll_uid) is not int or stored_scroll_uid<=0:
        raise ValueError('Stored scroll delivery requires an exact positive item UID')
    if pending():
        old=read_json(JOURNAL)
        if not old.get('market_only') or old.get('stored_scroll_uid')!=stored_scroll_uid:
            raise ValueError('A different merchant journey needs reconciliation')
        return resume(loop,send=send)
    if loop.living()['embedded_controls']['life']['map_id']!=1036:
        raise ValueError('Stored-scroll Market delivery requires a living farmer already in Market')
    if not preflight(loop,send,require_inventory=False):return False
    state={'phase':'market','origin':1036,'market_only':True,'route':{},
           'stored_scroll_uid':stored_scroll_uid,'started_at':time.time(),'receipts':[]}
    write_json(JOURNAL,state)
    return resume(loop,send=send)


def leg(loop,state,name):
    from conquest.meteor_banking import trip
    def before_submit():
        bag=loop.town('supplies')
        save(state,phase=name+'_pending',leg_before={'silver':bag['silver'],'items':bag['items']})
    plan={**state['route'][name],
          'activity':('Heading to Conductress for merchant delivery' if name=='outbound'
                      else 'Returning from Market after verified valuable storage')}
    trip(loop,plan,before_submit=before_submit)


def verify_arrival(loop,state,name):
    before=state.get('leg_before');bag=loop.town('supplies')
    if (not before or before['silver']-bag['silver']!=state['route'][name]['fare']
            or sorted(before['items'],key=lambda i:i['uid'])!=sorted(bag['items'],key=lambda i:i['uid'])):
        raise ValueError('Merchant journey fare or inventory needs reconciliation; no repeat payment')


def current_scroll_delivery(state,item):
    """Accept only a bilateral receipt admitted for this withdrawal/journey."""
    from conquest.merchants.delivery import exact_items
    wanted=exact_items([item]);uid=item['uid']
    withdrawal=state.get('scroll_withdrawal') or {}
    admitted={row['request_id']:row for row in state.get('scroll_delivery_requests',[])
              if row.get('withdrawal_operation_id')==withdrawal.get('operation_id')}
    minimum=max(state.get('started_at',0),state.get('scroll_withdrawal_receipt',{}).get('verified_at',0))
    records=state.get('scroll_delivery_receipts',[])+read_json(delivery_route.STATE).get('receipts',[])
    for row in reversed(records):
        request=admitted.get(row.get('request_id'))
        if (request and row.get('merchant')==request.get('merchant')
                and row.get('started_at')==request.get('started_at')
                and type(row.get('verified_at')) in (int,float)
                and row['verified_at']>=max(minimum,request['started_at'])
                and row.get('outcome')=='transferred' and row.get('proof_digest')
                and exact_items(request.get('items',[])).get(uid)==wanted[uid]
                and exact_items(row.get('items',[])).get(uid)==wanted[uid]):
            return row
    return None


def scroll_ownership(state,send,*,stored=None,allow_storage_recheck=False):
    """Bind the retrieved UID to current process/ownership and its disposition."""
    from conquest.merchants.delivery import exact_items
    receipt=state.get('scroll_withdrawal_receipt') or {}
    item=receipt.get('item');before=receipt.get('after',{}).get('source')
    if not item or not before:
        raise ValueError('Scroll withdrawal lacks its exact current ownership receipt')
    source=send({'action':'delivery-source'})['farmer']
    inventory=validate_snapshot(source,farmer_name(),time.time())
    if (any(source.get(k)!=before.get(k) for k in ('character','character_uid','identity','server','silver'))
            or source.get('trade') or source.get('request')):
        raise ValueError('Retrieved scroll farmer identity, silver or modal state changed')
    wanted=exact_items([item]);uid=item['uid'];delivered=current_scroll_delivery(state,item)
    if inventory.get(uid)==wanted[uid]:return 'carried',source
    if uid in inventory:raise ValueError('Retrieved scroll attributes changed')
    if (delivered and delivered.get('outcome')=='transferred' and delivered.get('proof_digest')
            and exact_items(delivered.get('items',[])).get(uid)==wanted[uid]):
        save(state,scroll_disposition={'outcome':'delivered','item':item,
                                      'receipt':delivered,'verified_at':time.time()})
        return 'delivered',source
    if stored is not None and exact_items(stored.get('items',[])).get(uid)==wanted[uid]:
        save(state,scroll_disposition={'outcome':'deferred_rebanked','item':item,
                                      'warehouse':stored,'verified_at':time.time()})
        return 'deferred_rebanked',source
    if allow_storage_recheck:return 'needs_storage_recheck',source
    raise ValueError('Requested MeteorScroll has no exact carried, bilateral-delivery or rebanked disposition')


def stored_scroll_recheck_needed(state):
    item=state.get('scroll_withdrawal_receipt',{}).get('item')
    if not item or state.get('deposit_pending'):return False
    disposition=state.get('scroll_disposition') or {}
    return ((disposition.get('outcome')=='deferred_rebanked' and disposition.get('item')==item)
        or any(row.get('uid',row.get('stored'))==item['uid'] and row.get('type_id')==720027
               and row.get('verified_in_warehouse') is True for row in state.get('receipts',[])))


def recheck_stored_scroll(loop,state,send):
    """A fallback receipt authorizes re-observation, never inferred ownership."""
    from conquest.banking import open_warehouse,close_warehouse
    from conquest.meteor_banking import approach_market_warehouse
    disposition,_=scroll_ownership(state,send,allow_storage_recheck=True)
    if disposition!='needs_storage_recheck':
        raise ValueError('Recorded scroll fallback conflicts with current ownership; reconcile before input')
    approach_market_warehouse(loop,'Rechecking the exact stored delivery scroll after interrupted banking')
    open_warehouse(loop)
    disposition,_=scroll_ownership(state,send,stored=loop.town('warehouse-items',rich=True))
    if disposition!='deferred_rebanked':
        raise ValueError('Interrupted scroll fallback lacks fresh exact warehouse ownership')
    close_warehouse(loop)


def admit_scroll_delivery(state,active):
    from conquest.merchants.delivery import exact_items
    item=state['scroll_withdrawal_receipt']['item']
    if exact_items(active.get('items',[]))!=exact_items([item]):
        raise ValueError('The admitted merchant request does not contain exactly the requested scroll')
    requests=state.setdefault('scroll_delivery_requests',[])
    requests.append({**active,'withdrawal_operation_id':state['scroll_withdrawal']['operation_id']})
    save(state)


def close_scroll_preparation(loop,state,send):
    if not state.get('scroll_preparation_done') or not state.get('scroll_withdrawal_receipt'):return
    scroll_ownership(state,send)
    if not state.get('scroll_warehouse_closed'):
        from conquest.banking import close_warehouse
        close_warehouse(loop)
        save(state,scroll_warehouse_closed=True)


def warehouse_fallback(loop,state,*,send=request):
    from conquest.banking import open_warehouse,close_warehouse
    from conquest.meteor_banking import approach_market_warehouse
    from conquest.town_trade import stash_candidate
    from conquest.storage_halt import request_stop
    if state.get('acceptance_scope'):
        from conquest.merchant_loop_acceptance import journey_scope,verify_carried_or_delivered
        if state['acceptance_scope']!=journey_scope():
            raise ValueError('Acceptance journey changed before warehouse fallback')
        if verify_carried_or_delivered(send({'action':'delivery-source'})['farmer']):
            raise ValueError('Acceptance delivery is deferred; exact item remains carried for merchant delivery')
    if not state.get('deposit_pending') and not any(stash_candidate(i) for i in loop.town('supplies')['items']):
        return
    approach_market_warehouse(loop,'Storing remaining valuables in the Market warehouse')
    open_warehouse(loop)
    while True:
        bag=loop.town('supplies')['items'];bank=loop.town('warehouse-items')
        old=state.get('deposit_pending')
        if old:
            fields=('uid','type_id','amount','plus')
            matches=[i for i in bank['items'] if all(i.get(k)==old.get(k) for k in fields)]
            if len(matches)!=1 or any(i['uid']==old['uid'] for i in bag):
                raise ValueError('Merchant fallback deposit is uncertain; no repeated storage input')
            state['receipts'].append({'uid':old['uid'],'type_id':old['type_id'],'verified_in_warehouse':True})
            save(state,deposit_pending=None)
        remaining=[i for i in bag if stash_candidate(i)]
        if delivery_route.warehouse_exhausted(loop,bank,remaining,send=send):
            request_stop(loop,bank,remaining,reason='Market warehouse is full; merchant delivery has no verified storage destination. Farming stopped and automatic reconnect disabled')
        if not remaining:break
        item=remaining[0]
        save(state,deposit_pending=item)
        receipt=loop.town('warehouse-deposit',uid=item['uid'])
        if receipt.get('verified_in_warehouse') is not True:
            raise ValueError('Merchant fallback deposit receipt is unverified')
        # Reconcile the exact UID through fresh inventory on the next pass.
        loop.record('valuable_stored',**receipt,plus=item.get('plus'),
                    activity='Remaining valuable verified in Market warehouse')
    if state.get('scroll_withdrawal_receipt'):
        scroll_ownership(state,send,stored=loop.town('warehouse-items',rich=True))
    close_warehouse(loop)


def prepare_market_scroll(loop,state,*,send=request):
    """Retrieve at most one freshly observed stored scroll for this journey.

    This stage ends at inventory ownership. The existing delivery route still
    plans, revalidates and receipts the merchant's independent ownership.
    """
    if state.get('acceptance_scope'):return False  # Never withdraw unrelated trial stock, including after restart.
    from conquest.banking import open_warehouse,close_warehouse
    from conquest.meteor_banking import approach_market_warehouse
    if state.get('scroll_preparation_done'):return False
    if delivery_route.pending():
        raise ValueError('Reconcile the submitted merchant delivery before any scroll withdrawal')
    old=state.get('scroll_withdrawal')
    if old:
        # A possibly submitted warehouse click is settled before any movement
        # or reopening input. Never invoke the withdrawal endpoint again.
        result=loop.town('warehouse-reconcile-scroll',operation_id=old['operation_id'],uid=old['uid'])
        if settle_scroll_admission(state,result):
            close_scroll_preparation(loop,state,send)
            return True
        # A closed no-input admission gets a fresh operation ID and fresh
        # warehouse authority below. Submitted/blocked operations never enter.
    if not state.get('scroll_withdrawal'):
        pending_item=state.get('loose_meteor_pending')
        if pending_item:
            bag=loop.town('supplies');stored=loop.town('warehouse-items')
            matches=[i for i in stored['items'] if all(i.get(k)==pending_item.get(k)
                for k in ('uid','type_id','amount','plus'))]
            if len(matches)!=1 or any(i['uid']==pending_item['uid'] for i in bag['items']):
                raise ValueError('Leftover Meteor deposit is uncertain; no repeated warehouse input')
            state['receipts'].append({'uid':pending_item['uid'],'type_id':1088001,
                                      'verified_in_warehouse':True})
            save(state,loose_meteor_pending=None)
        if not state.get('loose_meteor_pending') and not preflight(loop,send,require_inventory=False):
            return False
        approach_market_warehouse(loop,'Retrieving a stored MeteorScroll for merchant delivery')
        open_warehouse(loop)
        while True:
            bag=loop.town('supplies');stored=loop.town('warehouse-items')
            pending_item=state.get('loose_meteor_pending')
            if pending_item:
                matches=[i for i in stored['items'] if all(i.get(k)==pending_item.get(k)
                    for k in ('uid','type_id','amount','plus'))]
                if len(matches)!=1 or any(i['uid']==pending_item['uid'] for i in bag['items']):
                    raise ValueError('Leftover Meteor deposit is uncertain; no repeated warehouse input')
                state['receipts'].append({'uid':pending_item['uid'],'type_id':1088001,
                                          'verified_in_warehouse':True})
                save(state,loose_meteor_pending=None)
            loose=next((i for i in bag['items'] if i['type_id']==1088001),None)
            if loose is None:break
            if len(stored['items'])>=stored['capacity']:
                raise ValueError('No warehouse space for loose Meteors; no delivery scroll withdrawn')
            save(state,loose_meteor_pending=loose)
            receipt=loop.town('warehouse-deposit',uid=loose['uid'])
            if receipt.get('verified_in_warehouse') is not True:
                raise ValueError('Leftover Meteor deposit receipt is unverified; reconcile before delivery')
        requested=state.get('stored_scroll_uid')
        choices=[i for i in stored['items'] if i['type_id']==720027
                 and i.get('amount')==i.get('limit')==1
                 and (requested is None or i['uid']==requested)]
        if not choices or len(bag['items'])>=bag['capacity']:
            if requested is not None:
                raise ValueError('Requested MeteorScroll is not freshly verified in storage with free bag capacity')
            save(state,scroll_preparation_done=True)
            close_warehouse(loop)
            return False
        if not preflight(loop,send,require_inventory=False):
            close_warehouse(loop)
            return False
        # The town operation independently checks rich sockets, binding,
        # profile/process identity and fresh ownership before its durable click.
        selected=min(choices,key=lambda i:i['uid'])
        old={'operation_id':'journey-scroll:'+uuid.uuid4().hex,'uid':selected['uid']}
        save(state,scroll_withdrawal=old)
        result=loop.town('warehouse-withdraw-scroll',**old)
    settle_scroll_admission(state,result)
    close_scroll_preparation(loop,state,send)
    return True


def resume(loop,*,send=request):
    if not pending():return False
    from conquest.banking import open_warehouse,close_warehouse
    from conquest.navigation import read_terrain
    state=read_json(JOURNAL);origin=state['origin']
    if state.get('acceptance_scope'):
        from conquest.merchant_loop_acceptance import journey_scope,journey_scope_completed,pending_delivery_matches
        current_scope=journey_scope()
        if state['acceptance_scope']!=current_scope:
            if not journey_scope_completed(state['acceptance_scope']):
                raise ValueError('Acceptance journey scope changed; reconcile without new input')
            # The exact acceptance transfer and refill became terminal before
            # this follow-on journey started.  Detach that finished scope and
            # restore an untouched deferred scroll to the ordinary queue.
            state.pop('acceptance_scope',None)
            deferred=state.pop('deferred_stored_scroll_uid',None)
            if deferred is not None:state['stored_scroll_uid']=deferred
            save(state)
        if state.get('acceptance_scope'):
            native=read_json(delivery_route.STATE).get('active')
            if native and not pending_delivery_matches(native):
                raise ValueError('Acceptance delivery admission changed; reconcile without new input')
        # Recover journals written before acceptance journeys stopped claiming
        # unrelated stored scrolls.  This is read-only with respect to the
        # game: no withdrawal was admitted and the exact scroll remains stored.
        scoped_uid=state.get('acceptance_scope',{}).get('item',{}).get('uid')
        stored_uid=state.get('stored_scroll_uid')
        if (state.get('acceptance_scope') and stored_uid is not None and stored_uid!=scoped_uid
                and not state.get('scroll_withdrawal')
                and not state.get('scroll_withdrawal_receipt')
                and not state.get('scroll_preparation_done')):
            state['deferred_stored_scroll_uid']=stored_uid
            state.pop('stored_scroll_uid',None)
            save(state)
    loop.phase='restocking'
    world=loop.living()['embedded_controls']['life']['map_id']
    loop.terrain=read_terrain(installation_path(r'C:\Program Files\Classic Conquer 2.0'),world)
    if world==origin and state['phase']=='prepared':
        close_warehouse(loop)
        leg(loop,state,'outbound');world=1036
    if world==1036 and state['phase']=='outbound_pending':
        verify_arrival(loop,state,'outbound');save(state,phase='market',leg_before=None)
    if world==1036 and state['phase']=='market':
        # Reconcile submitted trades before warehouse or movement input.
        if delivery_route.pending():
            delivery_route.settle(loop,send,read_json(delivery_route.STATE))
        rechecked_storage=stored_scroll_recheck_needed(state)
        if rechecked_storage:recheck_stored_scroll(loop,state,send)
        if not rechecked_storage and not state.get('deposit_pending'):
            delivered_before=False
            close_scroll_preparation(loop,state,send)
            if (not state.get('scroll_preparation_done') and state.get('stored_scroll_uid') is None and not state.get('scroll_withdrawal')
                    and not state.get('loose_meteor_pending')
                    and not any(i['type_id']==1088001 for i in loop.town('supplies')['items'])):
                delivery_route.market_storage(loop,send=send)
                delivered_before=True
            prepared=prepare_market_scroll(loop,state,send=send)
            if state.get('stored_scroll_uid') is not None and not state.get('scroll_preparation_done'):
                raise ValueError('Requested stored scroll preparation is deferred; journey remains pending')
            if state.get('scroll_withdrawal_receipt'):
                disposition,_=scroll_ownership(state,send)
                if disposition=='carried':
                    receipts=delivery_route.market_storage(loop,send=send,
                        items=[state['scroll_withdrawal_receipt']['item']],
                        on_admitted=lambda active:admit_scroll_delivery(state,active))
                    state.setdefault('scroll_delivery_receipts',[]).extend(receipts or [])
                    save(state)
                    scroll_ownership(state,send)
            elif prepared or not delivered_before:
                delivery_route.market_storage(loop,send=send)
        warehouse_fallback(loop,state,send=send)
        if state.get('stored_scroll_uid') is not None and not state.get('scroll_disposition'):
            raise ValueError('Requested stored scroll disposition is unresolved; journey remains pending')
        if state.get('market_only'):
            save(state,phase='completed',completed_at=time.time())
            loop.record('merchant_journey_complete',
                        activity='Stored-scroll merchant delivery and any fallback storage are verified')
            return True
        leg(loop,state,'return');world=origin
    if world==origin and state['phase']=='return_pending':
        verify_arrival(loop,state,'return')
        from conquest.merchants.service_visit import MarketVisit
        MarketVisit().departed(world)
        open_warehouse(loop)
        save(state,phase='completed',completed_at=time.time())
        loop.overflow_bank_changed=True
        loop.record('merchant_journey_complete',activity='Merchant delivery and storage complete; resuming the farming route')
        return True
    raise ValueError('Merchant journey arrival is uncertain; valuables preserved and no repeat fare issued')
