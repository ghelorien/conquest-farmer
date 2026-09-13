"""Merchant delivery stage for required Market storage visits.

Warehouse callers retain ownership of fallback and the verified return trip.
An uncertain submitted trade never falls through to warehouse input.
"""
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
                        or len(inventory)>=merchant['capacity']):continue
                path=loop.terrain.travel_path(tuple(farmer['position']),tuple(merchant['position']))
                if (path and tuple(path[0])==tuple(farmer['position'])
                        and tuple(path[-1])==tuple(merchant['position'])):
                    return False
            except (ValueError,OSError,KeyError,TypeError):continue
    except (ValueError,OSError,KeyError,TypeError):pass
    return True


def candidates(loop,send):
    status=send({'action':'status'})
    states=[];farmer=None
    for name,state in status.get('characters',{}).items():
        if not state.get('ready'):continue
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
             'uids':[i['uid'] for i in active['items']]}
    if start:
        send(command)
    else:
        result=send({'action':'delivery-status','request_id':key})
        if result.get('receipt') is None:
            raise ValueError('Delivery submission has no receipt; reconcile before any storage input')
        if not result.get('running'):
            send(command)  # Existing source operations permit read-only recovery only.
    until=time.monotonic()+30
    while True:
        check_stop(loop)
        result=send({'action':'delivery-status','request_id':key})
        receipt=result.get('receipt')
        if not result.get('running'):
            if (result.get('error') or not receipt or receipt.get('request_id')!=key
                    or receipt.get('phase')!='verified' or receipt.get('character')!=active['merchant']
                    or sorted(receipt.get('uids',[]))!=sorted(command['uids'])):
                raise ValueError('Merchant delivery needs reconciliation; valuables remain protected')
            record={**active,'verified_at':time.time()}
            state.setdefault('receipts',[]).append(record);state['active']=None
            write_json(STATE,state)
            loop.record('merchant_delivery_verified',request_id=key,merchant=active['merchant'],
                        items=active['items'],activity='Valuables delivered and verified in both inventories')
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


def approach_merchant(loop,plan,send):
    """Stop at verified request range, never force entry into an occupied booth."""
    pair=send({'action':'delivery-pair','character':plan['merchant']})
    f,m=pair['farmer'],pair['merchant']
    if f['map_id']!=1036 or m['map_id']!=1036 or m['position']!=plan['position']:
        raise ValueError('Merchant approach location changed; re-plan before moving')
    source,target=tuple(f['position']),tuple(m['position'])
    distance=lambda p:max(abs(a-b) for a,b in zip(p,target))
    if distance(source)<=12:return
    path=loop.terrain.travel_path(source,target)
    if not path or tuple(path[0])!=source or tuple(path[-1])!=target:
        raise ValueError('No checked path into merchant trade range')
    destination=next(tuple(p) for p in path if distance(p)<=12)
    loop.travel(destination,arrival_radius=0,activity=f"Approaching trade range of {plan['merchant']}")


def market_storage(loop,*,send=request):
    state=read_json(STATE)
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
    # Inventory is bounded to forty slots. Re-plan after every receipt so
    # listings, capacity changes and split deliveries cannot reuse stale plans.
    for _ in range(40):
        if not transfers_enabled(route_character(loop)):return receipts
        if not plans:return receipts
        plan=plans[0]
        check_stop(loop)
        for window in ('Dialog','Inventory'):
            try:loop.town('service-close-panel',window=window)
            except ValueError as error:
                if not any(note in str(error) for note in ('not active','absent')):raise
        approach_merchant(loop,plan,send)
        fresh=candidates(loop,send)
        if not fresh:return receipts
        current=fresh[0]
        if (current['merchant']!=plan['merchant'] or current['position']!=plan['position']
                or current['merchant_identity']!=plan['merchant_identity']):
            plans=fresh
            continue
        from conquest.safe_reload import clear_observation
        health=loop.health();control=health['embedded_controls']['control']
        if control['enabled'] or control.get('paused') or not clear_observation(health):
            raise ValueError('Merchant delivery requires a memory-verified safe stopped farmer')
        key='route-delivery:'+uuid.uuid4().hex
        windows=WorkWindows();windows.reserve(key,town=True)
        requested=False
        try:
            # Record ownership before issuing the request so an uncertain
            # response still revokes the possible window in finally.
            requested=True
            send({'action':'delivery-window','request_id':key})
            deadline=windows.started()
            send({'action':'handoff-grant','request_id':key,'revision':control['revision'],
                  'expires_at':deadline,'safe':True})
            # Persist before the first bridge submission, including uncertain
            # HTTP results. Restart recovery never blindly resubmits input.
            state['active']={'request_id':key,'merchant':current['merchant'],
                             'items':current['items'],'started_at':time.time()}
            write_json(STATE,state)
            receipts.append(settle(loop,send,state,start=True))
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
        plans=candidates(loop,send)
    raise ValueError('Merchant capacity keeps changing; defer further delivery input')
