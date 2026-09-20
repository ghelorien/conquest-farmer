"""Confirm only the complete authorized offer, then reconcile both owners."""
import ctypes
import threading
import time
from contextlib import contextmanager
from copy import deepcopy
from conquest.capture import CaptureUnavailable
from conquest.merchants.delivery_probe import JOURNAL,read_probe,write_probe as write_json
from conquest.merchants.delivery_bridge import pair
from conquest.merchants.delivery import validate_offers,reconcile
from conquest.recovery_override import evidence_digest


def run(ui,state,*,revision=None):
    from conquest.desktop_runtime import physical_coordinates
    from conquest.foreground import foreground_click
    from conquest.merchants.memory import MerchantMemory
    from conquest.merchants.driver import wait_hover_validation
    from conquest.merchants.farmer_preferences import permits_new_delivery
    # Imported here so this stage is independently qualified after placement.
    from conquest.merchants.delivery_confirm_controls import confirm_control
    if state.get('phase') not in ('offer_verified','farmer_confirm_verified'):
        raise ValueError('Submitted confirmation is read-only; never repeat confirmation input')
    resume_merchant=state['phase']=='farmer_confirm_verified'
    receipt_digest=evidence_digest(state)
    intent=state['intent'];character=state['character']
    revision=ui.app.control.snapshot()['revision'] if revision is None else revision
    deadline=time.monotonic()+15
    def receipt():
        if evidence_digest(read_probe())!=receipt_digest:
            raise CaptureUnavailable('Delivery confirmation receipt changed')
    def manual_fence():
        if any(ui.coordinator.manual_session_blocked(owner) for owner in
               (state.get('farmer_profile_id','Farmer'),state.get('target_profile_id',character))):
            raise CaptureUnavailable('Manual visitor session holds a delivery participant')
    def check():
        permits_new_delivery(intent['farmer']['character']);ui.coordinator.check()
        manual_fence();receipt()
        c=ui.app.control.snapshot()
        if (getattr(ui,'delivery_probe_thread',None) is not threading.current_thread()
                or ui.closed or ui.app.closing or c['enabled'] or c.get('paused') or c['revision']!=revision
                or not ui.safe_to_yield() or time.monotonic()>=deadline
                or any(ctypes.windll.user32.GetAsyncKeyState(k)&0x8000 for k in (0x7a,0x7b))):
            raise CaptureUnavailable('Trade confirmation stopped or expired')
    def save(phase,**fields):
        nonlocal receipt_digest
        receipt()
        state.update(phase=phase,updated_at=time.time(),error=None,**fields);write_json(JOURNAL,state)
        receipt_digest=evidence_digest(state)
    def exact_pair(role):
        from conquest.merchants.delivery_probe_ownership import ownership
        f,m=pair(ui,character)
        ownership(state,character,ui.runtime.manual_target(character),ui.runtime.manual_target('Farmer'),
                  f,m,now=time.time())
        # Only each participant's own flag proves its confirmation. The remote
        # flag may lag; it must never cause a repeated Farmer or Merchant press.
        if (f['trade']['accepted'] is not (role=='merchant')
                or f['trade']['other_accepted'] is not False or m['trade']['accepted'] is not False
                or role=='farmer' and m['trade']['other_accepted'] is not False):
            raise ValueError('Local trade confirmation state changed; no confirmation repeated')
        receipt()
        return f,m
    def target_binding(role,observer,memory):
        """A fresh replacement's pair cannot authorize the old input target."""
        identity=deepcopy(intent[role]['identity']);adapter=observer.adapter
        target=observer.operations.target;hwnd=target.hwnd
        controller=ui.runtime.controllers[character] if role=='merchant' else None
        driver=controller.driver if controller else None
        host=ui.hosts.get(character) if controller else ui.app.host
        saved=host.saved if host else None
        def verify():
            check()
            if (observer.adapter is not adapter or adapter.identity!=identity
                    or observer.operations.target is not target or type(hwnd) is not int or hwnd<=0 or target.hwnd!=hwnd
                    or host is None or host.mode!='owned' or host.saved is not saved or not saved
                    or saved.hwnd!=hwnd or saved.identity!=identity):
                raise CaptureUnavailable('Confirmation observer or owned target changed')
            if role=='merchant':
                if (ui.runtime.controllers.get(character) is not controller or controller.driver is not driver
                        or ui.runtime.observers.get(character) is not observer or driver.observer is not observer
                        or driver.target is not target or driver.memory is not memory or ui.hosts.get(character) is not host):
                    raise CaptureUnavailable('Merchant confirmation controller changed')
            elif (ui.app.observer is not observer or ui.app.host is not host
                    or ui.app.client!=(identity['pid'],hwnd,identity)):
                raise CaptureUnavailable('Farmer confirmation controller changed')
            adapter.assert_identity();host.api.assert_owner(hwnd,identity)
        verify()
        return target,verify
    def click(role,observer,memory):
        check();f,m=exact_pair(role)
        target,native=target_binding(role,observer,memory)
        snap=f if role=='farmer' else m
        if snap['trade']['accepted']:raise ValueError('This account already confirmed; reconcile without repeating')
        w,point,seed=confirm_control(memory.gui,snap)
        size=target.snapshot()['client_size']
        if size!=memory.gui.viewport_size():raise ValueError('Native and GUI sizes differ')
        def before():
            check();a,b=exact_pair(role);native()
            current=a if role=='farmer' else b
            if current['trade']['accepted'] or confirm_control(memory.gui,current)!=(w,point,seed):
                raise ValueError('Trade confirmation changed')
            memory.gui.assert_hovered(w,'Accept Trade',seeds=[seed])
        native()
        save(role+'_confirm_submitted',confirming_role=role,confirm_point=point,
             farmer_before_confirm=f,merchant_before_confirm=m)
        foreground_click(target,*point,tuple(size),require_foreground=False,
            before_press=lambda:wait_hover_validation(before,check))
    @contextmanager
    def lease(owner):
        with ui.coordinator.lock:
            receipt()
            f,m=pair(ui,character)
            if not ui.runtime.reconcile_probe_pair(character,f,m):
                raise CaptureUnavailable('Delivery confirmation needs fresh bilateral probe reconciliation')
            manual_fence();check()
            with ui.coordinator.lease(owner,purpose='delivery_confirm_probe'),physical_coordinates():yield
    if not resume_merchant:
        with lease('Farmer'):
            from conquest.merchants.delivery_farmer_surface import prepare
            presentation=prepare(ui,state,purpose='delivery_confirm_probe',revision=revision,deadline=deadline)
            observer=ui.app.observer
            f,m=pair(ui,character);validate_offers(intent,f,m)
            if any(s['trade']['accepted'] or s['trade']['other_accepted'] for s in (f,m)):
                raise ValueError('Trade was accepted before confirmation activation')
            presentation()
            from conquest.focus_recovery import activate_client
            if not activate_client(observer.operations.target.hwnd,f['identity']):
                raise ValueError('Farmer focus unavailable; no confirmation sent')
            click('farmer',observer,MerchantMemory(observer))
            until=time.monotonic()+3
            while True:
                f,m=pair(ui,character);validate_offers(intent,f,m)
                if f['trade']['accepted']:
                    if f['trade']['other_accepted'] or m['trade']['accepted']:
                        raise ValueError('Merchant confirmation changed before its submission')
                    break
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
