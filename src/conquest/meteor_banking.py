"""Reusable ten-Meteor consolidation, using verified dialog and item receipts."""
from conquest.character_context import installation_path, state_path
import time
from pathlib import Path
from conquest.discord_notify import read_json,write_json

POLICY=Path('profiles/meteor-banking.json')
JOURNAL=Path(state_path('reports/banking/meteor-consolidation.json'))
METEOR=1088001
SCROLL=720027


def batch(items):
    meteors=[i for i in items if i['type_id']==METEOR and i['amount']==i['limit']==1]
    if len({i['uid'] for i in meteors})!=len(meteors):
        raise ValueError('Meteor batch contains duplicate inventory/warehouse IDs')
    return meteors[:10] if len(meteors)>=10 else []


def exchange_received(before,after,fee=0):
    identity=lambda i:(i['uid'],i['type_id'],i['amount'],i['limit'],i.get('plus'))
    old={i['uid']:i for i in before['items']};new={i['uid']:i for i in after['items']}
    removed=[i for uid,i in old.items() if uid not in new]
    added=[i for uid,i in new.items() if uid not in old]
    return (len(removed)==10 and all(i['type_id']==METEOR and i['amount']==i['limit']==1 for i in removed)
        and len(added)==1 and added[0]['type_id']==SCROLL and added[0]['amount']==1
        and all(identity(old[uid])==identity(new[uid]) for uid in old.keys()&new.keys())
        and before['silver']-after['silver']==fee
        and before.get('equipped_ammo')==after.get('equipped_ammo'))


def select_saved_dialog(loop,name,step):
    deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        loop.check_stop()
        data=loop.town('service-dialog')
        if data['records']==step['records']:
            from conquest.dialog_geometry import scroll_direction
            if scroll_direction(data,step['option'],data.get('viewport')):
                loop.town('service-scroll-dialog',name=name,records=data['records'],option=step['option'])
                time.sleep(.15)
                continue
            return loop.town('service-select',name=name,option=step['option'],records=data['records'])
        time.sleep(.1)
    raise ValueError('Saved Meteor route dialog changed; no choice sent')


def open_saved_service(loop,name,step):
    from conquest.worker import request
    # Opening an NPC can first walk into interaction range. Retry only that
    # non-transactional opening, never a selected option, exchange or fare.
    for attempt in range(3):
        loop.town('service-open',name=name)
        deadline=time.monotonic()+2
        while time.monotonic()<deadline:
            loop.living()
            try:data=request(loop.info,'town',{'action':'service-dialog'})
            except ValueError as error:
                if not any(t in str(error) for t in ('not active','absent','changed during observation')):raise
            else:
                if data['records']==step['records']:return
                raise ValueError('Saved Meteor route dialog changed; no choice sent')
            time.sleep(.1)
        loop.record('service_open_retry',npc=name,attempt=attempt+1,
                    activity='Reopening '+name+' after approaching interaction range')
    raise ValueError('Service dialog did not open; no transaction issued')


def trip(loop,plan,*,before_submit=None):
    from conquest.navigation import read_terrain
    life=loop.living()['embedded_controls']['life']
    if life['map_id']!=plan['source_map']:raise ValueError('Meteor route source map changed')
    if life['map_id']==1036 and plan['destination_map']!=1036:
        from conquest.town_trade import stash_candidate
        if any(stash_candidate(item) for item in loop.town('supplies')['items']):
            raise ValueError('Stay in Market: deposit all protected valuables before returning to town')
    if loop.terrain.map_id!=life['map_id']:
        loop.terrain=read_terrain(installation_path(r'C:\Program Files\Classic Conquer 2.0'),life['map_id'])
    # The warehouse frontage rejects short walking clicks. Leave through the
    # observed clear eastbound jump before planning toward the controller.
    # This is an intermediate waypoint, so do not chase a one-tile offset.
    if life['map_id']==1036 and plan['destination_map']!=1036:
        position=life['position']
        if max(abs(a-b) for a,b in zip(position,(186,188)))<=4:
            # The live exit landed at (191,186); chasing (194,184) from
            # there hit the frontage again despite already leaving the bank.
            loop.travel((191,186),activity='Leaving Market warehouse toward the city transport',arrival_radius=2)
    loop.record('meteor_travel',activity=plan['activity'])
    # The Market exit does not reliably open at the generic twelve-tile
    # service distance. Reach its qualified approach before clicking it.
    market_exit = life['map_id']==1036 and plan['destination_map']!=1036
    if market_exit:
        current=loop.living()['embedded_controls']['life']['position']
        if current[0]>=239 and current[1]<200:
            # Merchant booths occupy the northeast diagonal to the exit.
            # The central aisle was traversed during the delivery qualification.
            loop.travel((225,206),activity='Leaving merchant booths through the central Market aisle',arrival_radius=2)
            current=loop.living()['embedded_controls']['life']['position']
        if max(abs(a-b) for a,b in zip(current,plan['approach']))>4:
            waypoints=plan.get('waypoints',[])
            if waypoints:
                nearest=min(range(len(waypoints)),key=lambda i:max(abs(a-b) for a,b in zip(current,waypoints[i])))
                waypoints=waypoints[nearest:]
            for point in waypoints:
                loop.travel(tuple(point),activity=plan['activity'],arrival_radius=2)
    loop.travel(tuple(plan['approach']),activity=plan['activity'],
                service_name=None if market_exit else plan['npc'])
    service=loop.town('service-locate',name=plan['npc'])
    if service['identity']!=plan['identity']:raise ValueError('Meteor route NPC identity changed')
    open_saved_service(loop,plan['npc'],plan['dialogs'][0])
    before=loop.town('supplies')
    if life['map_id']==1036 and plan['destination_map']!=1036:
        if any(stash_candidate(item) for item in before['items']):
            raise ValueError('Stay in Market: valuables appeared before departure')
    if before_submit:before_submit()
    for step in plan['dialogs']:select_saved_dialog(loop,plan['npc'],step)
    deadline=time.monotonic()+8
    while time.monotonic()<deadline:
        data=loop.health()['embedded_controls'];fresh=data.get('life')
        if (fresh and not fresh['dead_candidate'] and fresh['object_address']==life['object_address']
                and fresh['map_id']==plan['destination_map'] and 0<=time.time()-data.get('observed_at',0)<=1):
            after=loop.town('supplies')
            if before['silver']-after['silver']!=plan['fare']:raise ValueError('Meteor route fare was not verified')
            loop.terrain=read_terrain(installation_path(r'C:\Program Files\Classic Conquer 2.0'),fresh['map_id'])
            from conquest.merchants.service_visit import MarketVisit
            MarketVisit().departed(fresh['map_id'])
            return
        time.sleep(.1)
    raise ValueError('Meteor route arrival unverified; no repeat payment issued')


PENDING={'withdrawing','travelling','exchange_ready','exchange_pending','storing_scroll','stored_in_market','returning','carried_in_market'}

def pending():return read_json(JOURNAL).get('phase') in PENDING


def save(state,phase=None,**fields):
    state.update(fields)
    if phase:state['phase']=phase
    write_json(JOURNAL,state)


def carried(loop):
    from conquest.town_trade import stash_candidate
    return [i for i in loop.town('supplies')['items'] if stash_candidate(i)]


def approach_market_warehouse(loop,activity):
    life=loop.living()['embedded_controls']['life']
    if life['map_id']!=1036:raise ValueError('Market warehouse approach requires Market')
    try:loop.town('service-close-panel',window='Dialog')
    except ValueError as error:
        if not any(text in str(error) for text in ('not active','absent')):raise
    if (loop.town('vendor-status',vendor_type=0) or {}).get('reachable'):
        return
    policy=read_json(POLICY)
    target=tuple(policy.get('market_bank_approach',[182,184]))
    if max(abs(a-b) for a,b in zip(life['position'],target))<=2:return
    # The old (186,188) approach gets trapped at the warehouse frontage.
    # Retain the verified corridor but accept nearby tiles in this crowd.
    # open_warehouse rechecks the NPC and interaction before any transaction.
    recovered=set()
    pending_points=[(tuple(p),activity,2) for p in policy.get('market_bank_waypoints',[[186,184]])+[list(target)]]
    while pending_points:
        point,note,radius=pending_points.pop(0)
        try:
            loop.travel(point,activity=note,vendor_type=0,arrival_radius=radius)
        except ValueError as error:
            fresh=loop.living()['embedded_controls']['life']
            if str(error)!='Town route remains obstructed' or fresh['map_id']!=1036:raise
            if max(abs(a-b) for a,b in zip(fresh['position'],(183,190)))<=6:
                area='frontage'
                recovery=[(p,'Taking the western corridor to Market warehouse',1)
                          for p in ((186,199),(176,199),(176,183))]
                try:loop.town('service-close-panel',window='Inventory')
                except ValueError as close_error:
                    if not any(text in str(close_error) for text in ('not active','absent')):raise
            elif max(abs(a-b) for a,b in zip(fresh['position'],(201,215)))<=4:
                area='southern_crossing'
                recovery=[(p,'Taking the western Market aisle to the warehouse',2)
                          for p in ((189,215),(189,203))]
            else:raise
            # A crossing recovery must not consume the warehouse's recovery.
            # Each area gets one bounded attempt; failed movement cannot replay
            # deposits or fares because this queue contains movement only.
            if area in recovered:raise
            recovered.add(area)
            pending_points=recovery+[(point,note,radius)]+pending_points
        if (loop.town('vendor-status',vendor_type=0) or {}).get('reachable'):return



def market_bank(loop,state):
    from conquest.banking import open_warehouse,close_warehouse
    from conquest.storage_halt import request_stop
    from conquest.merchants.delivery_route import market_storage,receipt_for,warehouse_exhausted
    market_storage(loop)
    approach_market_warehouse(loop,'Storing valuables in Market before returning to Phoenix')
    open_warehouse(loop)
    while True:
        stored=loop.town('warehouse-items');items=carried(loop)
        if warehouse_exhausted(loop,stored,items):
            save(state,'market_full');request_stop(loop,stored,items)
        if not items:break
        item=items[0]
        receipt=loop.town('warehouse-deposit',uid=item['uid'])
        if receipt.get('verified_in_warehouse') is not True:raise ValueError('Market deposit receipt missing; no return issued')
        state.setdefault('receipts',[]).append(receipt);save(state)
        loop.record('valuable_stored',**receipt,plus=item.get('plus'),activity='Valuable safely stored in Market')
    scroll=state.get('scroll_uid')
    consumed=state.get('user_confirmed_scroll_consumption') or {}
    manually_used=(consumed.get('uid')==scroll and consumed.get('confirmed') is True
                   and consumed.get('source')=='explicit user confirmation')
    moved=state.get('user_confirmed_scroll_transfer') or {}
    manually_moved=(moved.get('uid')==scroll and moved.get('type_id')==SCROLL
                    and moved.get('confirmed') is True
                    and moved.get('source')=='explicit user confirmation'
                    and moved.get('destination')=='another_character'
                    and not any(i['uid']==scroll for i in stored['items'])
                    and not any(i['uid']==scroll for i in loop.town('supplies')['items']))
    delivered=receipt_for(scroll,SCROLL) if scroll else None
    if scroll and not manually_used and not manually_moved and not delivered and not any(i['uid']==scroll and i['type_id']==SCROLL for i in stored['items']):
        raise ValueError('Expected MeteorScroll is not in Market storage; no return issued')
    if manually_moved:
        # Operator testimony resolves this historical trip only. It is not a
        # merchant receipt or proof of the other character's current inventory.
        state['scroll_resolution']='operator_reported_external_transfer'
    save(state,'stored_in_market',market_verified_at=time.time())
    close_warehouse(loop)


def resume(loop):
    """Reconcile item IDs before continuing an interrupted ten-Meteor trip."""
    if not pending():return False
    state=read_json(JOURNAL);policy=read_json(POLICY)
    route=policy.get('origins',{}).get(str(state['origin']))
    if not route:raise ValueError('Meteor trip origin has no verified transport')
    from conquest.banking import open_warehouse,close_warehouse
    from conquest.navigation import read_terrain
    loop.phase='restocking'
    world=loop.living()['embedded_controls']['life']['map_id']
    loop.terrain=read_terrain(installation_path(r'C:\Program Files\Classic Conquer 2.0'),world)
    if world==state['origin'] and state['phase']=='returning':
        if carried(loop):raise ValueError('Protected valuables unexpectedly carried after Market return')
        from conquest.merchants.service_visit import MarketVisit
        MarketVisit().departed(world)
        open_warehouse(loop);save(state,'completed',completed_at=time.time())
        loop.record('meteor_loop_complete',activity='Market banking complete; checking Phoenix supplies before farming')
        return True
    if world==state['origin'] and state['phase']=='withdrawing':
        open_warehouse(loop)
        for uid in state['meteor_uids']:
            bag=loop.town('supplies')['items'];bank=loop.town('warehouse-items')['items']
            in_bag=[i for i in bag if i['uid']==uid];in_bank=[i for i in bank if i['uid']==uid]
            if len(in_bag)+len(in_bank)!=1 or (in_bag or in_bank)[0]['type_id']!=METEOR:
                raise ValueError('Meteor withdrawal state is ambiguous; no additional withdrawal issued')
            if in_bag:continue
            receipt=loop.town('warehouse-withdraw-meteor',uid=uid)
            if receipt.get('verified_in_inventory') is not True:raise ValueError('Meteor withdrawal receipt missing')
            state.setdefault('withdrawals',[]).append(receipt);save(state)
        # The server chooses the consumed Meteors. Leave exactly the recorded
        # batch in inventory so its receipt cannot consume unrelated IDs.
        for item in loop.town('supplies')['items']:
            if item['type_id']==METEOR and item['uid'] not in state['meteor_uids']:
                receipt=loop.town('warehouse-deposit',uid=item['uid'])
                if receipt.get('verified_in_warehouse') is not True:
                    raise ValueError('Extra Meteor deposit unverified; no departure issued')
        save(state,'travelling')
    if world==state['origin'] and state['phase']=='travelling':
        if state.get('departure_attempted'):raise ValueError('Meteor departure uncertain; no repeat fare issued')
        close_warehouse(loop);save(state,departure_attempted=True)
        trip(loop,route['outbound']);world=1036
        save(state,'exchange_ready')
    if world!=1036:raise ValueError('Unexpected map during Meteor trip; valuables preserved')
    if state['phase'] in ('travelling','carried_in_market'):save(state,'exchange_ready')
    exchange=policy['exchange']
    if state['phase']=='exchange_ready':
        if not exchange.get('transaction_verified'):raise ValueError('Meteor exchange has not been verified')
        bag=loop.town('supplies')
        expected=set(state['meteor_uids'])
        if expected!={i['uid'] for i in bag['items'] if i['type_id']==METEOR}:
            raise ValueError('Recorded Meteor batch is missing; no exchange issued')
        loop.travel(tuple(exchange['approach']),activity='Heading to MillionaireLee to pack ten Meteors',service_name='MillionaireLee')
        npc=loop.town('service-locate',name='MillionaireLee')
        if npc['identity']!=exchange['identity']:raise ValueError('MillionaireLee identity changed')
        loop.town('service-open',name='MillionaireLee')
        steps=exchange['dialogs']
        for step in steps[:-1]:select_saved_dialog(loop,'MillionaireLee',step)
        before=loop.town('supplies');save(state,'exchange_pending',before=before)
        select_saved_dialog(loop,'MillionaireLee',steps[-1])
    if state['phase']=='exchange_pending':
        before=state['before'];deadline=time.monotonic()+5
        while True:
            after=loop.town('supplies')
            if exchange_received(before,after,exchange['fee']):break
            if time.monotonic()>=deadline:raise ValueError('Meteor exchange unverified; no repeat exchange or return issued')
            time.sleep(.1)
        old={i['uid'] for i in before['items']};new={i['uid'] for i in after['items']}
        if old-new!=set(state['meteor_uids']):raise ValueError('Exchange removed a different batch; remain in Market')
        scroll=next(i for i in after['items'] if i['uid'] not in old)
        save(state,'storing_scroll',scroll_uid=scroll['uid'],after=after,exchange_verified=True)
        loop.record('meteor_exchange_verified',activity='Ten Meteors packed; storing the scroll in Market',scroll_uid=scroll['uid'])
    if state['phase'] in ('storing_scroll','stored_in_market','returning'):
        # A verified bank receipt survives a later movement interruption. Do
        # not walk back to the warehouse unless valuables remain to deposit.
        if (state['phase']=='storing_scroll' or not state.get('market_verified_at')
                or carried(loop)):
            market_bank(loop,state)
        save(state,'returning')
        trip(loop,route['return'])
        # Arrival is checked by trip; the reopened origin bank lets shopping
        # finish its cash transfer without reusing the old city balance.
        open_warehouse(loop);save(state,'completed',completed_at=time.time())
        loop.record('meteor_loop_complete',activity='Market banking complete; checking Phoenix supplies before farming')
        return True
    raise ValueError('Meteor journal needs reconciliation')


def consolidate(loop,stored):
    """Start a qualified batch; finish back at the original open warehouse."""
    policy=read_json(POLICY)
    if not policy.get('enabled') or not policy.get('qualified'):return False
    if not policy.get('exchange'):raise ValueError('Meteor exchange has not been qualified')
    previous=read_json(JOURNAL)
    if previous and previous.get('phase')!='completed':
        raise ValueError('An unfinished Meteor transfer needs reconciliation; no new batch withdrawn')
    before=loop.town('supplies')
    loose=[i for i in before['items'] if i['type_id']==METEOR and i['amount']==i['limit']==1]
    items=batch(loose+stored['items'])
    if not items:return False
    life=loop.living()['embedded_controls']['life'];origin=life['map_id']
    route=policy.get('origins',{}).get(str(origin))
    if not route:return False
    if not all(route[leg].get('verified') for leg in ('outbound','return')):
        raise ValueError('Meteor consolidation requires a verified round trip')
    bag_uids={i['uid'] for i in before['items']};needed=sum(i['uid'] not in bag_uids for i in items)
    if before['capacity']-len(before['items'])<needed:
        raise ValueError('Insufficient free inventory slots for the ten-Meteor batch')
    from conquest.banking import transfer,transport_reserve
    reserve=route['outbound']['fare']+route['return']['fare']+policy['exchange']['fee']+transport_reserve()
    if before['silver']<reserve:
        bank=loop.town('warehouse-money')
        if bank['stored_silver']<reserve-before['silver']:raise ValueError('Insufficient transport reserve for Meteor consolidation')
        transfer(loop,'withdraw',reserve-before['silver'])
    state={'origin':origin,'meteor_uids':[i['uid'] for i in items],'started_at':time.time(),'phase':'withdrawing'}
    save(state)
    loop.record('meteor_consolidation_started',activity='Withdrawing ten Meteors for Market packing and banking')
    return resume(loop)
