"""Ordinary farmer trade input, gated by live memory/control qualification.

No control coordinates or remote-player layout are guessed here. A qualified
profile must supply them for the pinned client before this adapter can act.
"""
from conquest.character_context import state_path
from contextlib import contextmanager,ExitStack
from pathlib import Path
import struct
import time

from conquest.capture import CaptureUnavailable
from conquest.addressing import checked_address
from conquest.memory_entities import sample_fields
from conquest.merchants.delivery import exact_items,prepare,validate_offers
from conquest.merchants.delivery_bridge import pair
from conquest.merchants.driver import MerchantDriver,wait_hover_validation
from conquest.merchants.trade_controls import targeting_state

PROFILE=Path(state_path('.runtime/merchants/farmer-delivery-qualified.json'))


class RecipientAbsent(CaptureUnavailable):
    """The qualified receiver UID is not present in the farmer scene."""

    def __init__(self,message,*,occupied_tiles=()):
        super().__init__(message)
        self.occupied_tiles=list(map(list,occupied_tiles))


class RecipientAmbiguous(ValueError):
    """More than one live scene object claims the qualified receiver UID."""


class RecipientSceneChanged(ValueError):
    """The receiver scene changed during a read-only target observation."""


def partial_offer(intent,farmer,merchant):
    trade=farmer.get('trade')
    if not trade:raise ValueError('Farmer trade is not open')
    offered=trade.get('own_items',[])
    expected=exact_items(intent['items'])
    if any(expected.get(uid)!=detail for uid,detail in exact_items(offered).items()):
        raise ValueError('Unexpected item in farmer trade')
    validate_offers({**intent,'items':offered},farmer,merchant)
    return offered


def _recipient_record(observer,profile,merchant,*,targeting=False):
    """Resolve the receiver UID in a stable scene using a qualified layout."""
    started=time.monotonic();s=observer.adapter;e=observer.entities
    spec=profile['recipient']
    if spec.get('name_format')!='inline_utf8':
        raise ValueError('Remote player name layout is not qualified')
    if spec.get('draw_format')!='i32':
        raise ValueError('Remote player projection format is not qualified')
    offsets=[spec[k] for k in ('uid_offset','name_offset','position_offset','draw_offset')]
    if any(type(o) is not int or not 8<=o<=2048 for o in offsets):
        raise ValueError('Remote player layout exceeds bounds')
    length=spec['name_capacity']
    if type(length) is not int or not 2<=length<=64:
        raise ValueError('Remote player name exceeds bounds')
    span=max(offsets[0]+4,offsets[1]+length,offsets[2]+8,offsets[3]+8)
    base,collection,trace=e._resolve();p=e.layout
    headers=[(collection+o,'u64') for o in (p.begin_offset,p.end_offset,p.capacity_offset)]
    header=sample_fields(s,headers);begin,end,capacity=header
    if not (0<begin<=end<=capacity and (end-begin)%16==0
            and (capacity-begin)%16==0 and (capacity-begin)//16<=p.max_objects):
        raise ValueError('Remote player scene is invalid')
    entries=s.read_block(checked_address(begin),end-begin) if end>begin else b''
    objects=[struct.unpack_from('<Q',entries,i+8)[0] for i in range(0,len(entries),16)]
    if len(set(objects))!=len(objects):raise ValueError('Ambiguous scene object ownership')
    vtable=base+spec['vtable_rva']
    types=sample_fields(s,[(checked_address(obj),'u64') for obj in objects])
    matches=[];occupied=[];occupied_blocks=[]
    for obj,kind in zip(objects,types):
        if kind!=vtable:continue
        raw=s.read_block(checked_address(obj),span)
        if struct.unpack_from('<Q',raw)[0]!=vtable:raise ValueError('Receiver object type changed')
        uid=struct.unpack_from('<I',raw,offsets[0])[0]
        position=struct.unpack_from('<II',raw,offsets[2])
        occupied.append({'uid':uid,'position':list(position)})
        occupied_blocks.append((obj,raw))
        if uid!=merchant['character_uid']:continue
        name=raw[offsets[1]:offsets[1]+length].split(b'\0')[0].decode('utf-8')
        point=struct.unpack_from('<2i',raw,offsets[3])
        if name!=merchant['character'] or list(position)!=merchant['position']:
            raise ValueError('Receiver name or position disagrees between clients')
        fresh=s.read_block(obj,span)
        ranges=[(0,8),(offsets[0],offsets[0]+4),(offsets[1],offsets[1]+length),
                (offsets[2],offsets[2]+8),(offsets[3],offsets[3]+8)]
        if any(raw[a:b]!=fresh[a:b] for a,b in ranges):
            raise ValueError('Receiver moved during observation')
        matches.append({'address':obj,'uid':uid,'name':name,'position':list(position),
                        'point':list(point)})
    if len(matches)==1 and targeting:
        mode=profile['target_mode']
        native=targeting_state(s)
        if mode.get('rva')!=native['rva'] or mode.get('value')!=native['value']:
            raise ValueError('Trade targeting profile differs from the pinned accessor')
        if not native['targeting_trade']:
            raise ValueError('Client is not in the qualified trade targeting mode')
    if (sample_fields(s,headers)!=header or (end>begin and s.read_block(begin,end-begin)!=entries)
            or any(s.read_block(obj,span)[offsets[2]:offsets[2]+8]
                   !=raw[offsets[2]:offsets[2]+8] for obj,raw in occupied_blocks)
            or sample_fields(s,[(a,'u64') for a,_ in trace])!=[v for _,v in trace]):
        raise RecipientSceneChanged('Receiver scene changed')
    s.assert_identity()
    if time.monotonic()-started>.5:raise CaptureUnavailable('Receiver observation expired')
    if not matches:
        raise RecipientAbsent('Receiver UID is absent from the farmer scene',
                              occupied_tiles=[v['position'] for v in occupied])
    if len(matches)!=1:
        raise RecipientAmbiguous('Receiver UID is ambiguous in the farmer scene')
    return {**matches[0],'occupied_tiles':[v['position'] for v in occupied]}


def recipient_actionability(observer,profile,merchant,*,farmer=None,targeting=False):
    """Return structured, read-only live projection/actionability evidence."""
    record=_recipient_record(observer,profile,merchant,targeting=targeting)
    from conquest.target_actionability import target_actionability
    result=target_actionability(record['point'],profile['gui_size'],profile.get('client_size',profile['gui_size']),
                                (farmer or {}).get('windows',()))
    return {**result,'recipient':record}


def recipient_record(observer,profile,merchant,*,farmer=None,targeting=False):
    result=recipient_actionability(observer,profile,merchant,farmer=farmer,targeting=targeting)
    if not result['actionable']:
        from conquest.target_actionability import TargetNotActionable
        raise TargetNotActionable(result)
    return result['recipient']


def recipient_binding(record):
    """Bind input to one target, excluding scene occupancy used for pathing.

    Each caller still obtains a fresh recipient_record, which validates the
    complete scene within that read, targeting mode and target actionability.
    Other players may move or reorder between two valid scene observations.
    """
    fields=('address','uid','name','position','point')
    if not isinstance(record,dict) or any(field not in record for field in fields):
        raise ValueError('Trade recipient input binding is incomplete')
    if (any(type(record[key]) is not int or record[key]<=0 for key in ('address','uid'))
            or not isinstance(record['name'],str) or not record['name']
            or any(not isinstance(record[key],(list,tuple)) or len(record[key])!=2
                   or any(type(value) is not int for value in record[key])
                   for key in ('position','point'))):
        raise ValueError('Trade recipient input binding is invalid')
    return (record['address'],record['uid'],record['name'],
            tuple(record['position']),tuple(record['point']))


class FarmerTradeDriver:
    def __init__(self,ui,qualification=None):
        self.ui=ui
        if qualification is None:
            from conquest.merchants.farmer_qualification import qualification_path
            qualification=qualification_path(ui.app.observer)
        self.driver=MerchantDriver(ui.app.observer,qualification,ui.coordinator)
        self.revision=ui.app.control.snapshot()['revision']
        self.recipient=None
        self.operation=None

    def set_operation(self,operation):
        self.operation=operation
        return self

    def _before_action(self,stage):
        if self.operation is not None:self.operation.before_action(stage)

    def _action_observed(self,stage,evidence=None):
        if self.operation is not None:self.operation.action_observed(stage,evidence=evidence)

    def report(self,activity,state='running'):
        self.ui.app.messages.put(('automation_work',{'state':state,'activity':activity,
            'revision':self.revision,'at':time.time()}))

    def require_qualified(self,capability='farmer_delivery'):
        profile=self.driver.require_qualified(capability)
        for key in ('recipient','target_mode','gui_size','controls'):
            if not profile.get(key):raise ValueError('Farmer delivery layout qualification is incomplete')
        for control in ('start_trade','open_inventory','inventory_item','trade_drop','confirm_trade'):
            if control not in profile['controls']:raise ValueError('Farmer trade control is not qualified')
        if profile.get('native_trade_layout_revision')==1:
            profile={**profile,'client_size':self.driver.target.snapshot()['client_size'],
                     'gui_size':self.driver.memory.gui.viewport_size()}
        return profile

    def check(self):
        self.ui.coordinator.check()
        state=self.ui.app.control.snapshot()
        grant=getattr(self.ui,'grant',None)
        if grant and grant['expires_at']<=time.time():
            raise CaptureUnavailable('Merchant delivery work window expired; reconcile before further input')
        if (self.ui.closed or state['enabled'] or state.get('paused')
                or state['revision']!=self.revision or not self.ui.safe_to_yield()):
            raise CaptureUnavailable('Farmer delivery input permission changed or expired')
        if self.recipient and not self.ui.runtime.enabled(self.recipient):
            raise CaptureUnavailable('Merchant trading was paused during delivery')
        import ctypes
        if any(ctypes.windll.user32.GetAsyncKeyState(k)&0x8000 for k in (0x7a,0x7b)):
            raise CaptureUnavailable('Farmer delivery paused with F11')

    def read_pair(self,merchant):
        return pair(self.ui,merchant)

    @contextmanager
    def action(self,intent,*,stage):
        self.recipient=intent['merchant']['character']
        self.require_qualified()
        if not self.ui.runtime.enabled(intent['merchant']['character']):
            raise CaptureUnavailable('Merchant trading is paused')
        from conquest.desktop_runtime import physical_coordinates
        with ExitStack() as stack:
            # The receiver can still be releasing its request-acceptance lease.
            # Wait only for ownership; never replay a click or transaction body.
            deadline=time.monotonic()+3
            while True:
                try:
                    self.check()
                    stack.enter_context(self.ui.coordinator.lease('Farmer',purpose='farmer_delivery'))
                    break
                except CaptureUnavailable as error:
                    if (str(error) not in ('Another character owns game input','Waiting for input owner',
                                          'Waiting for the current input action','Another app owns merchant input')
                            or time.monotonic()>=deadline):raise
                    time.sleep(.03)
            stack.enter_context(physical_coordinates())
            from conquest.merchants.delivery_farmer_surface import prepare_delivery,verify_stage_pair
            presentation=prepare_delivery(self,intent,stage=stage,deadline=time.monotonic()+3)
            f,m=self.read_pair(intent['merchant']['character'])
            verify_stage_pair(intent,f,m,stage=stage);presentation()
            self.check()
            from conquest.focus_recovery import activate_client
            if not activate_client(self.driver.target.hwnd,self.driver.observer.adapter.identity):
                raise CaptureUnavailable('Farmer focus unavailable; no delivery input sent')
            yield

    def button(self,intent,control,guard,*,stage=None):
        from conquest.foreground import foreground_click
        profile=self.require_qualified();snapshot=self.driver.read()
        point=self.driver.point(snapshot,control);spec=profile['controls'][control]
        if not spec.get('label'):raise ValueError('Trade button hover identity is not qualified')
        layout=self.driver.layout_revision();layout_revision=layout.stable()
        def before():
            self.check();f,m=self.read_pair(intent['merchant']['character']);guard(f,m)
            if self.driver.point(f,control)!=point:raise ValueError('Trade control moved')
            if spec.get('mode')=='native_trade_confirm':
                from conquest.merchants.native_trade_input import hover
                hover(self.driver,f,spec['mode'])
            else:
                window=next(w for w in f['windows'] if w['name']==spec['window'])
                seeds=[0x02a99238] if spec.get('mode')=='native_items_trade' else None
                self.driver.memory.gui.assert_hovered(window,spec['label'],seeds=seeds)
            layout.assert_current(layout_revision)
            if stage:self._before_action(stage)
        foreground_click(self.driver.target,*point,tuple(profile['client_size']),require_foreground=True,
            before_press=lambda:wait_hover_validation(before,self.check),
            layout_guard=lambda:layout.assert_current(layout_revision))

    def target_status(self,merchant):
        """Read-only bridge helper for route preflight and live re-projection."""
        profile=self.require_qualified();farmer,receiver=self.read_pair(merchant)
        try:
            result=recipient_actionability(self.driver.observer,profile,receiver,farmer=farmer)
        except RecipientAbsent as error:
            result=None;absent_occupied=error.occupied_tiles
            deferred_reason='recipient_absent'
        except RecipientSceneChanged:
            # This dispatcher path runs before any focus, lease, or input.  A
            # changing scene is therefore only a volatile observation: return
            # a non-actionable projection so the route can retry without
            # carrying a possibly stale click point into an input phase.
            result=None;absent_occupied=[]
            deferred_reason='recipient_scene_changed'
        from conquest.memory_life import read_life
        from conquest.scene_input import memory_player_anchor
        life=read_life(self.driver.observer.adapter,self.driver.observer.health_layout,
                       self.driver.observer.character)
        if list(life.position)!=farmer['position']:
            raise ValueError('Farmer moved during delivery target preflight')
        anchor=memory_player_anchor(self.driver.observer,life)
        if result is None:
            return {'schema_version':1,'ready':False,'actionable':False,
                    'reason':deferred_reason,'character':farmer['character'],
                    'farmer_position':farmer['position'],'merchant':receiver['character'],
                    'merchant_position':receiver['position'],'point':None,
                    'viewport':list(profile['gui_size']),'client_size':list(profile['client_size']),
                    'anchor':list(anchor),'occupied_tiles':list(map(list,dict.fromkeys(map(tuple,
                        [farmer['position'],*absent_occupied]))))}
        occupied=[farmer['position'],*result['recipient'].get('occupied_tiles',[])]
        occupied=list(map(list,dict.fromkeys(map(tuple,occupied))))
        return {'schema_version':1,'ready':result['actionable'],'reason':result['reason'],
                'character':farmer['character'],'farmer_position':farmer['position'],
                'merchant':receiver['character'],'merchant_position':receiver['position'],
                'point':result['recipient']['point'],'viewport':list(profile['gui_size']),
                'client_size':list(profile['client_size']),'anchor':list(anchor),
                'occupied_tiles':occupied,**result}

    def open_trade(self,intent):
        self.report('Requesting a trade with '+intent['merchant']['character'])
        from conquest.foreground import foreground_click
        def unchanged(f,m):
            prepare(f,m,intent['items'])
            for role,current in (('farmer',f),('merchant',m)):
                if (current['identity']!=intent[role]['identity']
                        or current['character_uid']!=intent[role]['character_uid']
                        or exact_items(current['inventory'])!=exact_items(intent[role]['inventory'])):
                    raise ValueError('Delivery participants or stock changed before request')
        with self.action(intent,stage='open'):
            profile=self.require_qualified();f,m=self.read_pair(intent['merchant']['character'])
            unchanged(f,m);recipient_record(self.driver.observer,profile,m,farmer=f)
            self.button(intent,'start_trade',unchanged,stage='trade_target_mode')
            targeting_f,targeting_m=self.read_pair(m['character']);unchanged(targeting_f,targeting_m)
            recipient=recipient_record(self.driver.observer,profile,targeting_m,
                                       farmer=targeting_f,targeting=True)
            binding=recipient_binding(recipient)
            self._action_observed('trade_target_mode',{'targeting_trade':True})
            point=tuple(round(v*p/g) for v,p,g in zip(recipient['point'],profile['client_size'],profile['gui_size']))
            layout=self.driver.layout_revision();layout_revision=layout.stable()
            def before():
                self.check();fresh_f,fresh_m=self.read_pair(m['character']);unchanged(fresh_f,fresh_m)
                fresh_recipient=recipient_record(self.driver.observer,profile,fresh_m,
                                                 farmer=fresh_f,targeting=True)
                if recipient_binding(fresh_recipient)!=binding:
                    raise ValueError('Receiver changed before trade request')
                layout.assert_current(layout_revision)
                self._before_action('trade_request')
            foreground_click(self.driver.target,*point,tuple(profile['client_size']),require_foreground=True,before_press=before,
                layout_guard=lambda:layout.assert_current(layout_revision))
        self.wait_until(m['character'],lambda f,m:partial_offer(intent,f,m)==[])
        self._action_observed('trade_request',{'trade_open':True})

    def place_item(self,intent,item):
        self.report('Placing '+item['name']+' in the trade with '+intent['merchant']['character'])
        from conquest.foreground import foreground_drag
        with self.action(intent,stage='place'):
            f,m=self.read_pair(intent['merchant']['character']);placed=partial_offer(intent,f,m)
            if item['uid'] not in exact_items(intent['items']):raise ValueError('Item is outside reserved batch')
            if item['uid'] in exact_items(placed):raise ValueError('Item already offered; no repeat drag')
            spec=self.require_qualified()['controls']['inventory_item']
            if not any(w['name']==spec['window'] for w in f['windows']):
                self.button(intent,'open_inventory',lambda f,m:partial_offer(intent,f,m))
                f,m=self.read_pair(m['character']);partial_offer(intent,f,m)
            current=next((i for i in f['inventory'] if i['uid']==item['uid']),None)
            if not current or exact_items([current])!=exact_items([item]):raise ValueError('Reserved item changed')
            source=self.driver.point(f,'inventory_item',current['slot']);destination=self.driver.point(f,'trade_drop')
            layout=self.driver.layout_revision();layout_revision=layout.stable()
            def before():
                self.check();fresh,receiver=self.read_pair(m['character'])
                if exact_items(partial_offer(intent,fresh,receiver))!=exact_items(placed):
                    raise ValueError('Partial offer changed before drag')
                same=next((i for i in fresh['inventory'] if i['uid']==current['uid']),None)
                if (not same or exact_items([same])!=exact_items([current]) or same['slot']!=current['slot']
                        or self.driver.point(fresh,'inventory_item',same['slot'])!=source
                        or self.driver.point(fresh,'trade_drop')!=destination):
                    raise ValueError('Item slot or trade geometry changed before drag')
                # The inventory table may be covered by a newly opened panel.
                # Do not drag from another window at otherwise unchanged pixels.
                from conquest.merchants.memory import unpack,HoverNotReady
                window=next(w for w in fresh['windows'] if w['name']==spec['window'])
                gui=self.driver.memory.gui
                context=unpack(gui.session,gui.base+0x6966f0,'<Q')[0]
                if unpack(gui.session,context+0x3ec0,'<Q')[0]!=window['address']:
                    raise HoverNotReady('Inventory cell is covered by another window')
                layout.assert_current(layout_revision)
                self._before_action('offer_item:'+str(item['uid']))
            foreground_drag(self.driver.target,source,destination,tuple(self.require_qualified()['client_size']),
                before_press=lambda:wait_hover_validation(before,self.check),
                layout_guard=lambda:layout.assert_current(layout_revision))
        self.wait_until(m['character'],lambda f,m:item['uid'] in exact_items(partial_offer(intent,f,m)))
        self._action_observed('offer_item:'+str(item['uid']),{'offered_uid':item['uid']})

    def confirm(self,intent):
        self.report('Confirming the exact item transfer to '+intent['merchant']['character'])
        with self.action(intent,stage='confirm'):
            f,m=self.read_pair(intent['merchant']['character']);validate_offers(intent,f,m)
            self.button(intent,'confirm_trade',lambda f,m:validate_offers(intent,f,m),stage='farmer_confirm')

    def wait_until(self,merchant,predicate,seconds=10):
        # Read-only observation may finish after an input handoff has expired.
        deadline=time.monotonic()+seconds
        while time.monotonic()<deadline:
            try:
                f,m=self.read_pair(merchant)
                if predicate(f,m):return f,m
            except (OSError,ValueError):pass
            time.sleep(.1)
        raise ValueError('Trade result unverified; reconcile before retrying input')

    def wait_pair(self,merchant):
        self.report('Verifying both inventories after transfer to '+merchant)
        return self.wait_until(merchant,lambda f,m:not f.get('trade') and not m.get('trade')
                               and not f.get('request') and not m.get('request'))


def delivery_target_status(ui,merchant,qualification=None):
    """Dispatcher entry point; performs no focus, grant, lease or input action."""
    return FarmerTradeDriver(ui,qualification).target_status(merchant)
