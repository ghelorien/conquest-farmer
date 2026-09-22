"""Bounded ordinary vendor input with fresh memory checks and trade receipts."""
from conquest.viewport import size_for
from dataclasses import asdict
from pathlib import Path
import time
import yaml

from conquest.capture import CaptureUnavailable
from conquest.addressing import PlayerLayout
from conquest.foreground import foreground_click, foreground_drag
from conquest.memory_inventory import MemoryInventoryReader, InventoryLayout
from conquest.memory_life import read_life
from conquest.memory_npcs import MemoryNpcReader, interaction_point
from conquest.memory_shop import MemoryShopReader
from conquest.memory_warehouse import MemoryWarehouseReader, deposit_received
from conquest.reconnect import login_screen


# Explicitly identified low-value consumables, inferior to this route's Painkiller
# or unnecessary mana supplies for the archer. Never blanket-sell special IDs.
JUNK_CONSUMABLES = frozenset((1000000,1000010,1001000,1001010,1001020))
from conquest.valuables import SPECIAL_LOOT_TYPES,storage_only
PROTECTED_VALUABLES = SPECIAL_LOOT_TYPES


def stash_candidate(item):
    get=item.get if isinstance(item,dict) else lambda key,default=None:getattr(item,key,default)
    kind,plus=get('type_id'),get('plus')
    return (get('slot') is not None and (kind in PROTECTED_VALUABLES or
        (type(kind) is int and 100000<=kind<600000 and
         (kind%10 in (8,9) or (type(plus) is int and 1<=plus<=12)))))


def junk_type(type_id):
    # Type alone is insufficient for gear; sale_candidate also requires +0.
    return type_id in JUNK_CONSUMABLES


def sale_candidate(item):
    get=item.get if isinstance(item,dict) else lambda key,default=None:getattr(item,key,default)
    kind=get('type_id')
    if kind in PROTECTED_VALUABLES or storage_only(item):return False
    if junk_type(kind):
        return True
    # User authorized sales after inspecting the client formatter's + field.
    # Unknown/+ gear and quality 7+ stay protected; only carried gear is sold.
    plus=get('plus')
    return (isinstance(kind,int) and 100000<=kind<600000 and kind%10<=6
            and type(plus) is int and plus==0 and get('slot') is not None)


def expendable_arrow(item, inventory):
    # Repeated early reloads can leave 13/25-arrow bundles occupying whole slots.
    # Retire remnants that cannot fire Scatter; larger small bundles require
    # at least 600 arrows of the same tier to remain available.
    from conquest.arrow_upgrades import NORMAL_ARROWS
    total=inventory.count(item.type_id)
    ammo=inventory.equipped_ammo
    if ammo and ammo.type_id==item.type_id:
        total+=ammo.amount
    return (item in inventory.items and item.type_id in NORMAL_ARROWS and 0<item.amount<=25
            and (item.amount<3 or total-item.amount>=600))


class TownObservationUnavailable(ValueError):
    """Observation or input acquisition failed before the action was submitted."""
    code = 'town_observation_unavailable'


def transient_observation(error):
    from conquest.merchants.memory import GuiObservationChanged
    if isinstance(error,GuiObservationChanged):return True
    text = str(error)
    return any(part in text for part in (
        'changed during observation', 'observation expired',
        'Vendor moved before interaction', 'Shop or vendor changed before buying',
        'Inventory changed before selling', 'Shop product reference changed',
        'Warehouse or inventory changed before deposit',
        'Pointer is null or outside supported user address space',
    ))


class TownTrade:
    def __init__(self, observer):
        self.observer = observer
        self.shop = MemoryShopReader.for_session(observer.adapter)
        self.npcs = MemoryNpcReader(observer.entities)
        self.inventory = MemoryInventoryReader.for_session(observer.adapter)

    def life(self, minimum_health=0, *, any_map=False):
        o = self.observer
        if login_screen(o.operations.target.hwnd):
            raise CaptureUnavailable('Reconnect before town actions')
        life = o.read_life() if hasattr(o,'read_life') else read_life(o.adapter,o.health_layout,o.character)
        if life.dead_candidate or (life.map_id not in (1002,1011) and not any_map) or life.current_hp <= 0 or life.current_hp < life.max_hp*minimum_health:
            raise ValueError('Town action requires a living character on the town map')
        return life

    def warehouse_layout(self):
        """Qualify the current native window and draggable warehouse panels."""
        from conquest.layout_revision import SharedLayoutRevision
        from conquest.merchants.memory import GuiReader
        gui = GuiReader.for_session(self.observer.adapter)
        layout = SharedLayoutRevision(self.observer.operations.target,
            windows=gui.windows, gui_size=gui.viewport_size)
        return layout, layout.stable()

    @staticmethod
    def warehouse_native_point(point, revision):
        """Scale one memory-GUI point into the stable physical client."""
        logical=revision.gui_size;physical=revision.client_size
        if (logical is None or len(logical)!=2 or len(physical)!=2
                or min(*logical,*physical)<=1
                or not 0<=point[0]<logical[0] or not 0<=point[1]<logical[1]):
            raise ValueError('Warehouse point is outside the current GUI/client geometry')
        result=tuple(round(value*native/gui)
            for value,native,gui in zip(point,physical,logical))
        if not 0<=result[0]<physical[0] or not 0<=result[1]<physical[1]:
            raise ValueError('Scaled warehouse point is outside the native client')
        return result

    def require_warehouse_hover(self, window):
        """Require the pointer's topmost ImGui window to be this exact grid."""
        from conquest.merchants.memory import GuiReader, HoverNotReady, unpack
        gui=GuiReader.for_session(self.observer.adapter)
        context=unpack(gui.session,gui.base+gui.context_rva,'<Q')[0]
        address=window['address'] if isinstance(window,dict) else window.address
        if unpack(gui.session,context+0x3ec0,'<Q')[0]!=address:
            raise HoverNotReady('Warehouse drag endpoint is covered by another window')

    def warehouse_deposit(self, uid):
        """Deposit one exact protected UID under physical/layout input guards."""
        from conquest.desktop_runtime import physical_coordinates
        with physical_coordinates():
            layout, layout_revision = self.warehouse_layout()
            npc = self.vendor(0)
            reader = MemoryWarehouseReader.for_session(self.observer.adapter)
            before = self.inventory.read()
            stored = reader.read()
            candidates = [i for i in before.items if i.uid == uid and stash_candidate(i)]
            if len(candidates) != 1:
                raise ValueError('Selected warehouse item is absent or not a supported valuable')
            item = candidates[0]
            if len(stored.items) >= stored.capacity or item.uid in {i.uid for i in stored.items}:
                raise ValueError('Warehouse is full or already contains this UID')
            grid = self.shop.gui.read('Inventory/##ItemGrid_')
            target = reader.gui.read('Warehouse/ScrollingRegion_')
            if grid.size != (407.,175.) or grid.scroll != (0.,0.):
                raise ValueError('Inventory grid differs from its calibrated layout')
            if target.size != (272.,326.) or target.scroll != (0.,0.):
                raise ValueError('Warehouse grid differs from its calibrated layout')
            fresh = self.inventory.read()
            if (fresh.items != before.items or fresh.silver != before.silver
                    or fresh.equipped_ammo != before.equipped_ammo
                    or reader.read() != stored or self.vendor(0) != npc
                    or self.shop.gui.read('Inventory/##ItemGrid_') != grid
                    or reader.gui.read('Warehouse/ScrollingRegion_') != target):
                raise ValueError('Warehouse or inventory changed before deposit')
            logical_source = (round(grid.position[0]+20+40*(item.slot%10)),
                              round(grid.position[1]+20+40*(item.slot//10)))
            logical_destination = (round(target.position[0]+target.size[0]/2),
                                   round(target.position[1]+target.size[1]/2))
            source=self.warehouse_native_point(logical_source,layout_revision)
            destination=self.warehouse_native_point(logical_destination,layout_revision)
            self.life(any_map=True)
            def input_guard():
                check_input=getattr(self,'check_input',None)
                if check_input is not None:check_input()
                layout.assert_current(layout_revision)
            def before_press():
                from conquest.merchants.driver import wait_hover_validation
                def ready():
                    input_guard()
                    current=self.inventory.read()
                    if (current.items!=before.items or current.silver!=before.silver
                            or current.equipped_ammo!=before.equipped_ammo
                            or reader.read()!=stored or self.vendor(0)!=npc
                            or self.shop.gui.read('Inventory/##ItemGrid_')!=grid
                            or reader.gui.read('Warehouse/ScrollingRegion_')!=target):
                        raise TownObservationUnavailable(
                            'Warehouse or inventory changed before deposit; no button pressed')
                    self.require_warehouse_hover(grid)
                wait_hover_validation(ready,input_guard)
            def before_release():
                from conquest.merchants.driver import wait_hover_validation
                def ready():
                    input_guard()
                    # The destination must still be the exact qualified warehouse
                    # grid while the button is held. Releasing over a moved panel
                    # could transfer the item somewhere other than storage.
                    if (reader.gui.read('Warehouse/ScrollingRegion_')!=target
                            or self.vendor(0)!=npc):
                        raise ValueError('Warehouse moved during deposit drag; item preserved if still carried')
                    self.require_warehouse_hover(target)
                wait_hover_validation(ready,input_guard)
            self.input_attempted = True
            from conquest.merchants.memory import GuiObservationChanged
            try:
                foreground_drag(self.observer.operations.target,source,destination,
                    tuple(layout_revision.client_size),before_press=before_press,
                    layout_guard=input_guard,before_release=before_release)
            except GuiObservationChanged:
                # The drag helper releases held input on failure. It may have
                # completed the deposit: reconcile both inventories below without
                # another drag. Missing proof remains an uncertain transaction.
                pass
            self.verified_read(lambda:(self.inventory.read(),reader.read()),
                lambda pair:deposit_received(item,before,stored,*pair),
                'Warehouse deposit was not verified; no repeat input issued',timeout=5)
            return {'stored':item.uid,'type_id':item.type_id,'verified_in_warehouse':True}

    def click(self, point, button='left', *, before_press=None, before_mouse_down=None):
        try:self.life(any_map=True)
        except ValueError as error:
            # This check precedes the button event, even if opening a panel
            # earlier in the operation set input_attempted.
            if transient_observation(error):
                raise TownObservationUnavailable(str(error)) from error
            raise
        from conquest.viewport import size_for
        from conquest.merchants.coordination import InputAcquisitionBusy
        try:
            return foreground_click(self.observer.operations.target,*point,size_for(self.observer),
                button=button,require_foreground=True,before_press=before_press,
                before_mouse_down=before_mouse_down)
        except InputAcquisitionBusy as error:
            # Re-enter the whole town operation on retry, including its fresh
            # vendor, shop, product, funds and capacity checks. Never retry an
            # arbitrary CaptureUnavailable or a partially submitted click.
            raise TownObservationUnavailable(str(error)) from error
        except CaptureUnavailable as error:
            if 'no input sent' in str(error) or 'no button pressed' in str(error):
                raise TownObservationUnavailable(str(error)) from error
            raise

    def click_npc(self, npc, reread, *, point=None):
        from conquest.scene_pointer import wait_scene_pointer
        point=interaction_point(npc) if point is None else point
        def unchanged():
            try:
                self.life(any_map=True)
                fresh=reread()
            except ValueError as error:
                if transient_observation(error):
                    raise TownObservationUnavailable(str(error)+'; no button pressed') from error
                raise
            if fresh!=npc:
                raise TownObservationUnavailable('Vendor moved before interaction; no button pressed')
        def before_press():
            # The renderer may still be using the previous pointer location
            # after Windows has accepted its move. Wait for memory feedback,
            # rechecking the NPC while waiting, without repeating any click.
            wait_scene_pointer(self.observer.adapter,point,unchanged)
        return self.click(point,before_press=before_press)

    def vendor(self, type_id, *, stable_identity_only=False):
        life = self.life(any_map=True) if type_id==0 else self.life()
        from conquest.memory_npcs import vendor_identity
        if stable_identity_only:
            if type_id!=0 or life.map_id!=1036:
                raise ValueError('Stable warehouse identity is restricted to Market item withdrawal')
            from conquest.market_services import discover
            # The selected NPC remains in a freshly bounded scene with its
            # object, model, UID, name/type and world tile pinned. Unrelated
            # player reordering and draw animation cannot alter grid input.
            npc=discover(self.observer.entities,life.map_id,'Warehouseman',stable_identity_only=True)[1]
        elif type_id==0:
            from conquest.memory_npcs import warehouse_identity
            expected=warehouse_identity(self.observer.entities,life.map_id)
            reader=MemoryNpcReader(self.observer.entities,vendors=[expected])
        else:expected=vendor_identity(life.map_id,type_id);reader=self.npcs
        if not stable_identity_only:
            choices = [n for n in reader.read(life.map_id).npcs if n.type_id == expected.type_id]
            if len(choices) != 1:
                raise ValueError('Vendor is not in the current scene')
            npc = choices[0]
        if max(abs(a-b) for a,b in zip(life.position,npc.position)) > 18:
            raise ValueError('Travel closer to the vendor before opening its shop')
        return npc

    def __call__(self, body):
        # The bridge serializes calls. Never label a failed input attempt as a
        # retryable read: SendInput may have partially succeeded.
        self.input_attempted = False
        from conquest.merchants.coordination import observe_manual_farmer
        observer=getattr(self,'observer',None)
        if observer is not None and observe_manual_farmer(observer):
            raise TownObservationUnavailable('Manual trade observation requires a fresh read before town actions')
        try:
            return self.execute(body)
        except ValueError as error:
            if not self.input_attempted and transient_observation(error):
                raise TownObservationUnavailable(str(error)) from error
            raise

    def verified_read(self, read, accept, failure, timeout=2):
        # Once input is sent, retry observation only, never the transaction.
        deadline = time.monotonic()+timeout
        while time.monotonic() < deadline:
            try:
                result = read()
                if accept(result):
                    return result
            except ValueError:
                pass
            time.sleep(.05)
        raise ValueError(failure)

    def execute(self, body):
        if body=={'action':'clear-travel-panels'}:
            from conquest.game_panels import close_one
            return {'closed_panel':close_one(self)}
        if body.get('action')=='consume-healing' and set(body)=={'action','uid'}:
            from conquest.healing import consume_inventory_potion
            return consume_inventory_potion(self,body['uid'])
        if body.get('action','').startswith('service-'):
            from conquest.market_services import execute
            result=execute(self,body)
            if result is not None:return result
        if body=={'action':'ground-items'}:
            from conquest.memory_ground import MemoryGroundReader,wanted_drop
            life=self.life(any_map=True)
            drops=MemoryGroundReader(self.observer.entities).read()
            return {'source':'read_only_memory','map_id':life.map_id,'position':life.position,
                    'drops':[{**asdict(drop),'wanted':wanted_drop(drop)} for drop in drops]}
        if body=={'action':'conductress-open'}:
            from conquest.conductress import read_conductress
            npc=read_conductress(self.observer)
            if read_conductress(self.observer)!=npc:raise ValueError('Conductress changed during observation')
            self.input_attempted=True
            self.click_npc(npc,lambda:read_conductress(self.observer))
            self.conductress_npc=npc
            self.service_npc=npc  # The shared geometry reader can scroll this dialogue.
            return {'interacted':True,'npc_id':npc.entity_id}
        if set(body)=={'action','destination'} and body['action']=='conductress-travel':
            from conquest.conductress import read_conductress,destination_point
            npc=read_conductress(self.observer)
            if npc!=getattr(self,'conductress_npc',None):raise ValueError('Open this Conductress before selecting a destination')
            point=destination_point(self.observer,body['destination'])
            if self.inventory.read().silver<100:raise ValueError('Conductress requires 100 silver')
            if read_conductress(self.observer)!=npc or destination_point(self.observer,body['destination'])!=point:
                raise ValueError('Conductress changed before departure')
            self.input_attempted=True
            self.click(point)
            self.conductress_npc=None
            return {'destination_selected':body['destination'],'point':point,'npc_id':npc.entity_id}
        action = body.get('action')
        if action=='warehouse-items' and (set(body)=={'action'} or
                set(body)=={'action','rich'} and body['rich'] is True):
            self.vendor(0)
            reader=MemoryWarehouseReader.for_session(self.observer.adapter)
            if body.get('rich'):
                from conquest.merchants.memory import MerchantMemory
                memory=MerchantMemory(self.observer)
                return asdict(reader.read(rich_item=memory.item))
            return asdict(reader.read())
        if action=='return-scroll' and set(body)=={'action'}:
            from conquest.return_scroll import use
            return use(self)
        if action=='open-bank' and set(body)=={'action'}:
            from conquest.memory_warehouse import WarehouseMoneyReader
            npc=self.vendor(0);reader=WarehouseMoneyReader.for_session(self.observer.adapter)
            try:
                reader.read()
                return {'opened':True,'npc_id':npc.entity_id}
            except ValueError as error:
                if 'not active' not in str(error) and 'absent' not in str(error):raise
            self.input_attempted=True
            self.click_npc(npc,lambda:self.vendor(0))
            self.verified_read(reader.read,lambda b:True,'Warehouse opening unverified; no repeat input issued')
            return {'opened':True,'npc_id':npc.entity_id}
        if action=='warehouse-locate' and set(body)=={'action'}:
            from conquest.memory_npcs import warehouse_identity
            life=self.life(any_map=True);identity=warehouse_identity(self.observer.entities,life.map_id)
            found=MemoryNpcReader(self.observer.entities,vendors=[identity]).read(life.map_id).npcs
            if len(found)!=1:raise ValueError('Warehouseman not in the memory scene')
            return {'npc_id':found[0].entity_id,'position':found[0].position,'map_id':life.map_id}
        if action=='warehouse-money' and set(body)=={'action'}:
            from conquest.memory_warehouse import WarehouseMoneyReader
            self.vendor(0)
            bank=WarehouseMoneyReader.for_session(self.observer.adapter).read()
            return {'silver':self.inventory.read().silver,'stored_silver':bank.silver,'amount':bank.amount}
        if action in ('warehouse-money-deposit','warehouse-money-withdraw') and set(body)=={'action','amount'}:
            from conquest.warehouse_money import transfer
            return transfer(self,action.rsplit('-',1)[1],body['amount'])
        if action=='vendor-status' and set(body)=={'action','vendor_type'}:
            try:
                npc=self.vendor(body['vendor_type'])
                point=interaction_point(npc)
                from conquest.viewport import clear_scene
                return {'reachable':clear_scene(point,size_for(self.observer)),
                        'npc_id':npc.entity_id,'position':npc.position,'point':point}
            except ValueError as error:
                return {'reachable':False,'detail':str(error)}
        if action=='gear' and set(body)=={'action'}:
            from conquest.equipment import read_equipment
            return read_equipment(self.observer)
        if action=='buy-equipment' and set(body)=={'action','vendor_type','type_id'}:
            from conquest.equipment import read_equipment,upgrade_candidate,category,VENDORS,RESERVE_SILVER
            from conquest.foreground import foreground_scroll
            if body['vendor_type'] not in VENDORS:raise ValueError('Unsupported equipment vendor')
            npc=self.vendor(body['vendor_type'])
            for _ in range(20):
                shop=self.shop.read(npc.entity_id)
                choices=[p for p in shop.products if p.type_id==body['type_id']]
                if len(choices)!=1:raise ValueError('Equipment absent from live shop')
                product=choices[0]
                state=read_equipment(self.observer)
                if category(product.type_id) not in VENDORS[body['vendor_type']] or not upgrade_candidate(product,state):
                    raise ValueError('Item is not an eligible archer upgrade')
                before=self.inventory.read()
                if len(before.items)>=before.capacity-1 or before.silver<product.price+RESERVE_SILVER:
                    raise ValueError('Equipment budget or inventory reserve unavailable')
                if any(i.type_id==product.type_id for i in before.items):
                    raise ValueError('Matching upgrade already carried; no duplicate purchase')
                try:point=shop.point(product)
                except ValueError as error:
                    if 'qualified visible' not in str(error):raise
                    y=shop.grid.position[1]+32+80*(product.index//5)-shop.grid.scroll[1]
                    ticks=2 if y<shop.grid.position[1]+8 else -2
                    self.life()
                    foreground_scroll(self.observer.operations.target,
                        (round(shop.grid.position[0]+120),round(shop.grid.position[1]+160)),ticks,
                        expected_size=size_for(self.observer))
                    continue
                if self.shop.read(npc.entity_id)!=shop or self.vendor(body['vendor_type'])!=npc:
                    raise ValueError('Shop or vendor changed before buying')
                self.input_attempted=True
                self.click(point,'right')
                after=self.verified_read(self.inventory.read,
                    lambda b:len([i for i in b.items if i.type_id==product.type_id and i.uid not in {j.uid for j in before.items}])==1
                        and b.silver==before.silver-product.price,
                    'Equipment purchase unverified; no repeat purchase issued')
                bought=next(i for i in after.items if i.type_id==product.type_id and i.uid not in {j.uid for j in before.items})
                return {'bought':product.type_id,'uid':bought.uid,'price':product.price,'level':product.level}
            raise ValueError('Equipment could not be scrolled into the live shop viewport')
        if action in ('equip','equip-arrows') and set(body)=={'action','uid'}:
            from conquest.equipment import read_equipment,item_details,upgrade_candidate,category,equip_receipt
            from conquest.addressing import resolve_player
            from conquest.discard_loot import inventory_button
            import struct
            self.life(any_map=action=='equip-arrows')
            try:self.shop.gui.read('Shop')
            except ValueError as error:
                if 'not active' not in str(error) and 'absent' not in str(error):raise
            else:raise ValueError('Close shop before equipping')
            before=self.inventory.read();state=read_equipment(self.observer)
            matches=[i for i in before.items if i.uid==body['uid']]
            if len(matches)!=1:raise ValueError('Equipment UID not carried')
            item=matches[0];layout=self.inventory.layout;s=self.observer.adapter
            actor=resolve_player(s,self.inventory.player_layout)['object']
            table,capacity,first,count=struct.unpack('<4Q',s.read_block(actor+layout.deque_map,32))
            if not 0<count<=40 or not count<=capacity<=256 or capacity&(capacity-1):raise ValueError('Invalid inventory equipment lookup')
            shared=struct.unpack('<Q',s.read_block(table+((first+item.slot)%capacity)*8,8))[0]
            pointer=struct.unpack('<Q',s.read_block(shared,8))[0]
            details=item_details(s,pointer,self.shop.base)
            from conquest.arrow_upgrades import eligible_arrow
            eligible=eligible_arrow(details,state) if action=='equip-arrows' else upgrade_candidate(details,state)
            if details['uid']!=item.uid or details['type_id']!=item.type_id or not eligible:
                raise ValueError('Carried item is not an eligible equipment upgrade')
            try:self.shop.gui.read('Inventory')
            except ValueError as error:
                if 'not active' not in str(error) and 'absent' not in str(error):raise
                self.click(inventory_button(self.shop.gui))
                self.verified_read(lambda:self.shop.gui.read('Inventory'),bool,'Inventory opening unverified')
            grid=self.shop.gui.read('Inventory/##ItemGrid_')
            if grid.size!=(407.,175.) or grid.scroll!=(0.,0.):raise ValueError('Inventory equip grid differs')
            if self.inventory.read().items!=before.items or read_equipment(self.observer)!=state:
                raise ValueError('Equipment changed before equip input')
            point=(round(grid.position[0]+20+40*(item.slot%10)),round(grid.position[1]+20+40*(item.slot//10)))
            self.input_attempted=True
            self.click(point,'right')
            slot=category(item.type_id)
            after=self.verified_read(lambda:(self.inventory.read(),read_equipment(self.observer)),
                lambda pair:equip_receipt(item.uid,slot,before,state,*pair),
                'Equip unverified; item preserved and no repeat input issued',timeout=3)
            return {'equipped':item.uid,'type_id':item.type_id,'slot':slot,'level':details['level'],
                    'replaced_uid':state['equipment'].get(slot,{}).get('uid')}

        if action == 'discard-loot' and set(body)=={'action','uid'}:
            from conquest.discard_loot import DiscardLoot
            if not hasattr(self,'discarder'):
                self.discarder=DiscardLoot(self)
            self.input_attempted=True  # An opening hotkey may precede the drag.
            return self.discarder.discard(body['uid'])
        if action == 'warehouse-deposit' and set(body)=={'action','uid'}:
            return self.warehouse_deposit(body['uid'])
        if action=='warehouse-withdraw-meteor' and set(body)=={'action','uid'}:
            from conquest.memory_warehouse import withdrawal_received
            npc=self.vendor(0);reader=MemoryWarehouseReader.for_session(self.observer.adapter)
            before=self.inventory.read();stored=reader.read()
            candidates=[i for i in stored.items if i.uid==body['uid'] and i.type_id==1088001 and i.amount==i.limit==1]
            if len(candidates)!=1 or len(before.items)>=before.capacity:
                raise ValueError('One stored Meteor and a free inventory slot are required')
            item=candidates[0];grid=reader.gui.read('Warehouse/ScrollingRegion_')
            if grid.size!=(272.,326.) or grid.scroll!=(0.,0.) or not 0<=item.slot<48:
                raise ValueError('Meteor warehouse slot is not in the qualified visible grid')
            point=(round(grid.position[0]+20+40*(item.slot%6)),round(grid.position[1]+20+40*(item.slot//6)))
            fresh=self.inventory.read()
            if (fresh.items!=before.items or fresh.silver!=before.silver or fresh.equipped_ammo!=before.equipped_ammo
                    or reader.read()!=stored or self.vendor(0)!=npc or reader.gui.read('Warehouse/ScrollingRegion_')!=grid):
                raise ValueError('Warehouse changed before Meteor withdrawal')
            self.input_attempted=True
            self.click(point,'left')
            self.verified_read(lambda:(self.inventory.read(),reader.read()),
                lambda pair:withdrawal_received(item,before,stored,*pair),
                'Meteor withdrawal unverified; no repeat input issued',timeout=3)
            return {'withdrawn':item.uid,'type_id':item.type_id,'verified_in_inventory':True}
        if action=='warehouse-withdraw-protected' and set(body)=={
                'action','plan_id','operation_id','uid'}:
            from conquest.protected_withdrawal import withdraw
            return withdraw(self,body['plan_id'],body['operation_id'],body['uid'])
        if action=='warehouse-withdraw-scroll' and set(body)=={'action','operation_id','uid'}:
            from conquest.scroll_withdrawal import withdraw
            return withdraw(self,body['operation_id'],body['uid'])
        if action=='warehouse-reconcile-scroll' and set(body)=={'action','operation_id','uid'}:
            from conquest.scroll_withdrawal import reconcile
            return reconcile(self,body['operation_id'],body['uid'])
        if action == 'warehouse-open' and set(body)=={'action'}:
            npc=self.vendor(0)
            if self.vendor(0)!=npc:
                raise ValueError('Vendor moved before interaction')
            self.input_attempted=True
            self.click_npc(npc,lambda:self.vendor(0),
                           point=(npc.draw_position[0],npc.draw_position[1]-32))
            return {'interacted':True,'vendor_id':npc.entity_id,'position':npc.position}
        if action == 'supplies' and set(body) == {'action'}:
            self.life(0,any_map=True)
            return asdict(self.inventory.read())
        if action == 'open' and set(body) == {'action','vendor_type'}:
            npc = self.vendor(body['vendor_type'])
            try:
                self.shop.read(npc.entity_id)
                return {'opened':True,'vendor_id':npc.entity_id}
            except ValueError as error:
                if transient_observation(error):
                    raise
            fresh = self.vendor(body['vendor_type'])
            if fresh != npc:
                raise ValueError('Vendor moved before interaction')
            self.input_attempted = True
            self.click_npc(npc,lambda:self.vendor(body['vendor_type']))
            time.sleep(.25)
            result = self.verified_read(lambda:self.shop.read(npc.entity_id),lambda value:True,
                'Shop opening was not verified; no repeat input issued')
            return {'opened':True,'vendor_id':npc.entity_id,
                    'products':[asdict(p) for p in result.products]}
        if action == 'shop' and set(body) == {'action','vendor_type'}:
            npc = self.vendor(body['vendor_type'])
            return asdict(self.shop.read(npc.entity_id))
        if action == 'close' and set(body) == {'action','window'}:
            if body['window'] not in ('Shop','Inventory','Warehouse'):
                raise ValueError('Only town panels can be closed')
            try:
                window = self.shop.gui.read(body['window'])
            except ValueError as error:
                if 'not active' in str(error) or 'absent' in str(error):
                    return {'closed':True}
                raise
            from conquest.game_panels import PANELS,TRANSACTIONS
            from conquest.merchants.memory import GuiReader
            def panel_signature():
                return tuple(sorted((item['name'],item['address'],tuple(item['geometry']),tuple(item['scroll']))
                    for item in GuiReader.for_session(self.observer.adapter).windows()
                    if (item['name'] in PANELS or item['name'] in TRANSACTIONS
                        or item['name'].startswith(body['window']+'/'))))
            panels=panel_signature()
            self.input_attempted = True
            from conquest.panel_close import click_close
            click_close(self,body['window'])
            def closed():
                try:self.shop.gui.read(body['window'])
                except ValueError as error:
                    if 'not active' in str(error) or 'absent' in str(error):return True
                    raise
                return False
            try:
                self.verified_read(closed,bool,'Town panel close was not verified',timeout=2)
            except ValueError as error:
                if str(error) != 'Town panel close was not verified':
                    raise
                # Closing a display panel has no financial or ownership side
                # effect.  Retry it once only after fresh memory proves that
                # this is the exact same still-open panel instance.
                self.observer.adapter.assert_identity()
                try:current=self.shop.gui.read(body['window'])
                except ValueError as current_error:
                    if 'not active' in str(current_error) or 'absent' in str(current_error):
                        return {'closed':True}
                    raise
                if current != window:
                    raise ValueError('Town panel changed after its close attempt') from error
                def retry_guard():
                    self.observer.adapter.assert_identity()
                    current_panels=panel_signature()
                    if current_panels != panels:
                        raise ValueError('Town panel set changed before retrying its close')
                    if any(name in TRANSACTIONS for name,*_ in current_panels):
                        raise ValueError('Transaction dialog appeared before retrying panel close')
                retry={'submitted':False}
                click_close(self,body['window'],validate=retry_guard,
                            before_mouse_down=lambda:retry.update(submitted=True))
                if not retry['submitted']:
                    raise ValueError('Town panel close retry did not reach its input boundary')
                self.verified_read(closed,bool,'Town panel close was not verified',timeout=2)
            return {'closed':True}
        if action == 'buy' and set(body) == {'action','vendor_type','type_id'}:
            if body['type_id'] not in (1000020,1050000,1050001,1050002,1060020):
                raise ValueError('Unsupported healing or normal archer ammunition')
            npc = self.vendor(body['vendor_type'])
            shop = self.shop.read(npc.entity_id)
            matches = [p for p in shop.products if p.type_id == body['type_id']]
            if len(matches) != 1:
                raise ValueError('Requested supplies are not sold in this shop')
            product = matches[0]
            if body['type_id'] in (1050001,1050002):
                from conquest.arrow_upgrades import eligible_arrow
                from conquest.equipment import read_equipment
                if not eligible_arrow(product,read_equipment(self.observer)):
                    raise ValueError('Arrow tier is not usable at the current level')
            if product.type_id==1060020 and (product.price!=200 or body['vendor_type']!=3):
                raise ValueError('Return scroll requires the verified Pharmacist price')
            if product.price <= 0:
                raise ValueError('Supply purchase requires a positive verified silver price')
            before = self.inventory.read()
            if product.type_id in (1050000,1050001,1050002):
                from conquest.arrow_upgrades import require_arrow_purchase_room
                require_arrow_purchase_room(before)
            if before.silver < product.price or len(before.items) >= before.capacity:
                raise ValueError('Insufficient funds or inventory room to restock')
            fresh = self.shop.read(npc.entity_id)
            if fresh != shop or self.vendor(body['vendor_type']) != npc:
                raise ValueError('Shop or vendor changed before buying')
            point = shop.point(product)
            self.input_attempted = True
            self.click(point, 'right')
            after = self.verified_read(self.inventory.read,
                lambda after:after.count(product.type_id) > before.count(product.type_id)
                    and after.silver == before.silver-product.price,
                'Purchase was not verified; no repeat purchase issued')
            return {'bought':product.type_id,'amount':after.count(product.type_id)-before.count(product.type_id),
                    'price':product.price,'silver':after.silver}
        if action in ('sell','sell_partial_arrow') and set(body) == {'action','vendor_type','uid'}:
            npc = self.vendor(body['vendor_type'])
            shop = self.shop.read(npc.entity_id)
            before = self.inventory.read()
            candidates = [i for i in before.items if i.uid == body['uid'] and (
                sale_candidate(i) if action=='sell' else
                body['vendor_type']==5 and expendable_arrow(i,before))]
            if len(candidates) != 1:
                raise ValueError('Selected item is absent or protected from automatic sale')
            item = candidates[0]
            grid = self.shop.gui.read('Inventory/##ItemGrid_')
            if grid.size != (407.,175.) or grid.scroll != (0.,0.):
                raise ValueError('Inventory grid differs from its calibrated layout')
            current = self.inventory.read()
            if current != before:
                # Timestamps differ even when inventory contents are stable.
                if current.items != before.items or current.silver != before.silver:
                    raise ValueError('Inventory changed before selling')
            point = (round(grid.position[0]+20+40*(item.slot%10)),
                     round(grid.position[1]+20+40*(item.slot//10)))
            destination = (round(shop.window.position[0]+shop.window.size[0]/2),
                           round(shop.window.position[1]+shop.window.size[1]-31))
            self.life()
            self.input_attempted = True
            foreground_drag(self.observer.operations.target,point,destination,size_for(self.observer))
            after = self.verified_read(self.inventory.read,
                lambda after:item.uid not in {i.uid for i in after.items} and after.silver > before.silver,
                'Sale was not verified; no further sale issued')
            return {'sold':item.uid,'type_id':item.type_id,'plus':item.plus,'silver_gained':after.silver-before.silver}
        raise ValueError('Unsupported town action')
