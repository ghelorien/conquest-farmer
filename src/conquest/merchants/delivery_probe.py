"""Explicit live qualification of a farmer trade request; no item confirmation."""
from pathlib import Path
import threading
import time

from conquest.capture import CaptureUnavailable
from conquest.discord_notify import read_json,write_json
from conquest.merchants.delivery import eligible,prepare,exact_items
from conquest.merchants.delivery_bridge import pair
from conquest.merchants.journal import character_name

from conquest.character_context import state_path, farmer_name
JOURNAL=Path(state_path('reports/merchants/delivery-request-probe.json'))


def unchanged(intent,farmer,merchant):
    prepare(farmer,merchant,intent['items'])
    for role,current in (('farmer',farmer),('merchant',merchant)):
        old=intent[role]
        if (current['identity']!=old['identity'] or current['character_uid']!=old['character_uid']
                or current['position']!=old['position'] or current['silver']!=old['silver']
                or exact_items(current['inventory'])!=exact_items(old['inventory'])
                or exact_items(current['booth'])!=exact_items(old['booth'])):
            raise ValueError('Trade probe participants, stock or position changed')


def start(ui,character):
    from conquest.merchants.farmer_preferences import permits_new_delivery
    from conquest.merchants.farmer_identity import ui_character
    permits_new_delivery(ui_character(ui))
    character=character_name(character)
    if getattr(ui,'delivery_probe_thread',None) and ui.delivery_probe_thread.is_alive():
        raise ValueError('Trade request probe is running')
    if read_json(JOURNAL).get('phase') in ('targeting_submitted','targeting_verified','request_submitted','request_verified'):
        raise ValueError('Reconcile existing trade request probe before further input')
    ui.coordinator.check()
    if not ui.safe_to_yield() or ui.app.control.snapshot()['enabled']:
        raise ValueError('Trade probe requires stopped farming and released input')
    f,m=pair(ui,character)
    intent=prepare(f,m,[i for i in f['inventory'] if eligible(i)])
    if max(abs(a-b) for a,b in zip(f['position'],m['position']))>12:
        raise ValueError('Approach the memory-identified merchant before the trade probe')
    revision=ui.app.control.snapshot()['revision']
    state={'phase':'prepared','character':character,'intent':intent,'started_at':time.time()}
    write_json(JOURNAL,state)
    def work():
        try:run(ui,intent,revision,state)
        except Exception as error:
            state.update(error=str(error),finished_at=time.time())
            write_json(JOURNAL,state)
    ui.delivery_probe_thread=threading.Thread(target=work,name='delivery-request-probe',daemon=True)
    ui.delivery_probe_thread.start()
    return {'started':True,'character':character,'uids':[i['uid'] for i in intent['items']]}


def run(ui,intent,revision,state):
    from conquest.desktop_runtime import physical_coordinates
    from conquest.foreground import foreground_click
    from conquest.memory_shop import MemoryGui
    from conquest.merchants.memory import MerchantMemory
    from conquest.merchants.driver import wait_hover_validation
    from conquest.merchants.trade_controls import trade_button,targeting_state
    from conquest.merchants.farmer_trade import recipient_record
    import ctypes
    deadline=time.monotonic()+15
    observer=ui.app.observer;memory=MerchantMemory(observer)
    character=intent['merchant']['character']
    def check():
        from conquest.merchants.farmer_preferences import permits_new_delivery
        from conquest.merchants.farmer_identity import ui_character
        permits_new_delivery(intent['farmer']['character'])
        ui.coordinator.check()
        control=ui.app.control.snapshot()
        if (ui.closed or ui.app.closing or control['enabled'] or control.get('paused')
                or control['revision']!=revision or not ui.safe_to_yield()
                or time.monotonic()>=deadline
                or ctypes.windll.user32.GetAsyncKeyState(0x7a)&0x8000
                or ctypes.windll.user32.GetAsyncKeyState(0x7b)&0x8000):
            raise CaptureUnavailable('Trade request probe stopped or expired')
    def save(phase,**fields):
        state.update(phase=phase,updated_at=time.time(),**fields);write_json(JOURNAL,state)
    with ui.coordinator.lease('Farmer'),physical_coordinates():
        done,result=threading.Event(),{}
        ui.ui_requests.put((ui.app.show_game,done,result))
        if not done.wait(3):
            result['expired']=True
            raise ValueError('Farmer window did not become available')
        if result.get('error'):raise ValueError(result['error'])
        check()
        size=observer.operations.target.snapshot()['client_size']
        gui=memory.gui.viewport_size()
        if size!=gui:raise ValueError('Trade probe requires matching native and GUI dimensions')
        profile=read_json(state_path('reports/merchants/trade-layout-candidate.json'))
        profile={**profile,'gui_size':gui}
        if profile.get('client_sha256')!=observer.adapter.expected_sha256:
            raise ValueError('Trade layout build changed')
        f,m=pair(ui,character);unchanged(intent,f,m)
        recipient=recipient_record(observer,profile,m)
        point=trade_button(MemoryGui(observer.adapter))
        if targeting_state(observer.adapter)['current']!=16:
            raise ValueError('Farmer already has a targeting action active')
        def before_hud():
            check()
            f,m=pair(ui,character);unchanged(intent,f,m)
            if trade_button(MemoryGui(observer.adapter))!=point:
                raise ValueError('Trade button moved')
            window=next(w for w in f['windows'] if w['name']=='##Control')
            # The native HUD table pushes its ID before emitting Trade.
            # trade_button above verifies this exact live six-column table.
            memory.gui.assert_hovered(window,'Trade',seeds=[0x02a99238])
        save('targeting_submitted',hud_point=point)
        foreground_click(observer.operations.target,*point,tuple(size),require_foreground=True,
            before_press=lambda:wait_hover_validation(before_hud,check))
        until=time.monotonic()+1
        while not targeting_state(observer.adapter)['targeting_trade']:
            check()
            if time.monotonic()>=until:raise ValueError('Trade targeting transition not verified')
            time.sleep(.03)
        save('targeting_verified')
        recipient=recipient_record(observer,profile,m,targeting=True)
        def before_peer():
            check()
            f,m=pair(ui,character);unchanged(intent,f,m)
            if recipient_record(observer,profile,m,targeting=True)!=recipient:
                raise ValueError('Trade recipient changed before request')
        save('request_submitted',recipient=recipient)
        foreground_click(observer.operations.target,*recipient['point'],tuple(size),require_foreground=True,
                         before_press=before_peer)
        while True:
            check()
            f,m=pair(ui,character)
            if m.get('request'):
                if m['request'].get('participant')!=farmer_name():
                    raise ValueError('Merchant received a different trade request')
                save('request_verified',farmer_after=f,merchant_after=m)
                return
            time.sleep(.1)
