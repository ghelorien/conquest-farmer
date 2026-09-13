"""Exact two-account delivery plans and durable, fail-closed reconciliation."""
from conquest.merchants.capacity import available_slots
from conquest.character_context import farmer_name
import math
import time
import uuid
import json
from conquest.merchants.journal import CHARACTERS
from conquest.merchants.controller import identities
from conquest.valuables import SPECIAL_LOOT_TYPES, DRAGONBALL_TYPES, storage_only


def eligible(item, reserved=()):
    kind, plus, slot = item.get('type_id'), item.get('plus'), item.get('slot')
    if kind == 1088001:
        return False  # Bank loose Meteors; transfer only scrolls.
    if (item.get('uid') in reserved or item.get('bound') is not False
            or type(slot) is not int or not 0<=slot<40 or storage_only(item)):
        return False
    return (kind in SPECIAL_LOOT_TYPES or
        (type(kind) is int and 100000<=kind<600000 and
         (kind%10==9 or type(plus) is int and 1<=plus<=12)))


def exact_items(items):
    result=identities(items)
    for item in items:
        if type(item.get('bound')) is not bool:
            raise ValueError('Item binding is unknown')
        result[item['uid']]=(*result[item['uid']],item['bound'])
    return result


def validate_snapshot(snapshot, character, now):
    if (snapshot.get('character')!=character or snapshot.get('server')!='America'
            or not snapshot.get('identity') or type(snapshot.get('character_uid')) is not int
            or snapshot['character_uid']<=0 or not 0<=now-snapshot.get('timestamp',0)<=5
            or snapshot.get('hp',0)<=0 or snapshot.get('map_id')!=1036
            or type(snapshot.get('silver')) is not int or snapshot['silver']<0):
        raise ValueError('Delivery requires fresh, living, identified Market participants')
    inventory=exact_items(snapshot['inventory'])
    if not 0<=len(inventory)<=snapshot['capacity']<=40:
        raise ValueError('Invalid delivery inventory capacity')
    if set(inventory)&set(exact_items(snapshot.get('booth',[]))):
        raise ValueError('Ambiguous booth and inventory ownership')
    return inventory


def plan_deliveries(farmer, merchants, *, reserved=(), now=None):
    now=time.time() if now is None else now
    validate_snapshot(farmer,farmer_name(),now)
    candidates=[]
    seen=set()
    for state in merchants:
        name=state.get('character')
        if name in seen:
            raise ValueError('Duplicate merchant capacity observation')
        seen.add(name)
        if name not in CHARACTERS or not state.get('ready'):
            continue
        snapshot=state['snapshot']
        try:
            inventory=validate_snapshot(snapshot,name,now)
        except (ValueError,KeyError,TypeError):
            continue
        distance=state.get('verified_travel_distance')
        if (not snapshot.get('booth_open') or snapshot.get('trade') or snapshot.get('request')
                or type(distance) not in (int,float) or not math.isfinite(distance) or distance<0):
            continue
        space=available_slots(snapshot)
        if space:
            candidates.append((-space,distance,name,snapshot))
    # Spend limited merchant space on urgent valuables first; residual items
    # remain with the warehouse caller after all ready merchants are exhausted.
    items=sorted((i for i in farmer['inventory'] if eligible(i,reserved)),
        key=lambda i: (0 if i['type_id'] in DRAGONBALL_TYPES else
                       1 if type(i.get('plus')) is int and i['plus']>=2 else 2))
    plans=[]
    for negative_space,distance,name,snapshot in sorted(candidates,key=lambda row:row[:3]):
        # Reserve time for request/acceptance and both confirmations inside
        # the fifteen-second handoff, even with delayed server acknowledgments.
        for offset in range(0,-negative_space,5):
            batch=items[:min(5,-negative_space-offset)]
            if not batch:
                break
            items=items[len(batch):]
            plans.append({'merchant':name,'items':batch,'merchant_identity':snapshot['identity'],
                'merchant_uid':snapshot['character_uid'],'verified_travel_distance':distance})
    return {'deliveries':plans,'warehouse':[i for i in farmer['inventory']
        if (storage_only(i) or i.get('type_id')==1088001) and i.get('slot') is not None]+items}


def prepare(farmer, merchant, items, *, now=None):
    now=time.time() if now is None else now
    name=merchant.get('character')
    if name not in CHARACTERS:
        raise ValueError('Unknown delivery recipient')
    source=validate_snapshot(farmer,farmer_name(),now)
    destination=validate_snapshot(merchant,name,now)
    offered=exact_items(items)
    if (not 1<=len(offered)<=20 or len(offered)>available_slots(merchant)
            or any(not eligible(i) for i in items)
            or any(source.get(uid)!=details for uid,details in offered.items())
            or set(offered)&set(destination) or farmer.get('trade') or merchant.get('trade')
            or farmer.get('request') or merchant.get('request')):
        raise ValueError('Delivery identity, eligibility, capacity or modal state changed')
    return {'farmer':farmer,'merchant':merchant,'items':items}


def validate_offers(intent, farmer, merchant, *, now=None):
    now=time.time() if now is None else now
    wanted=exact_items(intent['items'])
    for role,snapshot,other in (('farmer',farmer,merchant),('merchant',merchant,farmer)):
        before=intent[role]
        validate_snapshot(snapshot,before['character'],now)
        trade=snapshot.get('trade')
        if (snapshot['identity']!=before['identity'] or snapshot['character_uid']!=before['character_uid']
                or not trade or trade.get('participant')!=other['character']
                or trade.get('participant_uid')!=other['character_uid']
                or trade.get('own_silver')!=0 or trade.get('other_silver')!=0
                or snapshot['silver']!=before['silver']):
            raise ValueError('Delivery participants or currency changed')
        if exact_items(snapshot.get('booth',[]))!=exact_items(before.get('booth',[])):
            raise ValueError('Booth stock changed during delivery')
        own,received=exact_items(trade['own_items']),exact_items(trade['items'])
        if (own,received)!=((wanted,{}) if role=='farmer' else ({},wanted)):
            raise ValueError('Trade contains missing, changed or unintended items')
    # Items offered by this client may move out of its inventory into its
    # trade deque. Reconcile their union rather than assume one representation.
    source=exact_items(farmer['inventory'])
    original=exact_items(intent['farmer']['inventory'])
    if any(source.get(uid)!=details for uid,details in original.items() if uid not in wanted):
        raise ValueError('Farmer supplies changed during trade')
    if set(source)-set(original) or any(uid in source and source[uid]!=details for uid,details in wanted.items()):
        raise ValueError('Farmer inventory changed during trade')
    if exact_items(merchant['inventory'])!=exact_items(intent['merchant']['inventory']):
        raise ValueError('Merchant inventory changed during trade')
    if len(wanted)>available_slots(merchant):
        raise ValueError('Merchant capacity changed during trade')


def reconcile(intent, farmer, merchant, *, now=None):
    now=time.time() if now is None else now
    try:
        source=validate_snapshot(farmer,farmer_name(),now)
        destination=validate_snapshot(merchant,intent['merchant']['character'],now)
        for role,current in (('farmer',farmer),('merchant',merchant)):
            before=intent[role]
            if (current['character_uid']!=before['character_uid'] or current['silver']!=before['silver']
                    or current.get('trade') or current.get('request')
                    or exact_items(current.get('booth',[]))!=exact_items(before.get('booth',[]))):
                return False
        offered=exact_items(intent['items'])
        expected_source={uid:detail for uid,detail in exact_items(intent['farmer']['inventory']).items() if uid not in offered}
        expected_destination={**exact_items(intent['merchant']['inventory']),**offered}
        return source==expected_source and destination==expected_destination
    except (ValueError,KeyError,TypeError):
        return False


class DeliveryTransaction:
    """Driver input is live-qualified separately; this journal survives crashes."""
    def __init__(self, journal, driver, peer=None):
        self.journal,self.driver=journal,driver
        if peer is None:
            from conquest.merchants.delivery_peer import DeliveryPeer
            peer=DeliveryPeer()
        self.peer=peer

    def run(self, merchant, items, *, request_id=None):
        self.driver.require_qualified('farmer_delivery')
        farmer,receiver=self.driver.read_pair(merchant)
        intent=prepare(farmer,receiver,items)
        key=request_id or 'farmer-delivery:'+uuid.uuid4().hex
        if not isinstance(key,str) or not 1<=len(key)<=100:raise ValueError('Invalid delivery request ID')
        if not self.journal.begin(key,merchant,'farmer_delivery',intent):
            return self.recover(key)
        return self.execute(key,merchant,intent)

    def execute(self,key,merchant,intent):
        """Execute a newly prepared operation owned by this process once."""
        items=intent['items']
        try:
            self.peer.reserve(key,intent)
            self.driver.open_trade(intent)
            for item in items:
                self.driver.place_item(intent,item)
            farmer,receiver=self.driver.read_pair(merchant)
            validate_offers(intent,farmer,receiver)
            # Persist submission intent before the first irreversible confirm.
            self.journal.transition(key,'submitted')
            self.driver.confirm(intent)
            # Release farmer input before the receiver confirms. A receiver
            # waiting for completion while holding input would prevent the
            # farmer's confirmation and deadlock both accounts.
            self.peer.ready(key,intent)
            farmer,receiver=self.driver.wait_pair(merchant)
            if not reconcile(intent,farmer,receiver):
                raise ValueError('Delivery result is uncertain; both inventories must reconcile')
            self.journal.transition(key,'verified',{'items':items,'farmer':farmer,'merchant':receiver})
        except Exception:
            self.journal.transition(key,'uncertain')
            raise
        # A lost release acknowledgement cannot undo a verified transfer.
        # Retrying this read-only command after restart must never send items.
        self.peer.finish(key,intent)
        return key

    def recover(self,key):
        with self.journal.db() as db:
            row=db.execute('SELECT * FROM transactions WHERE id=?',(key,)).fetchone()
        if not row or row['kind']!='farmer_delivery' or row['phase']=='aborted':
            raise ValueError('Unknown recoverable farmer delivery')
        intent=json.loads(row['before_json'])
        if row['phase']!='verified':
            farmer,merchant=self.driver.read_pair(row['character'])
            if not reconcile(intent,farmer,merchant):
                raise ValueError('Delivery remains uncertain; no new input is authorized')
            self.journal.transition(key,'verified',{'items':intent['items'],'farmer':farmer,'merchant':merchant})
        self.peer.finish(key,intent)
        return key
