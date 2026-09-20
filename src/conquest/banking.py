"""Bank earnings after shopping; keep transport fares available."""
from conquest.character_context import state_path
import json
import math
from pathlib import Path
import time
from conquest.discord_notify import read_json,write_json
from conquest.valuables import DRAGONBALL_TYPES

CONFIG=Path('profiles/banking.json')
STATUS=Path(state_path('reports/banking/status.json'))
LEDGER=Path(state_path('reports/banking/transfers.jsonl'))


def urgent_valuables(items):
    """Carried Dragonballs and +2-or-higher gear require immediate banking."""
    result=[]
    for item in items:
        get=item.get if isinstance(item,dict) else lambda k,d=None:getattr(item,k,d)
        kind,plus=get('type_id'),get('plus')
        if get('slot') is not None and (kind in DRAGONBALL_TYPES or
                (type(kind) is int and 100000<=kind<600000
                 and type(plus) is int and 2<=plus<=12)):
            result.append(item)
    return result


def policy():return read_json(CONFIG)


def transport_reserve():
    from conquest.conductress import TRIPS
    fares=[t['price'] for t in read_json(TRIPS,{}).get('trips',[]) if t.get('verified') and t.get('price',0)>0]
    return max(policy().get('transport_reserve',200),2*max(fares,default=100))


def shopping_budget(route,bag,level=None):
    from conquest.overnight import supply_counts
    counts=supply_counts(bag,route)
    # These pack sizes/prices were independently verified in live purchases.
    # Actual purchases still reread the shop price and respect the wallet.
    kind=route.supplies.arrow_type
    catalog=read_json('profiles/archer-shop-catalog.json',{}).get('cities',{}).get(str(route.restock_map_id),{})
    products=catalog.get('5',{}).get('products',[])
    price=next((p['price'] for p in products if p['type_id']==kind),{1050000:200,1050001:4800}.get(kind))
    ammo=bag.get('equipped_ammo')
    limits=[i['limit'] for i in bag['items'] if i['type_id']==kind]
    if ammo and ammo['type_id']==kind:limits.append(ammo['limit'])
    # SpeedArrow capacity was supplied by the user; actual inventory limits
    # take precedence and the shop price is still read before any purchase.
    pack=max(limits,default={1050000:200,1050001:1000,1050002:5000}.get(kind,0))
    if not price or not pack:raise ValueError('Arrow refill budget is not qualified')
    from conquest.arrow_upgrades import MAX_ARROW_PACKS,arrow_pack_count,NORMAL_ARROWS
    # One/two-arrow remnants will be recycled on this required shop visit.
    # They must not consume the budget for the replacement pack. The actual
    # purchase cap still counts every carried pack until recycling is verified.
    usable_bag={**bag,'items':[i for i in bag['items']
        if i['type_id'] not in NORMAL_ARROWS or i['amount']>=3]}
    if ammo and ammo['type_id'] in NORMAL_ARROWS and ammo['amount']<3:
        usable_bag['equipped_ammo']=None
    packs=min(math.ceil(max(0,route.supplies.arrows_restock_to-counts['arrows'])/pack),
              max(0,MAX_ARROW_PACKS-arrow_pack_count(usable_bag)))
    arrows=packs*price
    healing=max(0,route.supplies.healing_restock_to-counts['potions'])*60
    from conquest.return_scroll import POLICY,TYPE
    scrolls=(max(0,2-sum(i['amount'] for i in bag['items'] if i['type_id']==TYPE))*200
             if route.restock_map_id==1002 and read_json(POLICY).get('enabled') else 0)
    budget=transport_reserve()+arrows+healing+scrolls+(3000 if route.supplies.arrow_type!=1050000 and arrows else 0)
    if level is not None and arrow_pack_count(bag)<MAX_ARROW_PACKS:
        from conquest.arrow_upgrades import preferred_arrow,ARROW_LEVELS
        best=preferred_arrow(level)
        upgrade=next((p for p in products if p['type_id']==best and 0<p['price']
                      and 1<=p.get('level',0)<=level),None)
        if upgrade and ARROW_LEVELS[best]>ARROW_LEVELS.get(kind,0):
            budget=max(budget,transport_reserve()+healing+scrolls+upgrade['price']+3000)
    return budget


def open_warehouse(loop):
    life=loop.living()['embedded_controls']['life']
    from conquest.memory_npcs import TOWN_VENDORS
    known=next((v for v in TOWN_VENDORS if v.map_id==life['map_id'] and v.name=='Warehouseman'),None)
    position=known.position if known else tuple(loop.town('warehouse-locate')['position'])
    if not loop.town('vendor-status',vendor_type=0).get('reachable'):
        candidates=[(position[0]+dx,position[1]+dy) for dx in range(-4,5) for dy in range(-4,5)
                    if (dx or dy) and loop.terrain.walkable((position[0]+dx,position[1]+dy))]
        if not candidates:raise ValueError('No walkable approach to Warehouseman')
        target=min(candidates,key=lambda p:abs(p[0]-life['position'][0])+abs(p[1]-life['position'][1]))
        loop.travel(target,activity='Heading to Warehouseman for silver banking',vendor_type=0)
    loop.town('warehouse-locate')  # Re-identify the current NPC ID at arrival.
    for attempt in range(3):
        # A nearby shop can cover the bank's projected target and intercept
        # mouse motion. Closing Shop is memory-verified and does not submit
        # any sale; leave an already-open Warehouse/Inventory pair intact.
        loop.town('close',window='Shop')
        try:
            loop.town('open-bank')
            break
        except ValueError as error:
            if str(error)!='Warehouse opening unverified; no repeat input issued' or attempt==2:
                raise
            # Opening a panel transfers nothing. The next open-bank call first
            # checks fresh memory and sends no click if it is already open.
            loop.record('warehouse_open_retry',attempt=attempt+1,
                        activity='Rechecking Warehouseman interaction')
            # The failed NPC click can open a neighbouring shop. Remove its
            # panels before the alternate bank target, not after 80 attempts
            # to observe a scene pointer hidden behind the shop UI.
            loop.town('close',window='Shop')
            loop.town('close',window='Inventory')
            if attempt==0 and life['map_id']==1036:
                # At the crowded Market frontage, the higher click can select
                # the nearby surgeon. The lower NPC click was verified against
                # Warehouse inventory memory. It only opens a panel; the next
                # open-bank still verifies the warehouse before any transfer.
                try:loop.town('service-close-panel',window='Dialog')
                except ValueError as close_error:
                    if not any(t in str(close_error) for t in ('not active','absent')):raise
                loop.town('warehouse-open')
            if attempt==1 and life['map_id']==1036:
                # A projected in-range click can hit the adjacent shop at the
                # edge of the viewport. Only after two verified open failures,
                # take one checked closer approach without the range shortcut.
                fresh=loop.living()['embedded_controls']['life']
                current=tuple(fresh['position'])
                if max(abs(a-b) for a,b in zip(current,position))>6:
                    nearby=[(position[0]+dx,position[1]+dy) for dx in range(-4,5) for dy in range(-4,5)
                        if (dx or dy) and loop.terrain.walkable((position[0]+dx,position[1]+dy))]
                    if not nearby:raise ValueError('No checked closer warehouse approach')
                    target=min(nearby,key=lambda p:(max(abs(a-b) for a,b in zip(p,current)),p))
                    loop.record('warehouse_closer_approach',source=current,destination=target,
                        activity='Moving closer to Warehouseman after the neighbouring shop intercepted the click')
                    loop.travel(target,arrival_radius=1,activity='Moving closer to open Market warehouse')
            time.sleep(.5)
    return loop.town('warehouse-money')


def close_warehouse(loop):
    loop.town('close',window='Warehouse');loop.town('close',window='Inventory')


def transfer(loop,direction,amount):
    before=loop.town('warehouse-money')
    bag=loop.town('supplies')
    try:
        receipt=loop.town('warehouse-money-'+direction,amount=amount)
    except ValueError as error:
        # money_points raises this only before the transfer button is pressed.
        # Reopening resets transient grid/font state; it never authorizes a
        # retry after an uncertain money submission.
        if str(error)!='Warehouse money control geometry differs from renderer profile':raise
        loop.record('warehouse_money_layout_retry',
            activity='Refreshing warehouse controls before silver banking')
        close_warehouse(loop)
        fresh=open_warehouse(loop)
        current=loop.town('supplies')
        if (any(fresh.get(k)!=before.get(k) for k in ('silver','stored_silver'))
                or any(current.get(k)!=bag.get(k) for k in ('items','equipped_ammo','capacity','silver'))):
            raise ValueError('Warehouse state changed during layout recovery; no transfer retried')
        receipt=loop.town('warehouse-money-'+direction,amount=amount)
    if receipt.get('verified') is not True:raise ValueError('Bank transfer receipt is unverified')
    event={'time':time.time(),**receipt}
    LEDGER.parent.mkdir(parents=True,exist_ok=True)
    with LEDGER.open('a',encoding='utf-8') as out:out.write(json.dumps(event)+'\n')
    write_json(STATUS,event)
    loop.record('silver_'+direction,**receipt,
        activity=f'{"Deposited" if direction=="deposit" else "Withdrew"} {amount:,} silver at Warehouseman')
    return receipt


def fund_restock(loop):
    if not policy().get('enabled'):return
    if not policy().get('withdraw_essentials',True):return ensure_transport(loop)
    bag=loop.town('supplies')
    from conquest.savings import configure_route
    loop.route=configure_route(loop.route,bag['silver']+read_json(STATUS).get('stored_silver',0))
    from conquest.savings import savings_plan
    level=None if savings_plan() else loop.town('gear').get('level')
    if bag['silver']>=shopping_budget(loop.route,bag,level):return
    bank=open_warehouse(loop)
    try:
        loop.route=configure_route(loop.route,bank['silver']+bank['stored_silver'])
        required=shopping_budget(loop.route,bag,level)
        amount=min(bank['stored_silver'],max(0,required-bank['silver']))
        if amount:transfer(loop,'withdraw',amount)
    finally:close_warehouse(loop)
    return True


def ensure_transport(loop,minimum=None):
    if not policy().get('enabled'):return
    required=max(transport_reserve(),minimum or 0)
    if loop.town('supplies')['silver']>=required:return
    bank=open_warehouse(loop)
    try:
        amount=min(bank['stored_silver'],max(0,required-bank['silver']))
        if amount:transfer(loop,'withdraw',amount)
    finally:close_warehouse(loop)
    return True


def after_shopping(loop):
    if not policy().get('enabled') or not policy().get('deposit_after_shopping',True):return
    bank=open_warehouse(loop)
    try:
        stash_valuables(loop,deliver=True)
        if getattr(loop,'overflow_bank_changed',False):
            bank=loop.town('warehouse-money')
            loop.overflow_bank_changed=False
        reserve=transport_reserve();excess=bank['silver']-reserve
        if excess>0:transfer(loop,'deposit',excess)
        elif excess<0 and bank['stored_silver']:
            transfer(loop,'withdraw',min(-excess,bank['stored_silver']))
    finally:
        from conquest.storage_halt import active
        from conquest.merchants.delivery_journey import pending as journey_pending
        from conquest.merchants.delivery_route import pending as trade_pending
        if not active() and not journey_pending() and not trade_pending():close_warehouse(loop)
    return True


def stash_valuables(loop,*,deliver=False):
    from conquest.town_trade import stash_candidate
    from conquest.meteor_banking import consolidate,POLICY as METEOR_POLICY
    settings=read_json(METEOR_POLICY)
    if settings.get('enabled') and settings.get('qualified'):
        # Consolidation comes before deposits: a full local bank can still
        # supply ten Meteors. Re-read both inventories after each round trip.
        while consolidate(loop,loop.town('warehouse-items')):
            loop.overflow_bank_changed=True
    if deliver:
        deposit_stash_items(loop,only_meteors=True)
        from conquest.merchants.delivery_journey import start
        from conquest.meteor_banking import completed_stored_scroll
        from conquest.merchant_loop_acceptance import cycle_pending
        scroll=None if cycle_pending() else completed_stored_scroll()
        if scroll is None:start(loop)
        else:start(loop,stored_scroll_uid=scroll)
    deposit_stash_items(loop)


def deposit_stash_items(loop,*,only_meteors=False):
    from conquest.town_trade import stash_candidate
    for item in loop.town('supplies')['items']:
        if only_meteors and item['type_id']!=1088001:continue
        if not stash_candidate(item):continue
        from conquest.storage_overflow import handle,POLICY
        if read_json(POLICY).get('overflow_enabled'):
            stored=loop.town('warehouse-items')
            if len(stored['items'])>=stored['capacity']:
                if handle(loop,stored):
                    loop.overflow_bank_changed=True
                    return
                raise ValueError('Town warehouse full; ten-Meteor packing needs a qualified exchanger')
        receipt=loop.town('warehouse-deposit',uid=item['uid'])
        if receipt.get('verified_in_warehouse') is not True:
            raise ValueError('Valuable deposit is unverified; no repeat input issued')
        loop.record('valuable_stored',**receipt,plus=item.get('plus'),
                    activity='Stored a valuable item safely in the warehouse')
