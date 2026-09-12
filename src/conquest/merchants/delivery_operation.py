"""Asynchronous native delivery operations with durable source-side receipts."""
import json
from pathlib import Path
import threading
import sqlite3
from conquest.discord_notify import read_json
from conquest.merchants.journal import Journal,character_name
from conquest.merchants.delivery import DeliveryTransaction,prepare
from conquest.merchants.farmer_trade import FarmerTradeDriver

JOURNAL=Path('reports/banking/merchant-deliveries.sqlite3')


def guard_reload():
    from conquest.merchants.delivery_route import pending
    if pending():raise ValueError('Reconcile the pending farmer delivery before reloading')
    if not JOURNAL.exists():return
    with sqlite3.connect(JOURNAL.resolve().as_uri()+'?mode=ro',uri=True,timeout=2) as db:
        if db.execute("SELECT 1 FROM transactions WHERE kind='farmer_delivery' AND phase NOT IN ('verified','aborted') LIMIT 1").fetchone():
            raise ValueError('Reconcile the pending farmer delivery before reloading')


def status(journal,key):
    with journal.db() as db:
        row=db.execute('SELECT * FROM transactions WHERE id=?',(key,)).fetchone()
    if not row:return None
    before=json.loads(row['before_json'])
    return {'request_id':key,'character':row['character'],'phase':row['phase'],
            'uids':[item['uid'] for item in before['items']]}


def dispatch(ui,body):
    if body=={'action':'delivery-readiness'}:
        try:
            FarmerTradeDriver(ui).require_qualified()
        except (ValueError,OSError,AttributeError):
            return {'qualified':False}
        return {'qualified':True}
    action=body.get('action');expected={'action','request_id'}
    if action=='delivery-start':expected|={'character','uids'}
    if action not in ('delivery-start','delivery-status') or set(body)!=expected:
        raise ValueError('Unsupported native delivery command')
    key=body['request_id']
    if not isinstance(key,str) or not 1<=len(key)<=100:raise ValueError('Invalid delivery request ID')
    journal=Journal(JOURNAL)
    if not hasattr(ui,'delivery_workers'):
        ui.delivery_workers={};ui.delivery_errors={}
    old=status(journal,key)
    worker=ui.delivery_workers.get(key)
    running=bool(worker and worker.is_alive())
    if action=='delivery-status':
        return {'request_id':key,'running':running,'receipt':old,'error':ui.delivery_errors.get(key)}
    character=character_name(body['character']);uids=body['uids']
    if (not isinstance(uids,list) or not 1<=len(uids)<=20
            or any(type(uid) is not int or uid<=0 for uid in uids) or len(set(uids))!=len(uids)):
        raise ValueError('Select distinct delivery item UIDs')
    if old and (old['character']!=character or sorted(old['uids'])!=sorted(uids)):
        raise ValueError('Delivery request ID reused for another batch')
    if running:return {'request_id':key,'running':True,'receipt':old}
    if not old:
        policy=read_json('profiles/merchant-deliveries.json')
        if not policy.get('enabled') or not policy.get('parity_verified'):
            raise ValueError('Merchant delivery rollout is not enabled')
        driver=FarmerTradeDriver(ui)
        driver.require_qualified();driver.check()
        if not ui.runtime.enabled(character):raise ValueError('Merchant trading is paused')
        receiver=ui.runtime.controllers.get(character)
        if receiver is None:raise ValueError('Delivery recipient is not connected')
        receiver.driver.require_qualified('trade_request')
        receiver.driver.require_qualified('trade')
        f,m=driver.read_pair(character)
        items=[item for item in f['inventory'] if item['uid'] in uids]
        if len(items)!=len(uids):raise ValueError('Selected delivery items are not carried')
        intent=prepare(f,m,items)
        # Persist before returning an accepted asynchronous request. A crash
        # before the worker starts cannot authorize blind re-entry.
        if not journal.begin(key,character,'farmer_delivery',intent):
            raise ValueError('Delivery is already prepared; poll its status before recovery')
    else:
        from types import SimpleNamespace
        from conquest.merchants.delivery_bridge import pair
        driver=SimpleNamespace(read_pair=lambda name:pair(ui,name))
    ui.delivery_errors.pop(key,None)
    def work():
        transaction=DeliveryTransaction(journal,driver)
        try:
            if old:transaction.recover(key)
            else:transaction.execute(key,character,intent)
            attention=ui.runtime.journal.get(character,'attention',{})
            if attention.get('kind')=='farmer_delivery' and attention.get('request_id')==key:
                ui.runtime.journal.set(character,'attention',None)
        except Exception as error:
            note=str(error) if isinstance(error,(ValueError,OSError)) else 'Native delivery failed; reconcile before retrying'
            ui.delivery_errors[key]=note
            from conquest.capture import CaptureUnavailable
            if not isinstance(error,CaptureUnavailable):
                ui.runtime.journal.set(character,'attention',{'kind':'farmer_delivery','request_id':key,'note':note})
            ui.runtime.journal.event(character,'delivery_needs_reconciliation',request_id=key,note=note)
    worker=threading.Thread(target=work,daemon=True,name='farmer-delivery')
    ui.delivery_workers[key]=worker;worker.start()
    return {'request_id':key,'running':True,'receipt':status(journal,key)}
