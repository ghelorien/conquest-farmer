"""Normal foreground route input, guarded by live merchant qualification."""
from conquest.character_context import merchant_installation
import struct
import time
from conquest.capture import CaptureUnavailable
from conquest.memory_life import read_life
from conquest.navigation import read_terrain,clear_segment


def transit_waypoint(terrain,path,anchor,viewport):
    """Choose a checked landing inside the current camera's visible scene."""
    width,height=viewport;source=path[0]
    for target in reversed(path[1:25]):
        dx,dy=target[0]-source[0],target[1]-source[1]
        if max(abs(dx),abs(dy))>12:continue
        point=(anchor[0]+(dx-dy)*32,anchor[1]+(dx+dy)*16)
        if (80<point[0]<width-80 and 170<point[1]<height-160
                and clear_segment(terrain,source,target)):
            return target
    raise ValueError('No visible checked Market route landing')


def stall_approach(terrain,position,flag):
    """Approach the flag outside its presumed booth standing tile."""
    x,y=flag['position'];candidates=[]
    for target in ((x-2,y),(x,y-2),(x,y+2)):
        try:path=terrain.travel_path(tuple(position),target)
        except ValueError:continue
        candidates.append((len(path),target))
    if not candidates:raise ValueError('No checked stall approach is available')
    return min(candidates)


class ReturnDriver:
    def __init__(self, driver, *, travel_only=False):
        self.driver,self.observer=driver,driver.observer
        self.terrains={}
        self.travel_only=travel_only

    def read(self):
        if self.travel_only:
            from conquest.merchants.memory import TransitObservationChanged,GuiObservationChanged
            deadline=time.monotonic()+.4
            while True:
                try:return self.driver.memory.read_travel()
                except (TransitObservationChanged,GuiObservationChanged):
                    if time.monotonic()>=deadline:raise
                    time.sleep(.025)
        return self.driver.memory.read(recovery=True)

    def qualify_movement(self):
        profile=self.driver.require_qualified('market_return')
        # Movement projection/GUI must have been verified for this viewport.
        if (self.driver.memory.gui.viewport_size()!=profile.get('gui_size') or
                list(self.driver.target.snapshot()['client_size'])!=profile.get('client_size')):
            raise ValueError('Merchant return-route viewport needs live qualification')
        return profile

    def click(self, point, check, *, before_press, jump=False):
        from conquest.focus_recovery import activate_client
        from conquest.foreground import foreground_click
        from conquest.desktop_runtime import physical_coordinates
        with physical_coordinates():
            check()
            profile=self.qualify_movement()
            identity=self.observer.adapter.identity
            if not activate_client(self.driver.target.hwnd,identity):
                raise CaptureUnavailable('Return to Market waits for foreground focus')
            gui=profile['gui_size'];size=profile['client_size']
            physical=tuple(round(v*p/g) for v,p,g in zip(point,size,gui))
            def guard():
                check();self.observer.adapter.assert_identity()
                if self.qualify_movement()!=profile:raise ValueError('Route calibration changed')
                before_press()
            diagnostics={}
            result=foreground_click(self.driver.target,*physical,tuple(size),control=jump,
                                    require_foreground=True,before_press=guard,diagnostics=diagnostics)
            return {**result,'diagnostics':diagnostics}

    def move(self, snapshot, destination, check):
        self.qualify_movement()
        o=self.observer
        if snapshot['map_id'] not in self.terrains:
            self.terrains[snapshot['map_id']]=read_terrain(merchant_installation(self.driver.observer.character),snapshot['map_id'])
        terrain=self.terrains[snapshot['map_id']]
        fresh=self.read()
        if any(fresh[k]!=snapshot[k] for k in ('identity','position','map_id')):
            raise CaptureUnavailable('Merchant moved before route planning')
        path=terrain.travel_path(tuple(fresh['position']),destination)
        from conquest.scene_input import memory_player_anchor
        life=read_life(o.adapter,o.health_layout,o.character)
        if life.map_id!=fresh['map_id'] or list(life.position)!=fresh['position']:
            raise CaptureUnavailable('Merchant moved before waypoint selection')
        target=transit_waypoint(terrain,path,memory_player_anchor(o,life),
                                self.driver.memory.gui.viewport_size())
        def point_now():
            life=read_life(o.adapter,o.health_layout,o.character)
            if life.map_id!=snapshot['map_id'] or list(life.position)!=snapshot['position'] or life.dead_candidate:
                raise CaptureUnavailable('Merchant moved before route input')
            raw=o.adapter.read_block(life.object_address+0xd8,24)
            if struct.unpack_from('<2I',raw)!=tuple(life.position):raise ValueError('Route anchor changed')
            anchor=struct.unpack_from('<2i',raw,16)
            dx,dy=target[0]-life.position[0],target[1]-life.position[1]
            point=(anchor[0]+(dx-dy)*32,anchor[1]+(dx+dy)*16)
            width,height=self.driver.memory.gui.viewport_size()
            if not (80<point[0]<width-80 and 170<point[1]<height-160):
                raise ValueError('Route landing is outside the qualified scene')
            # GUI window geometry is memory-derived. Never click through an overlay.
            for window in self.driver.memory.gui.windows():
                x,y,w,h=window['geometry']
                if w>=width-10 and h>=height-10:continue
                if x<=point[0]<=x+w and y<=point[1]<=y+h:
                    raise ValueError('Close the panel covering the return route')
            if not clear_segment(terrain,life.position,target):raise ValueError('Route segment became blocked')
            if o.adapter.read_block(life.object_address+0xd8,24)!=raw:raise ValueError('Route projection changed')
            return point
        point=point_now()
        def guard():
            if point_now()!=point:raise ValueError('Projected return waypoint moved')
            state=self.read()
            if state.get('trade') or state.get('request'):raise CaptureUnavailable('Trade interrupted the return route')
        self.last_move={'before':snapshot['position'],'target':list(target),'point':list(point)}
        self.last_move['input']=self.click(point,check,before_press=guard,
            jump=max(abs(a-b) for a,b in zip(target,snapshot['position']))>=8)
        end=time.monotonic()+3
        while time.monotonic()<end:
            check();after=self.read()
            if after['map_id']!=snapshot['map_id']:raise ValueError('Unexpected map change during return movement')
            self.last_move['after']=after['position']
            if tuple(after['position'])==target:return after
            time.sleep(.1)
        return after

    def prepare_transfer(self, snapshot, check):
        from conquest.conductress import read_conductress,read_dialog
        from conquest.market_services import dialog_point
        self.qualify_movement()
        if snapshot['silver']<100:raise ValueError('Merchant needs 100 silver for the Market transfer')
        npc=read_conductress(self.observer)
        point=(npc.draw_position[0],npc.draw_position[1]-32)
        def guard():
            if read_conductress(self.observer)!=npc:raise ValueError('Conductress moved before input')
        self.click(point,check,before_press=guard)
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            check()
            try:
                records=read_dialog(self.observer)['records']
                if not any(r['kind']==0 and '100 silver' in r['text'] for r in records):
                    raise ValueError('Conductress fare is not the verified 100 silver')
                dialog_point(self.observer,'Market',records)
                return records
            except ValueError:
                time.sleep(.1)
        raise ValueError('Conductress Market option and fare were not verified')

    def transfer(self, records, check):
        from conquest.conductress import read_conductress
        from conquest.market_services import dialog_point
        npc=read_conductress(self.observer)
        point=dialog_point(self.observer,'Market',records)
        def guard():
            if read_conductress(self.observer)!=npc or dialog_point(self.observer,'Market',records)!=point:
                raise ValueError('Conductress transfer changed before confirmation')
        self.click(point,check,before_press=guard)

    def stall(self, preferred):
        from conquest.merchants.stalls import vacant_flags
        profile=self.driver.require_qualified('booth_setup')
        spec=profile.get('shop_setup',{})
        if spec.get('claim_mode') not in ('direct','dialog'):
            raise ValueError('Shop flag claim needs live qualification')
        if spec['claim_mode']=='dialog' and (not spec.get('records') or not spec.get('option')):
            raise ValueError('Shop flag confirmation needs live qualification')
        candidates=vacant_flags(self.observer,spec)
        terrain=read_terrain(merchant_installation(self.driver.observer.character),1036)
        position=self.read()['position']
        for flag in sorted(candidates,key=lambda f:max(abs(a-b) for a,b in zip(f['position'],preferred))):
            try:_,approach=stall_approach(terrain,position,flag)
            except ValueError:continue
            return {'flag':flag,'position':list(approach)}
        raise ValueError('No memory-verified vacant reachable stall is available nearby')

    def prepare_shop(self, chosen, check):
        from conquest.merchants.stalls import vacant_flags
        profile=self.driver.require_qualified('booth_setup')
        flags=vacant_flags(self.observer,profile['shop_setup'])
        flag=next((f for f in flags if f['uid']==chosen['flag']['uid']),None)
        if flag is None:raise CaptureUnavailable('Selected stall was occupied; another will be selected')
        fresh=self.read();check()
        if fresh['map_id']!=1036 or max(abs(a-b) for a,b in zip(fresh['position'],chosen['position']))>1:
            raise ValueError('Merchant is not at the selected stall')
        return {'flag':flag,'snapshot':fresh,'spec':profile['shop_setup']}

    def start_shop(self, prepared, check):
        from conquest.merchants.stalls import vacant_flags
        from conquest.market_services import dialog_point
        flag=prepared['flag'];spec=prepared['spec'];snapshot=prepared['snapshot']
        point=(flag['draw_position'][0],flag['draw_position'][1]-32)
        def guard():
            check();fresh=self.read()
            if any(fresh[k]!=snapshot[k] for k in ('identity','map_id','position')):
                raise ValueError('Merchant moved before claiming the stall')
            if fresh.get('trade') or fresh.get('request'):raise ValueError('Trade interrupted stall setup')
            current=next((f for f in vacant_flags(self.observer,spec) if f['uid']==flag['uid']),None)
            if current!=flag:raise ValueError('Stall was occupied or changed before claiming it')
        self.click(point,check,before_press=guard)
        if spec['claim_mode']=='dialog':
            deadline=time.monotonic()+3
            while time.monotonic()<deadline:
                check()
                try:point=dialog_point(self.observer,spec['option'],spec['records']);break
                except ValueError:time.sleep(.1)
            else:raise ValueError('Stall setup dialog was not verified')
            def confirm_guard():
                guard()
                if dialog_point(self.observer,spec['option'],spec['records'])!=point:
                    raise ValueError('Stall confirmation changed before input')
            self.click(point,check,before_press=confirm_guard)
