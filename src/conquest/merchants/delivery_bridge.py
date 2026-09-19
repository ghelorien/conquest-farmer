"""Authenticated delivery coordination; snapshots are read here, never trusted from callers."""
from conquest.character_context import farmer_name
from conquest.merchants.journal import character_name
from conquest.merchants.memory import MerchantMemory
from conquest.merchants import delivery_reservation as reservations


def pair(ui,character,*,farmer_preflight=False):
    farmer=ui.app.observer
    receiver=ui.runtime.observers.get(character)
    if farmer is None or farmer.character!=farmer_name() or receiver is None:
        raise ValueError('Delivery requires both verified connected characters')
    with farmer.lock:
        source=(MerchantMemory(farmer).read(farmer_preflight=True) if farmer_preflight
                else MerchantMemory(farmer).read())
    with receiver.lock:
        destination=ui.runtime.controllers[character].driver.read()
    return source,destination


def source_intent(key,character):
    import json
    from conquest.merchants import delivery_operation as operation
    journal=operation.Journal(operation.JOURNAL)
    with journal.db() as db:
        row=db.execute('SELECT character,kind,before_json FROM transactions WHERE id=?',(key,)).fetchone()
    if not row or row['character']!=character or row['kind']!='farmer_delivery':
        raise ValueError('Missing matching durable farmer operation')
    return json.loads(row['before_json'])


def dispatch(ui,body):
    if body=={'action':'delivery-source'}:
        farmer=ui.app.observer
        if farmer is None or farmer.character!=farmer_name():
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
    if action=='delivery-disposition':expected.add('outcome')
    if set(body)!=expected:
        raise ValueError('Unsupported delivery command arguments')
    character=character_name(body['character'])
    if action not in ('delivery-pair','delivery-reserve','delivery-ready','delivery-finish','delivery-disposition'):
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
            intent=source_intent(body['request_id'],character)
            from conquest.merchants.delivery import exact_items,exact_listings
            if exact_items(intent['items'])!=exact_items(items):
                raise ValueError('Reservation items differ from the durable source operation')
            for role,fresh in (('farmer',source),('merchant',destination)):
                if any(intent[role][k]!=fresh[k] for k in ('identity','character_uid','character','silver')):
                    raise ValueError('Reservation participant changed since source preparation')
                if exact_items(intent[role]['inventory'])!=exact_items(fresh['inventory']):
                    raise ValueError('Reservation inventory changed since source preparation')
                if exact_listings(intent[role].get('booth',[]))!=exact_listings(fresh.get('booth',[])):
                    raise ValueError('Reservation booth changed since source preparation')
                if any(intent[role].get(k)!=fresh.get(k) for k in ('trade','request')):
                    raise ValueError('Reservation trade state changed since source preparation')
            origin={k:intent[k] for k in ('operation_id','visit_id','town_visit_id','farmer_profile_id') if k in intent}
            state=reservations.reserve(journal,body['request_id'],source,destination,items,origin=origin)
        elif action=='delivery-ready':
            state=reservations.ready(journal,character,body['request_id'],source,destination)
        elif action=='delivery-disposition':
            import json
            from conquest.merchants import delivery_operation as operation
            if body['outcome'] not in ('no_transfer','partial_transfer'):
                raise ValueError('Unknown delivery disposition')
            source_journal=operation.Journal(operation.JOURNAL)
            receipt=operation.status(source_journal,body['request_id'])
            if not receipt or receipt['character']!=character:
                raise ValueError('Disposition requires the matching durable source operation')
            trace=[{**step,'payload':json.loads(step['payload'])}
                   for step in source_journal.trace(body['request_id']) if step['stage']!='transaction']
            result=reservations.disposition(journal,character,body['request_id'],source,destination,
                trace=trace,intent=source_intent(body['request_id'],character))
            if result['outcome']!=body['outcome']:
                raise ValueError('Fresh bilateral evidence has a different disposition')
            return {'request_id':body['request_id'],**result}
        else:
            state=reservations.finish(journal,character,body['request_id'],source,destination)
    return {'request_id':state['request_id'],'phase':state['phase'],
            'uids':[item['uid'] for item in state['intent']['items']]}
