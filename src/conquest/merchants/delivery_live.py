"""Explicit staged live delivery qualification through the authenticated UI."""
import importlib
import threading
import time
from conquest.discord_notify import read_json,write_json
from conquest.merchants.delivery_probe import JOURNAL


def start(ui,stage):
    if stage not in ('accept','offer','confirm'):raise ValueError('Unknown delivery qualification stage')
    if getattr(ui,'delivery_probe_thread',None) and ui.delivery_probe_thread.is_alive():
        raise ValueError('Delivery qualification is running')
    from conquest.merchants.farmer_preferences import permits_new_delivery
    from conquest.merchants.farmer_identity import ui_character
    permits_new_delivery(ui_character(ui));ui.coordinator.check()
    if not ui.safe_to_yield():raise ValueError('Farmer input has not been released')
    state=read_json(JOURNAL)
    if state.get('phase')!={'accept':'request_verified','offer':'trade_open_verified','confirm':'offer_verified'}[stage]:
        raise ValueError('Reconcile the previous delivery stage first')
    module=importlib.import_module('conquest.merchants.delivery_'+stage+'_probe')
    def work():
        try:module.run(ui,state)
        except Exception as error:
            state.update(error=str(error),finished_at=time.time());write_json(JOURNAL,state)
    ui.delivery_probe_thread=threading.Thread(target=work,name='delivery-'+stage+'-probe',daemon=True)
    ui.delivery_probe_thread.start()
    return {'started':True,'stage':stage}
