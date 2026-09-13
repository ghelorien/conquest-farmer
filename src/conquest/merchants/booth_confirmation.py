"""Native Open Booth confirmation, qualified by its captured flag identity."""
import struct
import time
from conquest.merchants.memory import string,unpack
from conquest.merchants.qualification import stock
from conquest.merchants.stalls import vacant_flags,owned_booth
from conquest.capture import CaptureUnavailable

CONTROL={'mode':'native_open_booth','revision':1}
CODE={0x95fd6:'e835dcfaff',0x95fdf:'b201488bcbe857070000',
      0xd8b90:'0fb6124883c108e914d9ffff',
      0xd64cc:'0fb644246885c00f8489000000ba10000000488d4c2438e828f9f9ffe893c00b004889442428488b4424608b00894424204533c9448b442420488d542438488b4c2428e8dce20b00'}


def control(driver,snapshot,flag):
    g=driver.memory.gui;s=driver.observer.adapter
    model=g.model(15,0x5c4f30)
    if unpack(s,model+12,'<B')[0]!=1 or [string(s,model+o) for o in (0x48,0x68,0x88,0xa8)]!=[
            'Open Booth###Confirm','Start Vending','Yes','No']:
        raise ValueError('Expected native Open Booth confirmation')
    for rva,code in CODE.items():
        expected=bytes.fromhex(code)
        if s.read_block(g.base+rva,len(expected))!=expected:raise ValueError('Open Booth handler changed')
    callback=unpack(s,model+0x100,'<Q')[0]
    if (callback!=model+0xc8 or unpack(s,callback,'<Q')[0]!=g.base+0x5c7ca0
            or unpack(s,callback+8,'<I')[0]!=flag['uid']
            or unpack(s,g.base+0x5c7cb0,'<Q')[0]!=g.base+0xd8b90):
        raise ValueError('Open Booth callback does not identify the selected flag')
    windows=[w for w in snapshot['windows'] if w['name'].endswith('###Confirm')]
    if len(windows)!=1 or windows[0]['name']!='Open Booth###Confirm':
        raise ValueError('Open Booth confirmation is absent or ambiguous')
    w=windows[0];raw=s.read_block(w['address'],0x250)
    x,y,width,height=w['geometry'];end_x,button_y=struct.unpack_from('<2f',raw,0xe8)
    line=struct.unpack_from('<f',raw,0x114)[0]
    if width!=200 or not 100<=height<=400 or line!=18 or end_x!=x+width-8:
        raise ValueError('Open Booth button layout changed')
    point=(round(x+width/2),round(button_y-22+line/2))
    if not x<point[0]<x+width or not y<point[1]<y+height:raise ValueError('Open Booth button outside dialog')
    return w,point


def submit(driver,travel,before,flag,check,before_press):
    from conquest.merchants.driver import wait_hover_validation
    spec=driver.require_qualified('stall_occupancy')['shop_setup']
    def fresh():
        check();now=driver.memory.read()
        if (any(now[k]!=before[k] for k in ('identity','character_uid','map_id','position','silver'))
                or now['map_id']!=1036 or stock(now)!=stock(before)
                or now.get('own_booth_uid') or now.get('trade') or now.get('request')):
            raise ValueError('Merchant or stock changed before opening booth')
        candidate=next((f for f in vacant_flags(driver.observer,spec) if f['uid']==flag['uid']),None)
        if candidate is None or candidate['position']!=flag['position']:
            raise ValueError('Selected stall is no longer verified vacant')
        return now
    w,point=control(driver,fresh(),flag)
    def verify():
        if control(driver,fresh(),flag)!=(w,point):raise ValueError('Open Booth confirmation changed')
        driver.memory.gui.assert_hovered(w,'Yes')
    def press():
        wait_hover_validation(verify,check);before_press()
    travel.click(point,check,before_press=press)
    until=time.monotonic()+5
    while time.monotonic()<until:
        check();after=driver.memory.read()
        if any(after[k]!=before[k] for k in ('identity','character_uid','map_id','silver')) or stock(after)!=stock(before):
            raise ValueError('Booth claim needs stock reconciliation')
        if after.get('own_booth_uid'):
            booth=owned_booth(driver.observer,after)
            if booth['position']!=[flag['position'][0]+3,flag['position'][1]]:
                raise ValueError('Claimed booth belongs to another flag')
            return after
        time.sleep(.1)
    raise CaptureUnavailable('Booth confirmation submitted; reconcile before any retry')
