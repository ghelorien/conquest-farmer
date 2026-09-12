"""Authenticated delivery coordination; snapshots are read here, never trusted from callers."""
from conquest.merchants.journal import character_name
from conquest.merchants.memory import MerchantMemory
from conquest.merchants import delivery_reservation as reservations


def pair(ui,character):
    farmer=ui.app.observer
    receiver=ui.runtime.observers.get(character)
    if farmer is None or farmer.character!='Parasite' or receiver is None:
        raise ValueError('Delivery requires both verified connected characters')
    with farmer.lock:
        source=MerchantMemory(farmer).read()
    with receiver.lock:
        destination=ui.runtime.controllers[character].driver.read()
    return source,destination


def dispatch(ui,body):
    if body=={'action':'delivery-source'}:
        farmer=ui.app.observer
        if farmer is None or farmer.character!='Parasite':
            raise ValueError('Delivery source must be the verified farmer')
        with farmer.lock:
            return {'farmer':MerchantMemory(farmer).read(farmer_preflight=True)}
    if body.get('action')=='delivery-reserve':
        if not ui.coordinator.lock.acquire(blocking=False):
            raise ValueError('Wait for the current input action before reserving a delivery')
        try:
            if ui.coordinator.owner is not None:
                raise ValueError('Wait for the current input action before reserving a delivery')
            return _dispatch(ui,body)
        finally:
            ui.coordinator.lock.release()
    return _dispatch(ui,body)


def _dispatch(ui,body):
    action=body.get('action')
    expected={'action','character'}
    if action!='delivery-pair':expected.add('request_id')
    if action=='delivery-reserve':expected.add('uids')
    if set(body)!=expected:
        raise ValueError('Unsupported delivery command arguments')
    character=character_name(body['character'])
    if action not in ('delivery-pair','delivery-reserve','delivery-ready','delivery-finish'):
        raise ValueError('Unknown delivery operation')
    if action in ('delivery-reserve','delivery-ready'):
        if not ui.runtime.enabled(character) or ui.coordinator.stopped:
            raise ValueError('Merchant delivery acceptance is paused')
        if ui.app.control.snapshot()['enabled']:
            raise ValueError('Stop combat before preparing a delivery')
        driver=ui.runtime.controllers.get(character)
        if driver is None:raise ValueError('Merchant is not attached')
        driver.driver.require_qualified('trade_request')
        driver.driver.require_qualified('trade')
    source,destination=pair(ui,character)
    if action=='delivery-pair':
        return {'farmer':source,'merchant':destination}
    journal=ui.runtime.journal
    with ui.runtime.lock:
        if action=='delivery-reserve':
            uids=body['uids']
            if (not isinstance(uids,list) or not 1<=len(uids)<=20
                    or any(type(uid) is not int or uid<=0 for uid in uids) or len(set(uids))!=len(uids)):
                raise ValueError('Select 1–20 distinct carried item UIDs')
            items=[item for item in source['inventory'] if item['uid'] in uids]
            if len(items)!=len(uids):raise ValueError('Delivery items are no longer carried')
            state=reservations.reserve(journal,body['request_id'],source,destination,items)
        elif action=='delivery-ready':
            state=reservations.ready(journal,character,body['request_id'],source,destination)
        else:
            state=reservations.finish(journal,character,body['request_id'],source,destination)
    return {'request_id':state['request_id'],'phase':state['phase'],
            'uids':[item['uid'] for item in state['intent']['items']]}
