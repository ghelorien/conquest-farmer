"""One background scene step with position feedback and no desktop input.

The current projection was checked against two opposite live world steps. HUD,
combat hit boxes, other viewport sizes and minimization are separate capabilities.
"""
from dataclasses import dataclass
from pathlib import Path
import struct
import time


@dataclass(frozen=True)
class SceneReading:
    position: tuple[int, int]
    window: dict
    observed_at: float
    map_id: int | None = None
    hp: int | None = None
    max_hp: int | None = None


@dataclass(frozen=True)
class SceneProjection:
    viewport: tuple[int, int] = (1036, 793)
    anchor: tuple[int, int] = (518, 396)
    half_tile: tuple[int, int] = (32, 16)

    def point(self, source, destination, max_delta=1):
        if any(len(pair)!=2 for pair in (source,destination)):
            raise ValueError('World positions need two coordinates')
        if any(type(v) is not int for pair in (source, destination) for v in pair):
            raise ValueError('World positions must contain integer tile coordinates')
        dx, dy = destination[0]-source[0], destination[1]-source[1]
        if type(max_delta) is not int or not 1<=max_delta<=6:
            raise ValueError('Maximum movement delta must be 1 to 6 tiles')
        if not 0 < max(abs(dx),abs(dy)) <= max_delta:
            raise ValueError('Scene movement exceeds the bounded waypoint delta')
        point = (self.anchor[0]+(dx-dy)*self.half_tile[0],
                 self.anchor[1]+(dx+dy)*self.half_tile[1])
        if not (0 <= point[0] < self.viewport[0] and 0 <= point[1] < self.viewport[1]):
            raise ValueError('Projected tile falls outside the calibrated viewport')
        return point

    def adjacent_point(self, source, destination):
        if abs(destination[0]-source[0])+abs(destination[1]-source[1]) != 1:
            raise ValueError('Scene movement accepts exactly one adjacent tile')
        return self.point(source,destination)


class BackgroundSceneStepper:
    """Serialize externally; click(point, viewport) must queue one message pair.

    This class never enables farming and never retries an uncertain action.
    The callback should use the existing authenticated, HP-guarded worker.
    """
    def __init__(self, observe, click, *, projection=SceneProjection(),
                 expected_map=None, allowed_bounds=None, max_delta=1,
                 require_unfocused=True, clock=time.monotonic, sleep=time.sleep):
        self.observe, self.click, self.projection = observe, click, projection
        self.clock, self.sleep = clock, sleep
        self.expected_map, self.allowed_bounds = expected_map, allowed_bounds
        self.max_delta, self.require_unfocused = max_delta, require_unfocused
        if allowed_bounds is not None:
            if (len(allowed_bounds)!=4 or any(type(v) is not int for v in allowed_bounds)
                    or allowed_bounds[0]>allowed_bounds[2] or allowed_bounds[1]>allowed_bounds[3]):
                raise ValueError('Allowed bounds must be minimum x/y then maximum x/y')

    def _in_bounds(self,position):
        if self.allowed_bounds is not None:
            x0,y0,x1,y1=self.allowed_bounds
            if not (x0<=position[0]<=x1 and y0<=position[1]<=y1):
                raise ValueError('Player or waypoint is outside the permitted map bounds')

    def _valid(self, reading):
        window = reading.window
        if not 0 <= self.clock()-reading.observed_at <= .5:
            raise ValueError('Scene observation expired')
        if window['minimized'] or (self.require_unfocused and
                window['foreground'] == window.get('root_hwnd', window['hwnd'])):
            raise ValueError('Background movement requires another app foreground')
        if tuple(window['client_size']) != self.projection.viewport:
            raise ValueError('Scene projection does not match the viewport')
        if self.expected_map is not None and reading.map_id != self.expected_map:
            raise ValueError('Player changed map or the map observation is unavailable')
        self._in_bounds(reading.position)
        if reading.hp is not None and (reading.max_hp is None or reading.max_hp<=0
                or not 0 < reading.hp <= reading.max_hp or reading.hp/reading.max_hp < .4):
            raise ValueError('Player HP is too low for movement')

    def step(self, source, destination):
        source, destination = tuple(source), tuple(destination)
        point = self.projection.point(source, destination,self.max_delta)
        self._in_bounds(source)
        self._in_bounds(destination)
        before = self.observe()
        self._valid(before)
        if before.position != source:
            raise ValueError('Player moved from the planned starting tile')
        self.sleep(.2)
        fresh = self.observe()
        self._valid(fresh)
        if fresh.position != source or fresh.window != before.window:
            raise ValueError('Scene or desktop changed before movement; no input sent')
        report = {'source':list(source), 'destination':list(destination), 'point':list(point),
                  'reached':False, 'outcome':'uncertain', 'samples':[], 'observations':[],
                  'window_before':before.window,'cursor_changed':False}
        try:
            report['input'] = self.click(point, self.projection.viewport)
            consecutive = 0
            for _ in range(25):
                reading = self.observe()
                # Preserve the actual result even when validation rejects it.
                # A user's cursor movement cannot redirect a queued HWND click.
                report['samples'].append(list(reading.position))
                report['observations'].append({'position':list(reading.position),
                    'window':reading.window,'map_id':reading.map_id,'hp':reading.hp,
                    'max_hp':reading.max_hp,'observed_at':reading.observed_at})
                changes={key:{'before':before.window.get(key),'after':reading.window.get(key)}
                         for key in before.window.keys() | reading.window.keys()
                         if before.window.get(key)!=reading.window.get(key)}
                if 'cursor' in changes:
                    report['cursor_changed']=True
                if changes:
                    report['observations'][-1]['window_changes']=changes
                self._valid(reading)
                if changes.keys()-{'cursor'}:
                    raise ValueError('Focus or window geometry changed after movement')
                if not (min(source[0],destination[0])<=reading.position[0]<=max(source[0],destination[0])
                        and min(source[1],destination[1])<=reading.position[1]<=max(source[1],destination[1])):
                    raise ValueError('Player moved to an unexpected tile outside the waypoint segment')
                consecutive = consecutive+1 if reading.position == destination else 0
                if consecutive >= 3:
                    report.update(reached=True, outcome='reached')
                    return report
                self.sleep(.2)
            report['outcome'] = 'destination_not_observed'
        except Exception as error:
            report['error'] = str(error)
        return report


class BridgeSceneStepper:
    """Authenticated live adapter for map-checked route waypoints.

    Bounds are inclusive (minimum x, minimum y, maximum x, maximum y). The route
    planner must prove that the segment is walkable; this adapter proves arrival.
    The current worker endpoint requires the game to remain unfocused even when
    require_unfocused=False relaxes the local observation guard.
    """
    def __init__(self, worker_info, player_profile, health_profile, character,
                 *, expected_map, allowed_bounds, max_delta=6, require_unfocused=True):
        import yaml
        from conquest.addressing import PlayerLayout
        from conquest.memory_health import HealthLayout, HealthWorkerSession, MemoryHealthReader
        if type(expected_map) is not int or not 1<=expected_map<=65535:
            raise ValueError('Provide the expected numeric map ID')
        if allowed_bounds is None:
            raise ValueError('Live movement requires explicit allowed map bounds')
        def layout(value,model):
            return value if isinstance(value,model) else model.model_validate(
                yaml.safe_load(Path(value).read_text(encoding='utf-8')))
        self.player=layout(player_profile,PlayerLayout)
        self.health=layout(health_profile,HealthLayout)
        if self.player.expected_sha256!=self.health.player.expected_sha256:
            raise ValueError('Player and HP profiles identify different clients')
        if self.player.map_rva is None:
            raise ValueError('Map-checked movement requires a player map address')
        self.worker_info, self.character = worker_info, character
        self.session=HealthWorkerSession(worker_info,self.player.expected_sha256)
        self.health_reader=MemoryHealthReader(self.session,self.health,character)
        self.stepper=BackgroundSceneStepper(self.observe,self._click,expected_map=expected_map,
            allowed_bounds=allowed_bounds,max_delta=max_delta,require_unfocused=require_unfocused)

    def observe(self):
        from conquest.addressing import resolve_player
        addresses=resolve_player(self.session,self.player)
        hp=self.health_reader.read()
        position=tuple(struct.unpack('<II',self.session.read_block(addresses['position'],8)))
        map_id=struct.unpack('<I',self.session.read_block(addresses['map'],4))[0]
        health=self.session.request('health')
        if resolve_player(self.session,self.player)!=addresses:
            raise ValueError('Player pointer changed during movement observation')
        if struct.unpack('<I',self.session.read_block(addresses['map'],4))[0]!=map_id:
            raise ValueError('Player changed map during movement observation')
        # Position is a single contiguous read. Movement between observations is
        # expected while walking; only the pre-dispatch pair must be stationary.
        position=tuple(struct.unpack('<II',self.session.read_block(addresses['position'],8)))
        return SceneReading(position,health['window'],time.monotonic(),map_id,hp.current_hp,hp.max_hp)

    def _click(self,point,viewport):
        return self.session.request('background-click',{
            'health_profile':self.health.model_dump(mode='json'),'character':self.character,
            'point':list(point),'expected_size':list(viewport),'position_cursor':False,
            'move_settle_seconds':.2,'expires_at':time.time()+4})

    def step_to(self,destination,*,expected_position=None):
        source=self.observe().position if expected_position is None else tuple(expected_position)
        return self.stepper.step(source,destination)


def memory_player_anchor(observer,life,*,layout=None):
    """Pinned actor draw coordinates, including camera clamping at map edges."""
    from conquest.memory_life import CLIENT_SHA256
    if layout is None and observer.adapter.expected_sha256!=CLIENT_SHA256:
        raise ValueError('Player projection belongs to a different client build')
    if layout is not None and observer.adapter.expected_sha256!=layout.expected_sha256:
        raise ValueError('Player projection layout differs from client')
    address=life.object_address+0xd8
    raw=observer.adapter.read_block(address,24)
    position=struct.unpack_from('<2I',raw)
    anchor=struct.unpack_from('<2i',raw,16)
    from conquest.viewport import size_for
    width,height=size_for(observer)
    if position!=tuple(life.position) or not (0<anchor[0]<width and 0<anchor[1]<height):
        raise ValueError('Player draw position is unavailable or changed')
    if observer.adapter.read_block(address,24)!=raw:
        raise ValueError('Player projection changed during observation')
    return anchor


def memory_player_anchor_for_session(session,character):
    """Explicit read-only anchor observation; no stepper or click is exposed."""
    from types import SimpleNamespace
    from conquest.memory_build_layout import read_build_layout
    from conquest.memory_life import MemoryLifeReader
    reader=MemoryLifeReader.for_session(session,character);life=reader.read()
    anchor=memory_player_anchor(SimpleNamespace(adapter=reader.session),life,layout=read_build_layout(reader.session))
    latest=MemoryLifeReader.for_session(session,character).read()
    if latest.object_address!=life.object_address or latest.position!=life.position or latest.dead_candidate:
        raise ValueError('Player changed during anchor observation')
    reader.session.assert_identity()
    return anchor


def clear_route_point(point,bounds=(80,140,956,667)):
    """Exclude HUD/chat at the actual camera anchor, not the default center."""
    from conquest.viewport import clear_scene
    x,y=point;left,top,right,bottom=bounds
    # Bounds come from scene_bounds; share its popup exclusions as well as
    # its outside edges. Otherwise shortening can re-accept a blocked point.
    return left<x<right and top<y<bottom and clear_scene(point,(right+80,bottom+126))


def visible_route_delta(delta,anchor,bounds=(80,140,956,667)):
    """Shorten a checked straight segment until its click clears the HUD."""
    dx,dy=delta
    distance=int(max(abs(dx),abs(dy)))
    if distance<1:return None
    left,top,right,bottom=bounds
    for steps in range(distance,0,-1):
        x,y=round(dx*steps/distance),round(dy*steps/distance)
        px,py=anchor[0]+(x-y)*32,anchor[1]+(x+y)*16
        if clear_route_point((px,py),bounds):return x,y
    return None
