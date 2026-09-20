"""Cancel one unopened route request and reconcile it as a verified no-transfer."""
import json
import hashlib
import threading
import time
from pathlib import Path

from conquest.character_context import state_path
from conquest.discord_notify import read_json,write_json
from conquest.merchants.delivery import DeliveryTransaction,exact_items,ownership_digest
from conquest.merchants.delivery_bridge import pair
from conquest.merchants.delivery_cancel_probe import cancelled,run,unchanged
from conquest.merchants.delivery_operation import JOURNAL,clear_settled_attention,status
from conquest.merchants.journal import Journal

OUTPUT=Path('reports/merchants/reserved-request-cancellation.json')
ROUTE=Path('reports/banking/merchant-route.json')


def _same_items(left,right):
    return exact_items(left)==exact_items(right)


def _origin(intent):
    return {name:intent.get(name) for name in
            ('operation_id','town_visit_id','visit_id','farmer_profile_id')}


def _same_participants(intent,active):
    merchant=intent['merchant']
    return (active.get('merchant')==merchant['character']
            and active.get('merchant_identity')==merchant['identity']
            and active.get('merchant_uid')==merchant['character_uid']
            and _same_items(active.get('items',[]),intent['items'])
            and all(active.get(name)==intent.get(name) for name in
                    ('town_visit_id','visit_id','farmer_profile_id')))


def _source(ui):
    active=read_json(state_path(ROUTE)).get('active') or {}
    key=active.get('request_id')
    if not isinstance(key,str) or not key:
        raise ValueError('No active farmer route delivery is available for cancellation')
    journal=Journal(JOURNAL)
    with journal.db() as db:
        row=db.execute('SELECT * FROM transactions WHERE id=?',(key,)).fetchone()
    if not row or row['kind']!='farmer_delivery' or row['phase']!='uncertain':
        raise ValueError('Active route delivery is not an uncertain source operation')
    intent=json.loads(row['before_json']);character=row['character']
    if not _same_participants(intent,active):
        raise ValueError('Active route delivery differs from its source operation')
    admission=journal.delivery_admission(key)
    if (not admission or admission['character']!=character
            or admission['phase']!='transaction_started'
            or json.loads(admission['uids_json'])!=sorted(item['uid'] for item in intent['items'])
            or not admission['items_json']
            or not _same_items(json.loads(admission['items_json']),intent['items'])
            or json.loads(admission['origin_json'])!=_origin(intent)):
        raise ValueError('Delivery admission differs from its source operation')
    reservation=ui.runtime.journal.get(character,'delivery_reservation') or {}
    if (reservation.get('request_id')!=key
            or reservation.get('phase') not in ('reserved','no_transfer_reconciled')
            or (reservation.get('phase')=='no_transfer_reconciled'
                and (reservation.get('disposition') or {}).get('outcome')!='no_transfer')
            or not _same_items(reservation.get('intent',{}).get('items',[]),intent['items'])
            or any(reservation.get('intent',{}).get(role,{}).get(field)!=intent[role].get(field)
                   for role in ('farmer','merchant')
                   for field in ('identity','character_uid','character','server'))
            or _origin(reservation.get('intent',{}))!=_origin(intent)):
        raise ValueError('Reserved merchant delivery differs from its source operation')
    return journal,key,character,intent


def _idle(ui,character):
    if getattr(ui,'delivery_probe_thread',None) and ui.delivery_probe_thread.is_alive():
        raise ValueError('A delivery probe is still running')
    if any(worker.is_alive() for worker in getattr(ui,'delivery_workers',{}).values()):
        raise ValueError('A delivery worker is still running')
    control=ui.app.control.snapshot()
    if (control.get('enabled') or control.get('paused') or ui.runtime.enabled(character)
            or not ui.safe_to_yield() or ui.coordinator.owner is not None
            or ui.coordinator.manual_active() or getattr(ui.runtime,'delivery_window',None)
            or getattr(ui.runtime,'refill_window',None) or character in ui.runtime.refilling):
        raise ValueError('Cancellation requires Farming Off, a paused merchant and idle native input')
    if ui.runtime.journal.pending(character):
        raise ValueError('Merchant has an unfinished transaction')


def _eligible(ui,journal,key,character,intent):
    _idle(ui,character)
    farmer,merchant=pair(ui,character)
    unchanged(intent,farmer,merchant)
    return farmer,merchant


def _cancel_before_actions(journal,key):
    return [step for step in journal.trace(key)
            if step['stage']=='cancel_request' and step['status']=='before_action']


def _archive_terminal(output,state):
    if state.get('phase')!='aborted_verified':
        return False
    encoded=json.dumps(state,sort_keys=True,separators=(',',':'))
    archive=output.parent/'reserved-request-cancellation-audit'/(
        hashlib.sha256(encoded.encode()).hexdigest()+'.json')
    if archive.exists():
        if read_json(archive)!=state:
            raise ValueError('Historical cancellation audit differs from its terminal receipt')
        return True
    write_json(archive,state)
    if read_json(archive)!=state:
        raise ValueError('Historical cancellation audit did not persist')
    return True


def _terminal(ui,journal,key,intent,farmer,merchant):
    from conquest.merchants.sales import qualified_delivery_receipts
    sales=qualified_delivery_receipts(ui.runtime.journal,intent,merchant)
    digest=ownership_digest(intent,farmer,merchant,sales)
    terminal=[step for step in journal.trace(key)
              if step['stage']=='trade_session' and step['status']=='terminal']
    if terminal:
        payload=json.loads(terminal[-1]['payload'])
        if (payload.get('outcome')!='cancelled_unaccepted'
                or payload.get('ownership_digest')!=digest):
            raise ValueError('Cancellation terminal evidence changed')
        return digest
    journal.step(key,'trade_session','terminal',{
        'outcome':'cancelled_unaccepted','ownership_digest':digest,
        'cancelled_at':time.time()})
    return digest


def _recover(ui,journal,key,character,intent,state):
    farmer,merchant=pair(ui,character)
    if not cancelled(intent,farmer,merchant):
        raise ValueError('Cancellation remains unverified; only read-only reconciliation is available')
    digest=_terminal(ui,journal,key,intent,farmer,merchant)
    state.update(phase='cancel_verified',farmer_after=farmer,merchant_after=merchant,
                 ownership_digest=digest,verified_at=time.time(),error=None)
    write_json(state_path(OUTPUT),state)
    from types import SimpleNamespace
    from conquest.merchants.sales import qualified_delivery_receipts
    transaction=DeliveryTransaction(journal,SimpleNamespace(read_pair=lambda name:pair(ui,name)),
        sale_receipts=lambda planned,source,destination:
            qualified_delivery_receipts(ui.runtime.journal,planned,destination))
    transaction.recover(key)
    receipt=status(journal,key)
    if (not receipt or receipt.get('phase')!='aborted' or receipt.get('outcome')!='no_transfer'
            or not isinstance(receipt.get('proof_digest'),str)):
        raise ValueError('Cancelled request lacks a canonical no-transfer receipt')
    clear_settled_attention(ui,key,receipt)
    state.update(phase='aborted_verified',receipt=receipt,completed_at=time.time())
    write_json(state_path(OUTPUT),state)
    return receipt


def start(ui):
    journal,key,character,intent=_source(ui)
    output=Path(state_path(OUTPUT));previous=read_json(output)
    if previous and previous.get('request_id') not in (None,key):
        if not _archive_terminal(output,previous):
            raise ValueError('Another reserved-request cancellation needs reconciliation')
    if _cancel_before_actions(journal,key):
        # A durable cancellation boundary can never be replayed. It may only
        # finish from a fresh no-input ownership observation.
        _idle(ui,character)
        state=previous if previous else {'request_id':key,'character':character,'intent':intent}
        return {'request_id':key,'running':False,'receipt':_recover(ui,journal,key,character,intent,state),
                'reentered_read_only':True}
    _eligible(ui,journal,key,character,intent)
    state={'phase':'request_verified','kind':'cancel_reserved_request','request_id':key,
           'character':character,'intent':intent,'created_at':time.time()}
    write_json(output,state)
    def before_submit(current):
        journal.step(key,'cancel_request','before_action',{
            'request_id':key,'character':character,'intent_operation_id':intent.get('operation_id')})
    def work():
        try:
            run(ui,state,output_path=output,before_submit=before_submit)
            _recover(ui,journal,key,character,intent,state)
        except Exception as error:
            state.update(error=str(error),finished_at=time.time())
            write_json(output,state)
    ui.delivery_probe_thread=threading.Thread(target=work,daemon=True,name='cancel-reserved-request')
    ui.delivery_probe_thread.start()
    return {'started':True,'request_id':key}


def prove_request_replaced(intent,farmer,merchant):
    from conquest.merchants.delivery_cancel_probe import participants_unchanged
    participants_unchanged(intent,farmer,merchant)
    if farmer.get('trade') or merchant.get('trade') or farmer.get('request'):
        raise ValueError('A trade is still active')
    request=merchant.get('request')
    if request and request.get('participant')==farmer['character']:
        raise ValueError('Original request is still active')
    return {'outcome':'original_request_absent_no_transfer','farmer':farmer,'merchant':merchant,'verified_at':time.time()}
