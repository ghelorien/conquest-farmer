"""Normal foreground input, gated by per-capability live qualification."""
import json
from pathlib import Path
import time
import struct
import zlib
from conquest.capture import CaptureUnavailable
from conquest.foreground import foreground_click, foreground_drag
from conquest.input_probe import MessageTarget
from conquest.merchants.memory import MerchantMemory,HoverNotReady
from conquest.merchants.controller import identities
from conquest.merchants.pricing import MAX_BOOTH_PRICE, validate_booth_price, parse_booth_price, wait_booth_price


def booth_dialog_ready(snapshot, size=(264,92)):
    windows=[w for w in snapshot['windows'] if w['name']=='Add Item to Booth']
    return len(windows)==1 and list(windows[0]['geometry'][2:])==list(size)


def wait_hover_validation(validate, check, *, clock=time.monotonic, sleep=time.sleep):
    deadline=clock()+.5
    while True:
        check()
        try:
            return validate()
        except HoverNotReady:
            if clock()>=deadline:
                raise
        sleep(.02)


class MerchantDriver:
    def __init__(self, observer, qualification, coordinator):
        self.observer,self.qualification,self.coordinator = observer,Path(qualification),coordinator
        self.memory = MerchantMemory(observer)
        self.target = observer.operations.target

    def read(self):
        return self.memory.read()

    def layout_revision(self):
        from conquest.layout_revision import SharedLayoutRevision
        return SharedLayoutRevision(self.target,windows=self.memory.gui.windows,
                                    gui_size=self.memory.gui.viewport_size)

    def verify_listing_layout(self):
        """Detect resized panels before recording a listing transaction."""
        profile=self.require_qualified('booth_input')
        snapshot=self.read()
        for control in ('inventory_item','booth_drop','remove_listing'):
            spec=profile['controls'][control]
            matches=[w for w in snapshot['windows'] if w['name']==spec['window']]
            if len(matches)!=1 or list(matches[0]['geometry'][2:])!=spec['size']:
                raise ValueError('Merchant panel resized; refresh booth qualification')

    def require_qualified(self, capability):
        try:
            data = json.loads(self.qualification.read_text())
        except (OSError,ValueError):
            raise ValueError(f'{capability}: live memory/input qualification is pending') from None
        if (data.get('client_sha256') != self.observer.adapter.expected_sha256
                or data.get('character') != self.observer.character
                or data.get('server') != 'America'
                or data.get('capabilities',{}).get(capability) is not True
                or not data.get('evidence')):
            raise ValueError(f'{capability}: live memory/input qualification is pending')
        if capability=='booth_input':
            from conquest.desktop_runtime import physical_coordinates
            with physical_coordinates():
                if (list(self.target.snapshot()['client_size'])!=data.get('client_size')
                        or self.memory.gui.viewport_size()!=data.get('gui_size',data.get('client_size'))):
                    raise ValueError('Embedded merchant size changed; verify booth controls again')
        return data

    def point(self, snapshot, control, slot=None):
        profile = json.loads(self.qualification.read_text())
        if profile.get('native_trade_layout_revision')==1:
            profile={**profile,'client_size':self.target.snapshot()['client_size'],
                     'gui_size':self.memory.gui.viewport_size()}
        spec = profile['controls'][control]
        from conquest.merchants.native_trade_input import MODES,point as native_point
        if spec.get('mode') in MODES:
            if slot is not None:raise ValueError('Native trade control does not take a slot')
            return native_point(self,snapshot,spec['mode'])
        matches = [w for w in snapshot['windows'] if w['name']==spec['window'] or
            (spec['window'].endswith('_') and w['name'].startswith(spec['window']))]
        if len(matches)!=1:
            raise ValueError(f'{control}: expected exactly one active window')
        window = matches[0]
        x,y,width,height = window['geometry']
        if [width,height] != spec['size']:
            raise ValueError(f'{control}: unqualified window dimensions')
        if spec.get('mode')=='native_items_trade':
            label={'start_trade':'Trade','open_inventory':'Items'}.get(control)
            if spec['window']!='##Control' or slot is not None or not label or spec.get('label')!=label:
                raise ValueError('Unqualified native HUD control')
            from conquest.memory_shop import MemoryGui
            from conquest.discard_loot import inventory_button
            from conquest.merchants.trade_controls import trade_button
            gui=MemoryGui(self.observer.adapter)
            px,py=(trade_button if spec['label']=='Trade' else inventory_button)(gui)
        else:
            dx,dy = spec['offset']
            if slot is not None:
                columns = spec['columns']
                dx += slot % columns * spec['stride'][0]
                dy += slot // columns * spec['stride'][1]
            px,py = x+dx-window['scroll'][0],y+dy-window['scroll'][1]
        if slot is not None and spec.get('table'):
            table = self.memory.gui.table(window,spec['table'])
            columns = table['columns']
            if (len(columns)!=spec['columns'] or table['row_height']!=spec['stride'][1]
                    or any(abs(columns[i+1]['content_x']-columns[i]['content_x']-spec['stride'][0])>.01
                           for i in range(len(columns)-1))):
                raise ValueError('Merchant table spacing changed; recalibration required')
            px = columns[slot%len(columns)]['content_x']+spec['cell_offset'][0]
            py = table['outer'][1]+slot//len(columns)*table['row_height']+spec['cell_offset'][1]
            left,top,right,bottom = table['clip']
            if not left+2 < px < right-2 or not top+2 < py < bottom-2:
                raise ValueError('Control is clipped; scroll before sending input')
        if not x+2 < px < x+width-2 or not y+2 < py < y+height-2:
            raise ValueError('Control is outside the visible qualified window; scroll manually')
        gui_size = profile.get('gui_size',profile['client_size'])
        if self.memory.gui.viewport_size()!=gui_size:
            raise ValueError('Merchant GUI viewport changed; recalibration required')
        return tuple(round(v*physical/logical) for v,physical,logical in zip((px,py),profile['client_size'],gui_size))

    def click(self, snapshot, control, slot=None, *, validate=None):
        from conquest.desktop_runtime import physical_coordinates
        with physical_coordinates():
            return self._click(snapshot,control,slot,validate=validate)

    def _click(self, snapshot, control, slot=None, *, validate=None):
        self.coordinator.check()
        self.observer.adapter.assert_identity()
        if self.observer.adapter.identity != snapshot['identity']:
            raise ValueError('Client identity changed before input')
        point = self.point(snapshot,control,slot)
        size = tuple(self.target.snapshot()['client_size'])
        profile = json.loads(self.qualification.read_text())
        from conquest.merchants.native_trade_input import MODES,hover as native_hover
        native_mode=profile.get('controls',{}).get(control,{}).get('mode')
        if native_mode not in MODES and list(size) != profile['client_size']:
            raise ValueError('Client geometry differs from qualified merchant controls')
        from conquest.focus_recovery import activate_client
        if not activate_client(self.target.hwnd,snapshot['identity']):
            raise CaptureUnavailable('Merchant did not receive foreground focus; activate Conquest and verify again. No click sent')
        layout=self.layout_revision() if hasattr(self,'memory') else None
        revision=layout.stable() if layout is not None else None
        def before_press():
            from conquest.merchants.controller import offer_fingerprint,validate_trade
            fresh = self.read()
            if control not in ('accept_trade','accept_request') and (fresh.get('trade') or fresh.get('request')):
                raise ValueError('A trade interrupted the listing control')
            if fresh['identity']!=snapshot['identity'] or self.point(fresh,control,slot)!=point:
                raise ValueError('Merchant control changed before button press')
            if native_mode in MODES:
                native_hover(self,fresh,native_mode)
            if control=='accept_trade':
                if offer_fingerprint(validate_trade(fresh))!=offer_fingerprint(validate_trade(snapshot)):
                    raise ValueError('Trade offer changed before button press')
            elif control=='accept_request':
                if fresh.get('request') != snapshot.get('request'):
                    raise ValueError('Incoming request changed before button press')
            elif control=='remove_listing':
                if [(i['uid'],i['price']) for i in fresh['booth']] != [(i['uid'],i['price']) for i in snapshot['booth']]:
                    raise ValueError('Booth ordering changed before removing an item')
            if control in ('price_field','confirm_listing','cancel_listing','remove_listing'):
                from conquest.merchants.memory import unpack
                spec = profile['controls'][control]
                window = next(w for w in fresh['windows'] if w['name']==spec['window'])
                if control=='remove_listing':
                    ptr = self.memory.booth_pointer(slot,fresh['booth'][slot]['uid'])
                    window_seed = unpack(self.observer.adapter,window['address']+8,'<I')[0]
                    table_seed = self.memory.gui.table(window,'BoothTable')['id']
                    seeds = [zlib.crc32(struct.pack('<Q',ptr),seed) for seed in (window_seed,table_seed)]
                    self.memory.gui.assert_hovered(window,'\u274c',seeds=seeds)
                else:
                    label = {'price_field':'##Amount','confirm_listing':'OK','cancel_listing':'Cancel'}[control]
                    self.memory.gui.assert_hovered(window,label)
            if validate:
                validate()
            self.coordinator.check()
            if layout is not None:layout.assert_current(revision)
        # The client can process pointer movement after the OS reports it.
        # Re-read the complete guards each time; never reuse a stale offer,
        # item order or control position while waiting for its hover ID.
        return foreground_click(self.target,*point,size,require_foreground=False,
            before_press=lambda:wait_hover_validation(before_press,self.coordinator.check),
            layout_guard=(lambda:layout.assert_current(revision)) if layout is not None else None)

    def accept_request(self, snapshot):
        self.click(snapshot,'accept_request')

    def accept_trade(self, snapshot):
        from conquest.merchants.controller import validate_trade,offer_fingerprint
        fresh = self.read()
        if (fresh['identity'] != snapshot['identity'] or
                offer_fingerprint(validate_trade(fresh)) != offer_fingerprint(validate_trade(snapshot))):
            raise ValueError('Trade changed immediately before input')
        self.click(fresh,'accept_trade')

    def wait_for(self, predicate, check, seconds=10):
        deadline = time.monotonic()+seconds
        while time.monotonic()<deadline:
            check()
            try:
                snapshot = self.read()
                if predicate(snapshot):
                    return snapshot
            except (OSError,ValueError):
                pass
            time.sleep(.15)
        raise ValueError('Transaction result not verified; reconcile before retrying')

    def list_item(self, snapshot, item, price, check):
        from conquest.valuables import require_marketable
        require_marketable(item)
        # Reject before removing an existing listing or opening any dialog.
        validate_booth_price(price)
        from conquest.desktop_runtime import physical_coordinates
        submission={'attempted':False}
        try:
            with physical_coordinates():
                return self._list_item(snapshot,item,price,check,submission=submission)
        except (ValueError,OSError,CaptureUnavailable) as error:
            if not submission['attempted']:
                from conquest.merchants.controller import ListingNotSubmitted
                raise ListingNotSubmitted(str(error)) from error
            raise

    def prepare_listing(self, snapshot, item, check):
        """Resolve focus and read-only layout failures before recording intent."""
        from conquest.valuables import require_marketable
        require_marketable(item)
        from conquest.desktop_runtime import physical_coordinates
        from conquest.focus_recovery import activate_client
        with physical_coordinates():
            check()
            if any(w['name']=='Add Item to Booth' for w in snapshot['windows']):
                raise ValueError('Close the existing price dialog before starting a listing')
            if not activate_client(self.target.hwnd,snapshot['identity']):
                raise CaptureUnavailable('Listing waits for Conquest foreground focus; no item input sent')
            if item.get('price') is None:
                visible=self.ensure_visible(snapshot,'inventory_item',item['slot'],check)
                self.point(visible,'inventory_item',item['slot'])
                self.point(visible,'booth_drop')
            check()

    def _list_item(self, snapshot, item, price, check, *, submission=None):
        if any(w['name']=='Add Item to Booth' for w in snapshot['windows']):
            raise ValueError('Close the existing price dialog before starting a listing')
        uid = item['uid']
        if item.get('price') is not None:
            snapshot = self.ensure_visible(snapshot,'remove_listing',item['slot'],check)
            self.click(snapshot,'remove_listing',item['slot'])
            snapshot = self.wait_for(lambda s:any(i['uid']==uid for i in s['inventory']) and
                not any(i['uid']==uid for i in s['booth']),check)
            item = next(i for i in snapshot['inventory'] if i['uid']==uid)
        check()
        snapshot = self.ensure_visible(snapshot,'inventory_item',item['slot'],check)
        # Drag only an item whose UID was read from this exact inventory slot.
        source = self.point(snapshot,'inventory_item',item['slot'])
        destination = self.point(snapshot,'booth_drop')
        fresh = self.read()
        if identities(fresh['inventory']) != identities(snapshot['inventory']) or self.point(fresh,'inventory_item',item['slot']) != source or self.point(fresh,'booth_drop') != destination:
            raise ValueError('Inventory or booth changed before listing')
        self.coordinator.check()
        from conquest.focus_recovery import activate_client
        if not activate_client(self.target.hwnd,snapshot['identity']):
            raise CaptureUnavailable('Merchant did not receive foreground focus')
        size = tuple(self.target.snapshot()['client_size'])
        if list(size) != json.loads(self.qualification.read_text())['client_size']:
            raise ValueError('Unqualified merchant client size')
        layout=self.layout_revision();revision=layout.stable()
        def validate_drag():
            check()
            current = self.read()
            if current.get('trade') or current.get('request'):
                raise ValueError('A trade interrupted the listing drag')
            if (current['identity']!=snapshot['identity'] or identities(current['inventory'])!=identities(snapshot['inventory'])
                    or self.point(current,'inventory_item',item['slot'])!=source
                    or self.point(current,'booth_drop')!=destination):
                raise ValueError('Listing item or window changed before drag')
        foreground_drag(self.target,source,destination,size,before_press=validate_drag,
            layout_guard=lambda:layout.assert_current(revision))
        expected_dialog=json.loads(self.qualification.read_text())['controls']['price_field']['size']
        opened = self.wait_for(lambda s:booth_dialog_ready(s,expected_dialog),check)
        model = self.memory.gui.model(25,0x5c27f8)
        from conquest.merchants.memory import unpack
        if unpack(self.observer.adapter,model+0x50,'<I')[0] != uid:
            raise ValueError('Listing dialog belongs to another item')
        self.click(opened,'price_field')
        # Normal keyboard input, shared with the existing silver entry helper.
        from conquest.warehouse_money import type_amount
        type_amount(self.target,price,expected_size=size,maximum=MAX_BOOTH_PRICE)
        def entered_price():
            return parse_booth_price(self.observer.adapter.read_block(model+0x54,12).split(b'\0')[0])
        def check_entry():
            check()
            if unpack(self.observer.adapter,model+0x50,'<I')[0]!=uid:
                raise ValueError('Listing item changed during price entry')
        wait_booth_price(lambda:self.observer.adapter.read_block(model+0x54,12).split(b'\0')[0],price,check_entry)
        check()
        confirmed = self.read()
        if unpack(self.observer.adapter,model+0x50,'<I')[0] != uid or entered_price() != price:
            raise ValueError('Listing item changed before submission')
        def validate_price():
            if unpack(self.observer.adapter,model+0x50,'<I')[0] != uid or entered_price()!=price:
                raise ValueError('Listing item or price changed before button press')
        if submission is not None:submission['attempted']=True
        self.click(confirmed,'confirm_listing',validate=validate_price)

    def ensure_visible(self, snapshot, control, slot, check):
        """Scroll only the memory-owned grid; no offscreen item click is sent."""
        from conquest.foreground import foreground_scroll
        from conquest.focus_recovery import activate_client
        profile = json.loads(self.qualification.read_text())
        spec = profile['controls'][control]
        if not spec.get('table'):
            self.point(snapshot,control,slot)
            return snapshot
        original = (identities(snapshot['inventory']),identities(snapshot['booth']))
        size = tuple(profile['client_size'])
        for _ in range(12):
            check()
            matches = [w for w in snapshot['windows'] if w['name']==spec['window']]
            if len(matches)!=1 or list(matches[0]['geometry'][2:])!=spec['size']:
                raise ValueError('Merchant grid changed before scrolling')
            table = self.memory.gui.table(matches[0],spec['table'])
            if len(table['columns'])!=spec['columns'] or table['row_height']!=spec['stride'][1]:
                raise ValueError('Merchant table changed before scrolling')
            y = table['outer'][1]+slot//spec['columns']*table['row_height']+spec['cell_offset'][1]
            left,top,right,bottom = table['clip']
            if top+2 < y < bottom-2:
                self.point(snapshot,control,slot)
                return snapshot
            if not activate_client(self.target.hwnd,snapshot['identity']):
                raise ValueError('Merchant did not receive focus for grid scrolling')
            viewport = self.memory.gui.viewport_size()
            point = tuple(round(v*actual/logical) for v,actual,logical in
                          zip(((left+right)/2,(top+bottom)/2),size,viewport))
            self.coordinator.check()
            foreground_scroll(self.target,point,2 if y<=top+2 else -2,size)
            fresh = self.read()
            if (fresh['identity']!=snapshot['identity'] or fresh.get('trade') or fresh.get('request')
                    or (identities(fresh['inventory']),identities(fresh['booth']))!=original):
                raise ValueError('Merchant stock changed while scrolling')
            snapshot = fresh
        raise ValueError('Merchant grid did not reveal the requested item')
