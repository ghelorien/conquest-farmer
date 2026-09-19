"""Confirm only the complete authorized offer, then reconcile both owners."""
import ctypes
import threading
import time
from contextlib import contextmanager
from conquest.capture import CaptureUnavailable
from conquest.merchants.delivery_probe import JOURNAL,write_probe as write_json
from conquest.merchants.delivery_bridge import pair
from conquest.merchants.delivery import validate_offers,reconcile


def run(ui,state):
    from conquest.desktop_runtime import physical_coordinates
    from conquest.foreground import foreground_click
    from conquest.merchants.memory import MerchantMemory
    from conquest.merchants.driver import wait_hover_validation
    from conquest.merchants.farmer_preferences import permits_new_delivery
    # Imported here so this stage is independently qualified after placement.
    from conquest.merchants.delivery_confirm_controls import confirm_control
    intent=state['intent'];character=state['character'];revision=ui.app.control.snapshot()['revision']
    deadline=time.monotonic()+15
    def manual_fence():
        if any(ui.coordinator.manual_session_blocked(owner) for owner in
               (state.get('farmer_profile_id','Farmer'),state.get('target_profile_id',character))):
            raise CaptureUnavailable('Manual visitor session holds a delivery participant')
    def check():
        permits_new_delivery(intent['farmer']['character']);ui.coordinator.check()
        manual_fence()
        c=ui.app.control.snapshot()
        if (ui.closed or ui.app.closing or c['enabled'] or c.get('paused') or c['revision']!=revision
                or not ui.safe_to_yield() or time.monotonic()>=deadline
                or any(ctypes.windll.user32.GetAsyncKeyState(k)&0x8000 for k in (0x7a,0x7b))):
            raise CaptureUnavailable('Trade confirmation stopped or expired')
    def save(phase,**fields):
        state.update(phase=phase,updated_at=time.time(),error=None,**fields);write_json(JOURNAL,state)
    def click(role,observer,memory):
        check();f,m=pair(ui,character);validate_offers(intent,f,m)
        snap=f if role=='farmer' else m
        if snap['trade']['accepted']:raise ValueError('This account already confirmed; reconcile without repeating')
        w,point,seed=confirm_control(memory.gui,snap)
        target=observer.operations.target;size=target.snapshot()['client_size']
        if size!=memory.gui.viewport_size():raise ValueError('Native and GUI sizes differ')
        def before():
            check();a,b=pair(ui,character);validate_offers(intent,a,b)
            current=a if role=='farmer' else b
            if current['trade']['accepted'] or confirm_control(memory.gui,current)!=(w,point,seed):
                raise ValueError('Trade confirmation changed')
            memory.gui.assert_hovered(w,'Accept Trade',seeds=[seed])
        save(role+'_confirm_submitted',confirming_role=role,confirm_point=point,
             farmer_before_confirm=f,merchant_before_confirm=m)
        foreground_click(target,*point,tuple(size),require_foreground=False,
            before_press=lambda:wait_hover_validation(before,check))
    @contextmanager
    def lease(owner):
        with ui.coordinator.lock:
            f,m=pair(ui,character)
            if not ui.runtime.reconcile_probe_pair(character,f,m):
                raise CaptureUnavailable('Delivery confirmation needs fresh bilateral probe reconciliation')
            manual_fence()
            with ui.coordinator.lease(owner),physical_coordinates():yield
    with lease('Farmer'):
        done,result=threading.Event(),{}
        ui.ui_requests.put((ui.app.show_game,done,result))
        if not done.wait(3):
            result['expired']=True;raise ValueError('Farmer surface unavailable')
        if result.get('error'):raise ValueError(result['error'])
        observer=ui.app.observer
        f,m=pair(ui,character);validate_offers(intent,f,m)
        if not f['trade']['accepted']:
            click('farmer',observer,MerchantMemory(observer))
        until=time.monotonic()+3
        while True:
            f,m=pair(ui,character);validate_offers(intent,f,m)
            if f['trade']['accepted']:break
            if time.monotonic()>=until:raise ValueError('Farmer confirmation not verified; no second confirmation')
            time.sleep(.05)
        save('farmer_confirm_verified',farmer_after=f,merchant_after=m)
    if character in ui.calibrating:raise ValueError('Merchant has another calibration active')
    ui.calibrating.add(character);ui.calibration_cancel[character]=threading.Event()
    try:
        with lease(character):
            driver=ui.runtime.controllers[character].driver
            click('merchant',driver.observer,driver.memory)
            # Finish read-only reconciliation after an expired input deadline.
            until=time.monotonic()+5
            while True:
                f,m=pair(ui,character)
                if reconcile(intent,f,m):
                    save('delivery_verified',farmer_after=f,merchant_after=m,verified_at=time.time());return
                if time.monotonic()>=until:raise ValueError('Trade result uncertain; reconcile before retrying any transfer')
                time.sleep(.1)
    finally:
        ui.calibrating.discard(character)
