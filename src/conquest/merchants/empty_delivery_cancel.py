"""Cancel an identified empty delivery window without touching offered items."""
import threading
import time
from conquest.discord_notify import write_json
from conquest.character_context import state_path
from conquest.merchants.delivery import exact_items, validate_snapshot
from conquest.merchants.delivery_bridge import pair
from conquest.merchants.booth_panel_probe import CLOSE_CODE
from conquest.merchants.memory import unpack

PATH='reports/merchants/empty-delivery-cancel.json'
CODE={0x10f0cb:'33d241b861000020488d0d7ec04b00e861cbf0ff4c8d4dd70f14f666490f7ef0488d157ec04b00498bcee85642f6ff',
      0x110339:'807dd7007516e8ec170700488bc8e8e4110700498b06498bceff5070'}

def unchanged(before, current):
    for role,other in (('farmer','merchant'),('merchant','farmer')):
        a,b=before[role],current[role]
        validate_snapshot(b,a['character'],time.time())
        if any(a[k]!=b[k] for k in ('identity','character_uid','map_id','position','silver')):
            raise ValueError('Empty-trade participant changed')
        if any(exact_items(a[k])!=exact_items(b[k]) for k in ('inventory','booth')):
            raise ValueError('Empty-trade ownership changed')
        if b.get('request'):raise ValueError('Another trade request is active')
        t=b.get('trade')
        if t and (t.get('participant')!=before[other]['character'] or
                  t.get('participant_uid')!=before[other]['character_uid'] or
                  t.get('own_items') or t.get('items') or
                  t.get('own_silver')!=0 or t.get('other_silver')!=0 or
                  t.get('accepted') is not False or t.get('other_accepted') is not False):
            raise ValueError('Only an empty unaccepted delivery may be cancelled')

def control(driver,snapshot):
    g=driver.memory.gui;s=g.session
    # Trade uses the same custom header as the qualified booth panel. Its
    # p_open false branch calls the server cancellation then closes the model.
    for rva,encoded in {**{k:CLOSE_CODE[k] for k in (0x73637,0x7367c)},**CODE}.items():
        expected=bytes.fromhex(encoded)
        if s.read_block(g.base+rva,len(expected))!=expected:
            raise ValueError('Native trade cancellation control changed')
    model=g.model(14,0x5cb328)
    if unpack(s,model+12,'<B')[0]!=1:raise ValueError('Trade model is not open')
    windows=[w for w in snapshot['windows'] if w['name']=='Trade##TradeWindow']
    if len(windows)!=1:raise ValueError('Trade window is absent or ambiguous')
    w=windows[0];x,y,width,height=w['geometry']
    point=(round(x+width-23.5),round(y+12))
    if not x<point[0]<x+width or not y<point[1]<y+height:raise ValueError('Invalid trade close geometry')
    return w,point

def start(ui,character):
    from conquest.merchants.journal import character_name
    character=character_name(character)
    if getattr(ui,'delivery_probe_thread',None) and ui.delivery_probe_thread.is_alive():
        raise ValueError('A delivery probe is still running')
    if ui.runtime.enabled(character) or not ui.safe_to_yield():
        raise ValueError('Pause merchant operations and release farmer input first')
    ui.coordinator.check()
    f,m=pair(ui,character);before={'farmer':f,'merchant':m};unchanged(before,before)
    if not m.get('trade'):raise ValueError('Merchant has no empty trade window')
    state={'phase':'prepared','character':character,'before':before,'created_at':time.time()}
    write_json(state_path(PATH),state)
    revision=ui.app.control.snapshot()['revision']
    def work():
        try:run(ui,state,revision)
        except Exception as error:
            state.update(error=str(error),finished_at=time.time());write_json(state_path(PATH),state)
    ui.delivery_probe_thread=threading.Thread(target=work,daemon=True,name='empty-delivery-cancel')
    ui.delivery_probe_thread.start()
    return {'started':True,'character':character}

def run(ui,state,revision):
    import ctypes
    from conquest.capture import CaptureUnavailable
    from conquest.desktop_runtime import physical_coordinates
    from conquest.foreground import foreground_click
    from conquest.merchants.driver import wait_hover_validation
    character=state['character'];driver=ui.runtime.controllers[character].driver
    deadline=time.monotonic()+15;cancel=threading.Event()
    def check():
        ui.coordinator.check();c=ui.app.control.snapshot()
        if (cancel.is_set() or ui.closed or ui.app.closing or c['enabled'] or c.get('paused') or c['revision']!=revision or
            ui.runtime.enabled(character) or time.monotonic()>=deadline or
            any(ctypes.windll.user32.GetAsyncKeyState(k)&0x8000 for k in (0x7a,0x7b))):
            raise CaptureUnavailable('Empty trade cancellation stopped or expired')
    ui.calibrating.add(character);ui.calibration_cancel[character]=cancel
    try:
        with ui.coordinator.lease(character),physical_coordinates():
            check();f,m=pair(ui,character);unchanged(state['before'],{'farmer':f,'merchant':m})
            w,point=control(driver,m);size=driver.target.snapshot()['client_size']
            if size!=driver.memory.gui.viewport_size():raise ValueError('Trade viewport changed')
            def guard():
                check();a,b=pair(ui,character);unchanged(state['before'],{'farmer':a,'merchant':b})
                if control(driver,b)!=(w,point):raise ValueError('Trade close control moved')
                driver.memory.gui.assert_hovered(w,'#CLOSE')
            state.update(phase='cancel_submitted',point=point);write_json(state_path(PATH),state)
            foreground_click(driver.target,*point,tuple(size),require_foreground=False,
                before_press=lambda:wait_hover_validation(guard,check))
            while True:
                check();a,b=pair(ui,character);after={'farmer':a,'merchant':b};unchanged(state['before'],after)
                if not a.get('trade') and not b.get('trade'):
                    state.update(phase='cancel_verified',after=after,verified_at=time.time());write_json(state_path(PATH),state);return
                time.sleep(.05)
    finally:ui.calibrating.discard(character)
