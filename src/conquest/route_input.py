"""Foreground jumps shared by saved route travel and death return."""
from conquest.character_context import installation_path
from dataclasses import asdict
import time

from conquest.memory_life import read_life
from conquest.route_recovery import EmbeddedRecoveryInput


class RouteJumpInput:
    def __init__(self,observer,terrain):
        self.observer,self.terrain=observer,terrain
        self.recovery_input=EmbeddedRecoveryInput(observer,None,terrain=terrain)

    def __call__(self,body):
        portal='portal_id' in body
        if set(body)!={'source','destination','map_id','expires_at'}|({'portal_id'} if portal else set()):
            raise ValueError('Unsupported route jump arguments')
        source,destination=body['source'],body['destination']
        if any(not isinstance(p,list) or len(p)!=2 or any(type(v) is not int for v in p)
               for p in (source,destination)):
            raise ValueError('Route coordinates must be integer tile pairs')
        observer=self.observer
        life=observer.read_life() if hasattr(observer,'read_life') else read_life(observer.adapter,observer.health_layout,observer.character)
        if (type(body['map_id']) is not int or life.map_id!=body['map_id']
                or list(life.position)!=source or life.dead_candidate):
            raise ValueError('Route map changed')
        if body['map_id']!=self.terrain.map_id:
            from conquest.navigation import read_terrain
            self.terrain=read_terrain(installation_path(r'C:\Program Files\Classic Conquer 2.0'),body['map_id'])
            self.recovery_input.terrain=self.terrain
        dx,dy=destination[0]-source[0],destination[1]-source[1]
        distance=max(abs(dx),abs(dy))
        if not 1<=distance<=12:
            raise ValueError('Route movement must cover one to twelve tiles')
        if portal:
            if (type(body['portal_id']) is not int or tuple(destination)+(body['portal_id'],) not in self.terrain.portals
                    or distance>3 or (dx and dy) or not self.terrain.walkable(tuple(source))):
                raise ValueError('Portal entry must approach a known portal from an adjacent safe tile')
            # The ordinary planner intentionally excludes the portal's 3x3 guard.
            # Only an explicit portal action may walk into that guarded area.
            segment=[tuple(source)]
        elif dx and dy and abs(dx)+abs(dy)<=4:
            # Short corner runs use the client's pathfinder, never a diagonal jump.
            segment=self.terrain.path(tuple(source),tuple(destination),limit=1000)
            if len(segment)-1>4:
                raise ValueError('Corner run requires a longer terrain detour')
        else:
            from conquest.navigation import clear_segment,line_tiles
            if not clear_segment(self.terrain,source,destination):
                raise ValueError('Route jump crosses blocked terrain')
            segment=line_tiles(source,destination)
        if not all(self.terrain.walkable(p) for p in segment):
            raise ValueError('Route jump crosses blocked terrain')
        life=observer.read_life() if hasattr(observer,'read_life') else read_life(observer.adapter,observer.health_layout,observer.character)
        if list(life.position)!=source or life.map_id!=body['map_id']:
            raise ValueError('Player left the planned route segment')
        movement='jump' if distance>=8 else 'run'
        self.recovery_input.send(movement,tuple(destination),asdict(life))
        return {'source':source,'destination':destination,'movement':movement,'issued':True}


class BridgeJumpStepper:
    def __init__(self,worker_info,*,on_life=None):
        self.worker_info=worker_info
        self.on_life=on_life

    def health(self):
        from conquest.worker import request
        health=request(self.worker_info,'health')
        if self.on_life is not None:
            self.on_life(health)
        return health

    def observe(self):
        from types import SimpleNamespace
        health=self.health()
        life=health['embedded_controls']['life']
        return SimpleNamespace(position=tuple(life['position']))

    def step_to(self,destination,*,expected_position=None):
        from conquest.worker import request
        health=self.health()
        life=health['embedded_controls']['life']
        source=list(expected_position if expected_position is not None else life['position'])
        expected_map=life['map_id']
        result={'source':source,'destination':list(destination),'reached':False,'samples':[]}
        result['input']=request(self.worker_info,'route-jump',{'source':source,
            'destination':list(destination),'map_id':life['map_id'],'expires_at':time.time()+4})
        started=time.monotonic()
        deadline=started+5
        last_progress=started
        last_position=tuple(source)
        previous_hp=life.get('current_hp');damage_at=-float('inf')
        previous=None
        consecutive=0
        while time.monotonic()<deadline:
            from conquest.travel_care import PanelTravelChanged,TravelStateChanged
            try:
                health=self.health()
            except PanelTravelChanged as error:
                # Input was already issued. Typed interception avoids treating
                # unrelated route-care state changes as a failed landing.
                result.update(error='Route click intercepted by a shop panel',
                              outcome='panel_intercepted',panel=error.panel,
                              stalled_at=list(last_position),elapsed=time.monotonic()-started)
                return result
            except TravelStateChanged as error:
                # Compatibility for an older TravelCare instance surviving an
                # in-place code reload; new instances raise PanelTravelChanged.
                if str(error)=='Closed a shop panel; rechecking the route':
                    result.update(error='Route click intercepted by a shop panel',
                                  outcome='panel_intercepted',panel=None,
                                  stalled_at=list(last_position),elapsed=time.monotonic()-started)
                    return result
                raise
            life=health['embedded_controls'].get('life')
            if life is None:
                time.sleep(.05)
                continue
            window=health['window']
            if life['dead_candidate'] or life['map_id']!=expected_map:
                raise ValueError('Death or map change interrupted the route')
            if window['foreground']!=window['root_hwnd'] or window['minimized']:
                raise ValueError('Game focus changed during route movement')
            if life['timestamp']!=previous:
                previous=life['timestamp']
                result['samples'].append(life['position'])
                position=tuple(life['position'])
                current_hp=life.get('current_hp')
                if current_hp is not None and previous_hp is not None and current_hp<previous_hp:damage_at=time.monotonic()
                previous_hp=current_hp
                if position!=last_position:
                    last_position=position
                    last_progress=time.monotonic()
                consecutive=consecutive+1 if tuple(life['position'])==tuple(destination) else 0
                settle = .4 if result['input'].get('movement')=='jump' else .1
                if consecutive>=2 and time.monotonic()>=deadline-5+settle:
                    result['reached']=True
                    return result
                # Replan a blocked start or a stalled partial run promptly.
                # Care still runs on every health poll; moving characters keep
                # their full arrival budget and are never clicked repeatedly.
                stall_limit=.5 if time.monotonic()-damage_at<2 else 1
                if position!=tuple(destination) and time.monotonic()-last_progress>=stall_limit:
                    result.update(error='Route movement stopped progressing',
                                  stalled_at=list(position),elapsed=time.monotonic()-started)
                    return result
            time.sleep(.05)
        result['error']='Route jump arrival was not observed'
        return result
