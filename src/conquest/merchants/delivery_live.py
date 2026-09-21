"""Explicit staged live delivery qualification through the authenticated UI."""
import importlib
import threading
import time
from conquest.merchants.delivery_probe import JOURNAL,read_probe,write_probe as write_json


def start(ui,stage):
    if stage=='cancel-reserved-request':
        from conquest.merchants.cancel_reserved_request import start
        return start(ui)
    if stage not in ('accept','offer','confirm','cancel'):raise ValueError('Unknown delivery qualification stage')
    if getattr(ui,'delivery_probe_thread',None) and ui.delivery_probe_thread.is_alive():
        raise ValueError('Delivery qualification is running')
    from conquest.merchants.farmer_preferences import permits_new_delivery
    from conquest.merchants.farmer_identity import ui_character
    permits_new_delivery(ui_character(ui));ui.coordinator.check()
    if not ui.safe_to_yield():raise ValueError('Farmer input has not been released')
    # Complete any durable operator disposition before considering input.
    # A crash after override-intent persistence leaves the original phase on
    # disk; reading that phase directly could restart accept/confirm input.
    state=read_probe()
    phases={'accept':{'request_verified'},'cancel':{'request_verified'},
            'offer':{'trade_open_verified'},'confirm':{'offer_verified','farmer_confirm_verified'}}
    if not state or state.get('phase') not in phases[stage]:
        raise ValueError('Reconcile the previous delivery stage first')
    revision=ui.app.control.snapshot()['revision'] if stage=='confirm' else None
    module=importlib.import_module('conquest.merchants.delivery_'+stage+'_probe')
    def work():
        try:
            if stage=='confirm':module.run(ui,state,revision=revision)
            else:module.run(ui,state)
        except Exception as error:
            # A receipt replacement revokes this worker. Never overwrite that
            # newer incident while recording the obsolete worker's failure.
            from conquest.recovery_override import evidence_digest
            if evidence_digest(read_probe())==evidence_digest(state):
                state.update(error=str(error),finished_at=time.time());write_json(JOURNAL,state)
    ui.delivery_probe_thread=threading.Thread(target=work,name='delivery-'+stage+'-probe',daemon=True)
    ui.delivery_probe_thread.start()
    return {'started':True,'stage':stage}
