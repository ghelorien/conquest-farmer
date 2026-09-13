"""Cancel only an unchanged, empty incoming request during explicit qualification."""
import time,threading,ctypes
from conquest.capture import CaptureUnavailable
from conquest.discord_notify import write_json
from conquest.merchants.delivery_probe import JOURNAL
from conquest.merchants.delivery_bridge import pair
from conquest.merchants.delivery import exact_items,validate_snapshot
from conquest.merchants.delivery_accept_probe import control
from conquest.merchants.driver import wait_hover_validation

def unchanged(intent,farmer,merchant):
    if farmer.get('trade') or merchant.get('trade') or farmer.get('request'):
        raise ValueError('Only an unopened trade request may be cancelled')
    name=intent['farmer']['character']
    if merchant.get('request')!={'participant':name,'message':name+' wishes to trade with you.'}:
        raise ValueError('Incoming request changed')
    participants_unchanged(intent,farmer,merchant)

def participants_unchanged(intent,farmer,merchant):
    for role,current in (('farmer',farmer),('merchant',merchant)):
        before=intent[role]
        validate_snapshot(current,before['character'],time.time())
        if any(current[k]!=before[k] for k in ('identity','character_uid','silver','position')):
            raise ValueError('Request participants changed')
        if any(exact_items(current[k])!=exact_items(before[k]) for k in ('inventory','booth')):
            raise ValueError('Request stock changed')

def cancelled(intent,farmer,merchant):
    participants_unchanged(intent,farmer,merchant)
    return not any(s.get('trade') or s.get('request') for s in (farmer,merchant))

def run(ui,state):
    from conquest.desktop_runtime import physical_coordinates
    from conquest.foreground import foreground_click
    character=state['character'];intent=state['intent'];revision=ui.app.control.snapshot()['revision']
    driver=ui.runtime.controllers[character].driver;deadline=time.monotonic()+15
    cancel=threading.Event()
    def check():
        ui.coordinator.check();c=ui.app.control.snapshot()
        if (cancel.is_set() or ui.closed or ui.app.closing or c['enabled'] or c.get('paused') or c['revision']!=revision
                or time.monotonic()>=deadline
                or any(ctypes.windll.user32.GetAsyncKeyState(k)&0x8000 for k in (0x7a,0x7b))):
            raise CaptureUnavailable('Request cancellation stopped or expired')
    if ui.runtime.enabled(character):raise ValueError('Pause merchant operations before cancelling the test request')
    if character in ui.calibrating:raise ValueError('Merchant already has a calibration active')
    ui.calibrating.add(character);ui.calibration_cancel[character]=cancel
    try:
        with ui.coordinator.lease(character),physical_coordinates():
            check();f,m=pair(ui,character);unchanged(intent,f,m)
            w,accept=control(driver,m);point=(accept[0],accept[1]+22)
            size=driver.target.snapshot()['client_size'];native=driver.memory.gui.viewport_size()
            point=tuple(round(v*p/g) for v,p,g in zip(point,size,native))
            def guard():
                check();a,b=pair(ui,character);unchanged(intent,a,b)
                if control(driver,b)!=(w,accept):raise ValueError('Request dialog moved')
                driver.memory.gui.assert_hovered(w,'Cancel')
            state.update(phase='cancel_submitted',cancel_point=point);write_json(JOURNAL,state)
            foreground_click(driver.target,*point,tuple(size),require_foreground=False,
                before_press=lambda:wait_hover_validation(guard,check))
            until=time.monotonic()+3
            while time.monotonic()<until:
                a,b=pair(ui,character)
                if cancelled(intent,a,b):
                    state.update(phase='cancel_verified',farmer_after=a,merchant_after=b,verified_at=time.time());write_json(JOURNAL,state);return
                time.sleep(.05)
            raise ValueError('Request cancellation unverified')
    finally:ui.calibrating.discard(character)
