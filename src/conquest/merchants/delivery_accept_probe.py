"""Qualify native acceptance of Parasite's exact incoming trade request."""
import struct
import time
import threading
from conquest.capture import CaptureUnavailable
from conquest.discord_notify import write_json
from conquest.merchants.delivery_probe import JOURNAL
from conquest.merchants.delivery_bridge import pair
from conquest.merchants.delivery import exact_items,validate_offers
from conquest.merchants.memory import string
from conquest.character_context import farmer_name


def control(driver,snapshot):
    s=driver.observer.adapter;g=driver.memory.gui
    model=g.model(15,0x5c4f30)
    if [string(s,model+o) for o in (0x48,0x68,0x88,0xa8)]!=[
            'Trade###Confirm',f'{farmer_name()} wishes to trade with you.','Accept','Cancel']:
        raise ValueError('Confirmation is not Parasite trade acceptance')
    for rva,code in ((0x95fd6,'e835dcfaff'),(0x95fdf,'b201488bcbe857070000')):
        if s.read_block(g.base+rva,len(bytes.fromhex(code)))!=bytes.fromhex(code):
            raise ValueError('Native accept handler changed')
    windows=[w for w in snapshot['windows'] if w['name'].endswith('###Confirm')]
    if len(windows)!=1:raise ValueError('Trade confirmation window is absent or ambiguous')
    w=windows[0];raw=s.read_block(w['address'],0x250)
    x,y,width,height=w['geometry'];end_x,button_y=struct.unpack_from('<2f',raw,0xe8)
    line=struct.unpack_from('<f',raw,0x114)[0]
    if width!=200 or not 100<=height<=400 or line!=18 or end_x!=x+width-8:
        raise ValueError('Trade confirmation button layout changed')
    return w,(round(x+width/2),round(button_y-22+line/2))


def run(ui,state):
    from conquest.desktop_runtime import physical_coordinates
    from conquest.foreground import foreground_click
    from conquest.merchants.driver import wait_hover_validation
    from conquest.merchants.farmer_preferences import permits_new_delivery
    character=state['character'];intent=state['intent'];revision=ui.app.control.snapshot()['revision']
    deadline=time.monotonic()+15
    driver=ui.runtime.controllers[character].driver
    def check():
        permits_new_delivery(intent['farmer']['character']);ui.coordinator.check()
        import ctypes
        c=ui.app.control.snapshot()
        if (c['enabled'] or c.get('paused') or c['revision']!=revision or time.monotonic()>=deadline
                or any(ctypes.windll.user32.GetAsyncKeyState(k)&0x8000 for k in (0x7a,0x7b))):
            raise CaptureUnavailable('Trade qualification was stopped or expired')
    def fresh():
        check();f,m=pair(ui,character)
        if f.get('trade') or m.get('trade') or f.get('request') or m.get('request',{}).get('participant')!=farmer_name():
            raise ValueError('Expected incoming Parasite request changed')
        for role,snapshot in (('farmer',f),('merchant',m)):
            old=intent[role]
            if any(snapshot[k]!=old[k] for k in ('identity','character_uid','position','silver')) or exact_items(snapshot['inventory'])!=exact_items(old['inventory']):
                raise ValueError('Participants or inventory changed before request acceptance')
        return f,m
    # Calibration grants only this checked input window; it never enables the
    # merchant trading controller or changes its persistent permissions.
    if character in ui.calibrating:raise ValueError('Merchant has another calibration active')
    ui.calibrating.add(character);ui.calibration_cancel[character]=threading.Event()
    try:
        with ui.coordinator.lease(character),physical_coordinates():
            f,m=fresh();w,point=control(driver,m)
            size=driver.target.snapshot()['client_size']
            if size!=driver.memory.gui.viewport_size():raise ValueError('Native and GUI dimensions differ')
            def before():
                f,m=fresh()
                if control(driver,m)!=(w,point):raise ValueError('Accept control moved')
                driver.memory.gui.assert_hovered(w,'Accept')
            state.update(phase='accept_submitted',accept_point=point,error=None);write_json(JOURNAL,state)
            foreground_click(driver.target,*point,tuple(size),require_foreground=False,
                before_press=lambda:wait_hover_validation(before,check))
            while True:
                check();f,m=pair(ui,character)
                if f.get('trade') and m.get('trade'):
                    validate_offers({**intent,'items':[]},f,m)
                    state.update(phase='trade_open_verified',farmer_after=f,merchant_after=m,
                                 accepted_at=time.time());write_json(JOURNAL,state);return
                time.sleep(.1)
    finally:
        ui.calibrating.discard(character)
