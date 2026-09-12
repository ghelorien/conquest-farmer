"""Bounded native booth-control check: enter a test price, then cancel.

Runs inside the elevated desktop app with its normal input coordinator. It
never confirms a listing, removes booth stock, accepts a trade, or changes
merchant/farmer enablement. All coordinates come from pinned live GUI memory.
"""
import json
from pathlib import Path
import struct
import time

from conquest.merchants.controller import identities
from conquest.merchants.driver import MerchantDriver,booth_dialog_ready
from conquest.merchants.memory import unpack
from conquest.memory_life import CLIENT_SHA256


def stock(snapshot):
    return (snapshot['identity'], identities(snapshot['inventory']),
            identities(snapshot['booth']),
            {i['uid']:i['price'] for i in snapshot['booth']},snapshot['silver'])


def window(snapshot, name):
    matches = [w for w in snapshot['windows'] if w['name']==name]
    if len(matches)!=1:
        raise ValueError(f'Open exactly one {name} window for booth verification')
    return matches[0]


def grid_control(gui, snapshot, name, label, offset):
    w = window(snapshot,name)
    table = gui.table(w,label)
    cells = table['columns']
    strides = [cells[i+1]['content_x']-cells[i]['content_x'] for i in range(len(cells)-1)]
    if strides and (min(strides)<=0 or max(strides)-min(strides)>.01):
        raise ValueError('Nonuniform merchant table columns')
    width = strides[0] if strides else cells[0]['maximum']-cells[0]['minimum']
    x,y,sx,sy = w['geometry']
    return {'window':name,'size':[sx,sy],
            'offset':[cells[0]['content_x']-x+offset[0],
                      table['outer'][1]-y+w['scroll'][1]+offset[1]],
            'columns':len(cells),'stride':[width,table['row_height']],
            'table':label,'cell_offset':list(offset)},table


def modal_controls(session, snapshot):
    w = window(snapshot,'Add Item to Booth')
    raw = session.read_block(w['address'],0x250)
    x,y,width,height = struct.unpack_from('<4f',raw,0x18)
    # Last item is Cancel, after the 120-pixel Confirm button and 8-pixel gap.
    # Read CursorPosPrevLine and PrevLineSize from this window's own layout.
    end_x,button_y = struct.unpack_from('<2f',raw,0xe8)
    button_height = struct.unpack_from('<f',raw,0x114)[0]
    start_x,start_y = struct.unpack_from('<2f',raw,0xf0)
    if ([x,y,width,height]!=list(w['geometry']) or [width,height]!=[264.,92.]
            or button_height!=18 or end_x!=x+256 or button_y!=y+66
            or [start_x,start_y]!=[x+8,y+26]
            or session.read_block(w['address']+0x18,16)!=raw[0x18:0x28]):
        raise ValueError('Price dialog layout differs from the verified client renderer')
    def spec(px,py):
        return {'window':w['name'],'size':[width,height],'offset':[px-x,py-y]}
    return {'price_field':spec(start_x+64,button_y-13),
            'confirm_listing':spec(end_x-188,button_y+9),
            'cancel_listing':spec(end_x-60,button_y+9)}


def verify_booth_controls(driver, journal, check):
    """Caller holds the character's observer lock and foreground lease."""
    check()
    before = driver.read()
    if (before.get('trade') or before.get('request') or not before['booth_open']
            or journal.pending(driver.observer.character)):
        raise ValueError('Booth verification needs an open own booth and no pending trade/transaction')
    if driver.observer.adapter.expected_sha256!=CLIENT_SHA256:
        raise ValueError('Unqualified client fingerprint')
    gui = driver.memory.gui
    inventory,inventory_table = grid_control(gui,before,'Inventory/##ItemGrid_A800F95C','##ItemTable',(20,20))
    removal,booth_table = grid_control(gui,before,'Booth/##BoothChild_FFE4633E','BoothTable',(60,39))
    if inventory['stride']!=[40.,40.] or booth_table['row_height']!=64:
        raise ValueError('Unexpected inventory or booth item spacing')
    booth = window(before,'Booth')
    controls = {'inventory_item':inventory,'remove_listing':removal,
                'booth_drop':{'window':'Booth','size':list(booth['geometry'][2:]),
                              'offset':[booth['geometry'][2]/2,booth['geometry'][3]/2]}}
    size = tuple(driver.target.snapshot()['client_size'])
    profile = {'client_sha256':CLIENT_SHA256,'character':driver.observer.character,'server':'America',
               'client_size':list(size),'gui_size':gui.viewport_size(),
               'controls':controls,'capabilities':{'booth_input':False}}
    try:
        previous = json.loads(driver.qualification.read_text(encoding='utf-8'))
        if all(previous.get(k)==profile[k] for k in ('client_sha256','character','server')):
            controls.update({k:v for k,v in previous.get('controls',{}).items() if k not in controls})
            profile['capabilities'].update({k:v for k,v in previous.get('capabilities',{}).items() if k!='booth_input'})
    except (OSError,ValueError):
        pass
    candidate = driver.qualification.with_name('qualification.candidate.json')
    candidate.parent.mkdir(parents=True,exist_ok=True)
    candidate.write_text(json.dumps(profile,indent=2),encoding='utf-8')
    probe = MerchantDriver(driver.observer,candidate,driver.coordinator)
    model = gui.model(25,0x5c27f8)
    def unchanged():
        check()
        current = driver.read()
        if stock(current)!=stock(before) or current.get('trade') or current.get('request'):
            raise ValueError('Merchant stock or trade state changed during booth verification')
        return current
    opened = before
    if not any(w['name']=='Add Item to Booth' for w in opened['windows']):
        eligible = [i for i in before['inventory'] if not i['bound']]
        if not eligible or len(before['booth'])>=32:
            raise ValueError('Open a price dialog for an unbound inventory item before verifying')
        item = eligible[0]
        source = probe.point(before,'inventory_item',item['slot'])
        destination = probe.point(before,'booth_drop')
        from conquest.focus_recovery import activate_client
        from conquest.foreground import foreground_drag
        if not activate_client(probe.target.hwnd,before['identity']):
            raise ValueError('Merchant did not receive foreground focus')
        def before_drag():
            current = unchanged()
            if (probe.point(current,'inventory_item',item['slot'])!=source
                    or probe.point(current,'booth_drop')!=destination):
                raise ValueError('Merchant layout changed before calibration drag')
        foreground_drag(probe.target,source,destination,size,before_press=before_drag)
        opened = probe.wait_for(booth_dialog_ready,check)
        if unpack(driver.observer.adapter,model+0x50,'<I')[0]!=item['uid']:
            raise ValueError('Calibration drag selected another item; leave the dialog for inspection')
    uid = unpack(driver.observer.adapter,model+0x50,'<I')[0]
    if uid not in identities(before['inventory']):
        raise ValueError('Price dialog does not belong to carried inventory')
    controls.update(modal_controls(driver.observer.adapter,opened))
    candidate.write_text(json.dumps(profile,indent=2),encoding='utf-8')
    def same_dialog():
        current = unchanged()
        if (unpack(driver.observer.adapter,model+0x50,'<I')[0]!=uid
                or modal_controls(driver.observer.adapter,current)!={k:controls[k] for k in ('price_field','confirm_listing','cancel_listing')}):
            raise ValueError('Price dialog changed during calibration')
    probe.click(opened,'price_field',validate=same_dialog)
    from conquest.warehouse_money import type_amount
    same_dialog()
    type_amount(probe.target,123456,expected_size=size)
    from conquest.merchants.pricing import wait_booth_price
    wait_booth_price(lambda:driver.observer.adapter.read_block(model+0x54,12).split(b'\0')[0],123456,same_dialog)
    current = unchanged()
    probe.click(current,'cancel_listing',validate=same_dialog)
    after = probe.wait_for(lambda s:not any(w['name']=='Add Item to Booth' for w in s['windows']),check)
    if stock(after)!=stock(before) or unpack(driver.observer.adapter,model+0x50,'<I')[0]!=0:
        raise ValueError('Cancellation did not preserve stock; inspect before continuing')
    check()
    evidence = {'character':driver.observer.character,'identity':before['identity'],
                'client_sha256':CLIENT_SHA256,'timestamp':time.time(),'uid':uid,
                'test_price_verified':123456,'cancel_verified':True,'stock_unchanged':True,
                'inventory_table':inventory_table,'booth_table':booth_table,'controls':controls,
                'listing_submitted':False}
    evidence_path = candidate.with_name('booth-control-evidence.json')
    evidence_path.write_text(json.dumps(evidence,indent=2),encoding='utf-8')
    profile['evidence'] = str(evidence_path)
    profile['capabilities']['booth_input'] = True
    candidate.write_text(json.dumps(profile,indent=2),encoding='utf-8')
    candidate.replace(driver.qualification)
    journal.event(driver.observer.character,'booth_controls_verified',uid=uid,listing_submitted=False)
    if (journal.get(driver.observer.character,'attention') or {}).get('kind')=='calibration':
        journal.set(driver.observer.character,'attention',None)
    return {'verified':True,'character':driver.observer.character,'listing_submitted':False}
