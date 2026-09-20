"""Promote only the app's current verified staged exchange; never send input."""
from contextlib import contextmanager, ExitStack
from pathlib import Path
import time

from conquest.character_context import current, registry, merchant_directory, state_path
from conquest.discord_notify import read_json
from conquest.memory_life import CLIENT_SHA256
from conquest.merchants import delivery_probe, delivery_qualification
from conquest.merchants.delivery import reconcile
from conquest.merchants.delivery_bridge import pair
from conquest.merchants.delivery_confirm_probe import _merchant_key
from conquest.merchants.farmer_qualification import qualification_path
from conquest.recovery_override import evidence_digest


@contextmanager
def available(lock):
    if not lock.acquire(blocking=False):
        raise ValueError('Wait for the current merchant operation before qualification promotion')
    try:yield
    finally:lock.release()


def validate_candidate(observer,candidate,farmer,merchant,state):
    """The mutable layout file must still describe the live pinned client."""
    from conquest.merchants.farmer_trade import _recipient_record
    from conquest.merchants.memory import GuiReader
    from conquest.merchants.trade_controls import targeting_state
    gui_size=GuiReader(observer.adapter).viewport_size()
    if observer.operations.target.snapshot()['client_size']!=gui_size:
        raise ValueError('Trade layout requires matching native and memory GUI dimensions')
    # Promotion reads the layout only. An Inventory panel left open after the
    # completed offer need not leave this actor's point clickable.
    actor=_recipient_record(observer,{**candidate,'gui_size':gui_size},merchant)
    if any(actor.get(key)!=state.get('recipient',{}).get(key) for key in ('address','uid','name')):
        raise ValueError('Current recipient layout differs from the verified request')
    native=targeting_state(observer.adapter)
    if any(candidate.get('target_mode',{}).get(key)!=native[key] for key in ('rva','value')):
        raise ValueError('Trade target mode differs from the verified native accessor')


def promote_current(ui):
    """Resolve paths and participants in-process, without changing user intent."""
    with available(ui.coordinator.lock),ExitStack() as locks:
        state=delivery_probe.read_probe(read_only=True)
        if not state or state.get('phase')!='delivery_verified' or not state.get('verified_at'):
            raise ValueError('Promotion requires the current verified delivery receipt')
        digest=evidence_digest(state)
        character=_merchant_key(state)
        context=current();profiles=registry()
        farmer_id=context.profile.id if context else 'Farmer'
        if (profiles is not None and context is None
                or state.get('farmer_profile_id')!=farmer_id
                or context and (context.profile.role!='Farmer' or not context.profile.local_enabled)):
            raise ValueError('Delivery receipt belongs to a different farmer profile')
        observer=ui.app.observer;receiver=ui.runtime.observers.get(character)
        controller=ui.runtime.controllers.get(character)
        if observer is None or receiver is None or controller is None or controller.driver.observer is not receiver:
            raise ValueError('Both exact delivery observers must be attached before promotion')
        for account in (observer,receiver):locks.enter_context(available(account.lock))
        paths={
            'receipt':Path(state_path('reports/merchants/delivery-request-probe.json')),
            'candidate':Path(state_path('reports/merchants/trade-layout-candidate.json')),
            'farmer':qualification_path(observer,migrate=False),
            'merchant':merchant_directory(character)/'qualification.json'}
        if (delivery_probe.JOURNAL.resolve()!=paths['receipt'].resolve()
                or controller.driver.qualification.resolve()!=paths['merchant'].resolve()):
            raise ValueError('Delivery qualification paths differ from the selected profiles')
        candidate=read_json(paths['candidate'])
        if candidate.get('client_sha256')!=CLIENT_SHA256:
            raise ValueError('Trade layout build differs from the verified delivery')
        candidate_digest=evidence_digest(candidate)
        merchant_before=paths['merchant'].read_bytes()
        control=ui.app.control.snapshot()
        intent=state['intent']

        def guard():
            delivery_probe.recovery_available(ui)
            ui.coordinator.check()
            if (ui.coordinator.owner is not None or not ui.safe_to_yield()
                    or ui.app.control.snapshot()!=control or control['enabled'] or control.get('paused')
                    or getattr(ui,'closed',False) or getattr(ui.app,'closing',False)
                    or getattr(ui,'calibrating',()) or getattr(ui.runtime,'refilling',{})
                    or getattr(ui.runtime,'delivery_window',None) or getattr(ui.runtime,'refill_window',None)):
                raise ValueError('Release all delivery input before qualification promotion')
            if (ui.app.observer is not observer or ui.runtime.observers.get(character) is not receiver
                    or ui.runtime.controllers.get(character) is not controller
                    or controller.driver.observer is not receiver
                    or current()!=context or _merchant_key(state)!=character):
                raise ValueError('Delivery participant attachment or profile changed')
            if any(ui.coordinator.manual_session_blocked(owner) for owner in (farmer_id,character)):
                raise ValueError('Manual visitor session holds a delivery participant')
            if ui.runtime.journal.pending(character):
                raise ValueError('Reconcile pending merchant transactions before promotion')
            if (evidence_digest(delivery_probe.read_probe(read_only=True))!=digest
                    or evidence_digest(read_json(paths['candidate']))!=candidate_digest
                    or paths['merchant'].read_bytes()!=merchant_before):
                raise ValueError('Delivery receipt or qualification evidence changed before promotion')
            for role,account in (('farmer',observer),('merchant',receiver)):
                expected=intent[role]
                if (account.adapter.expected_sha256!=CLIENT_SHA256
                        or account.adapter.identity!=expected['identity']
                        or account.character!=expected['character']):
                    raise ValueError('Delivery participant process or build changed')
                account.adapter.assert_identity()
            farmer,merchant=pair(ui,character)
            for role,snapshot in (('farmer',farmer),('merchant',merchant)):
                expected=intent[role]
                if any(snapshot.get(key)!=expected.get(key) for key in
                       ('identity','character','character_uid','server')):
                    raise ValueError('Delivery participant identity differs from the verified receipt')
            if context and any(intent['farmer'].get(key)!=getattr(context.profile,key) for key in
                               ('character_uid','server')):
                raise ValueError('Verified farmer UID differs from its selected profile')
            if profiles:
                profile=profiles.resolve(state['target_profile_id'],role='Merchant',server='America')
                if profile.character_uid!=intent['merchant']['character_uid']:
                    raise ValueError('Verified merchant UID differs from its selected profile')
            if not reconcile(intent,farmer,merchant,now=time.time()):
                raise ValueError('Fresh bilateral ownership does not reconcile the verified delivery')
            validate_candidate(observer,candidate,farmer,merchant,state)
            # Memory reads may have overlapped a receipt replacement or Stop.
            ui.coordinator.check()
            if (evidence_digest(delivery_probe.read_probe(read_only=True))!=digest
                    or evidence_digest(read_json(paths['candidate']))!=candidate_digest
                    or paths['merchant'].read_bytes()!=merchant_before
                    or ui.app.control.snapshot()!=control):
                raise ValueError('Delivery authority changed during qualification validation')

        guard()
        # Later probes replace the current journal. Qualification provenance
        # must point at an immutable, fsynced copy of this completed exchange.
        receipt=delivery_probe.archive_probe(state)
        delivery_qualification.promote(receipt,paths['candidate'],paths['farmer'],paths['merchant'],check=guard)
        return {'promoted':True,'receipt_digest':digest,'evidence':str(receipt),
            'farmer':{'profile_id':farmer_id,'character':intent['farmer']['character'],
                'character_uid':intent['farmer']['character_uid'],'identity':intent['farmer']['identity'],
                'capabilities':{'farmer_delivery':True}},
            'merchant':{'profile_id':state['target_profile_id'],'character':str(character),
                'character_uid':intent['merchant']['character_uid'],'identity':intent['merchant']['identity'],
                'capabilities':{'trade_request':True,'trade':True}},
            'permissions_changed':False,'input_sent':False}
