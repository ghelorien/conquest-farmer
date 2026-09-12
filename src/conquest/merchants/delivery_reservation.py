"""Hold a merchant's stock until an exact farmer batch reconciles both sides."""
import time
import json
from conquest.capture import CaptureUnavailable
from conquest.merchants.delivery import prepare,exact_items,validate_offers,reconcile

KEY='delivery_reservation'
TERMINAL=('verified','cancelled_before_input')


def active(journal,character):
    state=journal.get(character,KEY)
    return state if state and state.get('phase') not in TERMINAL else None


def reserve(journal,request_id,farmer,merchant,items,*,now=None):
    now=time.time() if now is None else now
    if not isinstance(request_id,str) or not 1<=len(request_id)<=100:
        raise ValueError('Invalid delivery reservation ID')
    character=merchant['character']
    with journal.db() as db:
        row=db.execute('SELECT state FROM delivery_reservations WHERE character=? AND request_id=?',
                       (character,request_id)).fetchone()
    if row:
        previous=json.loads(row[0])
        if (exact_items(previous['intent']['items'])!=exact_items(items)
                or previous['intent']['farmer']['character_uid']!=farmer['character_uid']
                or previous['intent']['merchant']['character_uid']!=merchant['character_uid']):
            raise ValueError('Delivery reservation ID reused for different items')
        return previous
    previous=active(journal,character)
    if previous:
        if previous['request_id']==request_id and exact_items(previous['intent']['items'])==exact_items(items):
            return previous
        raise ValueError('Reconcile the previous delivery reservation first')
    if journal.pending(character):
        raise ValueError('Merchant has an unfinished transaction')
    intent=prepare(farmer,merchant,items,now=now)
    state={'request_id':request_id,'phase':'reserved','created_at':now,'expires_at':now+120,'intent':intent}
    save(journal,character,state,creating=True)
    journal.event(character,'delivery_reserved',request_id=request_id,uids=[i['uid'] for i in items])
    return state


def save(journal,character,state,*,creating=False):
    """Persist the active hold and request receipt in the same durable commit."""
    with journal.db() as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT value FROM state WHERE character=? AND name=?',(character,KEY)).fetchone()
        current=json.loads(row[0]) if row else None
        if current and current['request_id']!=state['request_id'] and current.get('phase') not in TERMINAL:
            raise ValueError('Another delivery reservation is active')
        if not creating and (not current or current['request_id']!=state['request_id']):
            raise ValueError('Delivery reservation changed before persistence')
        if creating and db.execute('SELECT 1 FROM delivery_reservations WHERE character=? AND request_id=?',
                                   (character,state['request_id'])).fetchone():
            raise ValueError('Delivery reservation was already created; reconcile its receipt')
        encoded=json.dumps(state)
        db.execute('INSERT OR REPLACE INTO state VALUES(?,?,?)',(character,KEY,encoded))
        db.execute('INSERT OR REPLACE INTO delivery_reservations VALUES(?,?,?)',
                   (character,state['request_id'],encoded))
        if state['phase']=='verified':
            db.execute('INSERT OR REPLACE INTO state VALUES(?,?,?)',(character,'new_stock','true'))


def require(journal,character,request_id,*,now=None):
    state=active(journal,character)
    if not state or state['request_id']!=request_id:
        raise ValueError('Delivery reservation mismatch')
    now=time.time() if now is None else now
    if now>state['expires_at']:
        raise ValueError('Delivery reservation expired; reconcile before further input')
    return state


def ready(journal,character,request_id,farmer,merchant,*,now=None):
    state=require(journal,character,request_id,now=now)
    if state['phase'] not in ('reserved','offer_ready'):
        raise ValueError('Delivery cannot be made ready in this phase')
    validate_offers(state['intent'],farmer,merchant,now=now)
    state.update(phase='offer_ready',offer_verified_at=time.time() if now is None else now)
    save(journal,character,state)
    return state


def validate_receiver(journal,snapshot,*,now=None):
    state=active(journal,snapshot['character'])
    if not state:
        return None
    require(journal,snapshot['character'],state['request_id'],now=now)
    intent=state['intent'];before=intent['merchant'];trade=snapshot.get('trade')
    if (snapshot['identity']!=before['identity'] or snapshot.get('character_uid')!=before['character_uid']
            or snapshot['silver']!=before['silver']
            or exact_items(snapshot['inventory'])!=exact_items(before['inventory'])
            or exact_items(snapshot.get('booth',[]))!=exact_items(before.get('booth',[]))):
        raise ValueError('Reserved merchant inventory or identity changed')
    if not trade:
        return state
    if (trade.get('participant')!='Parasite' or trade.get('participant_uid')!=intent['farmer']['character_uid']
            or trade.get('own_silver')!=0 or trade.get('other_silver')!=0 or trade.get('own_items')):
        raise ValueError('Reserved delivery participant or currency changed')
    if state['phase']!='offer_ready':
        raise CaptureUnavailable('Waiting for the complete reserved farmer offer')
    if exact_items(trade['items'])!=exact_items(intent['items']):
        raise ValueError('Reserved delivery offer changed')
    if len(trade['items'])>snapshot['capacity']-len(snapshot['inventory']):
        raise ValueError('Reserved delivery capacity changed')
    return state


def finish(journal,character,request_id,farmer,merchant,*,now=None):
    # Reconciliation is read-only and remains permitted after the work deadline.
    with journal.db() as db:
        row=db.execute('SELECT state FROM delivery_reservations WHERE character=? AND request_id=?',
                       (character,request_id)).fetchone()
    if row and json.loads(row[0]).get('phase')=='verified':
        return json.loads(row[0])
    state=journal.get(character,KEY)
    if not state or state['request_id']!=request_id:
        raise ValueError('Delivery reservation mismatch')
    if not reconcile(state['intent'],farmer,merchant,now=now):
        raise ValueError('Both delivery inventories have not reconciled')
    if state['phase']=='verified':
        return state
    state.update(phase='verified',verified_at=time.time() if now is None else now)
    save(journal,character,state)
    journal.event(character,'delivery_reconciled',request_id=request_id,uids=[i['uid'] for i in state['intent']['items']])
    return state
