"""Required-town delivery trip with verified fares and warehouse fallback."""
from conquest.merchants.capacity import available_slots
from conquest.character_context import farmer_name
from conquest.character_context import installation_path, state_path
from pathlib import Path
import time

from conquest.discord_notify import read_json,write_json
from conquest.merchants.bridge import request
from conquest.merchants.delivery import eligible,validate_snapshot
from conquest.merchants.journal import CHARACTERS
from conquest.merchants import delivery_route

JOURNAL=Path(state_path('reports/banking/merchant-journey.json'))


def pending():
    return read_json(JOURNAL).get('phase') in ('prepared','outbound_pending','market','return_pending')


def save(state,**fields):
    state.update(fields);write_json(JOURNAL,state)


def preflight(loop,send):
    from conquest.merchants.farmer_preferences import enabled
    from conquest.merchants.farmer_identity import route_character
    if not enabled(route_character(loop)):return False
    policy=read_json(delivery_route.POLICY)
    if not policy.get('enabled') or not policy.get('parity_verified'):return False
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
        return any(eligible(i) for i in source['inventory'])
    except (ValueError,OSError,KeyError,TypeError):return False


def start(loop,*,send=request):
    """Called after shopping with the origin warehouse already open."""
    if pending():return resume(loop,send=send)
    if not preflight(loop,send):return False
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
    write_json(JOURNAL,state)
    loop.record('merchant_journey_started',activity='Shopping complete; taking eligible loot to the Market merchants')
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


def warehouse_fallback(loop,state,*,send=request):
    from conquest.banking import open_warehouse,close_warehouse
    from conquest.meteor_banking import approach_market_warehouse
    from conquest.town_trade import stash_candidate
    from conquest.storage_halt import request_stop
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
    close_warehouse(loop)


def resume(loop,*,send=request):
    if not pending():return False
    from conquest.banking import open_warehouse,close_warehouse
    from conquest.navigation import read_terrain
    state=read_json(JOURNAL);origin=state['origin']
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
        if not state.get('deposit_pending'):
            delivery_route.market_storage(loop,send=send)
        warehouse_fallback(loop,state,send=send)
        leg(loop,state,'return');world=origin
    if world==origin and state['phase']=='return_pending':
        verify_arrival(loop,state,'return')
        open_warehouse(loop)
        save(state,phase='completed',completed_at=time.time())
        loop.overflow_bank_changed=True
        loop.record('merchant_journey_complete',activity='Merchant delivery and storage complete; resuming the farming route')
        return True
    raise ValueError('Merchant journey arrival is uncertain; valuables preserved and no repeat fare issued')
