"""Find a quiet nearby tile with read-only memory before an app handoff."""
from conquest.character_context import installation_path, state_path
import os
from pathlib import Path
import time

from conquest.discord_notify import read_json,write_json,process_alive

RESUME=Path(state_path('.runtime/reload-resume.json'))
CLEARANCE=24


def clear_observation(health):
    data=health.get('embedded_controls',{})
    life=data.get('life')
    if (not data.get('observations_available') or not isinstance(data.get('monsters'),list)
            or not life or life['dead_candidate']
            or data.get('manual_mouse') or not 0<=time.time()-data.get('observed_at',0)<=1
            or life['current_hp']<life['max_hp']*.6):
        return False
    x,y=life['position']
    # Unknown life is treated as a threat, including unselected monster types.
    return all(m.get('alive') is False or max(abs(m['position'][0]-x),abs(m['position'][1]-y))>=CLEARANCE
               for m in data.get('monsters',[]))


def nearby_escape(terrain,position,monsters,*,anchor=None,avoid=(),viewport=(1036,793)):
    from conquest.navigation import native_waypoint
    from conquest.viewport import clear_scene
    threats=[tuple(m['position']) for m in monsters if m.get('alive') is not False]
    def clearance(p):return min((max(abs(p[0]-x),abs(p[1]-y)) for x,y in threats),default=CLEARANCE)
    choices=[]
    offsets={(dx,dy) for dx in range(-36,37,12) for dy in range(-36,37,12)}
    offsets.update((dx,dy) for dx in range(-4,5) for dy in range(-4,5))
    for dx,dy in sorted(offsets):
            target=(position[0]+dx,position[1]+dy)
            if not(dx or dy) or not terrain.walkable(target):continue
            try:path=terrain.path(tuple(position),target,limit=8000)
            except ValueError:continue
            if len(path)>73:continue
            score=min(CLEARANCE,clearance(target))-(len(path)-1)*.03
            step=native_waypoint(path,12,viewport=viewport)
            if step in avoid:continue
            if anchor:
                sx,sy=step[0]-position[0],step[1]-position[1]
                px,py=anchor[0]+(sx-sy)*32,anchor[1]+(sx+sy)*16
                if not clear_scene((px,py),viewport):continue
            choices.append((score,-len(path),target,step))
    if not choices:return None
    best=max(choices)
    if clearance(best[2])<=clearance(position):return None
    return best[3]


def park(loop,cancelled,notify,*,seconds=120,allow_town_retreat=True):
    """Keep travel healing/revival active until three quiet seconds are verified."""
    from conquest.travel_care import TravelStateChanged
    from conquest.navigation import read_terrain
    stable_since=None;previous=None;started=time.monotonic();deadline=started+seconds;avoided=set()
    retreat=None;retreat_map=None
    while time.monotonic()<deadline:
        if cancelled.is_set():raise ValueError('Reload canceled by user')
        health=loop.living()
        try:loop.care.check(health)
        except TravelStateChanged:continue
        data=health['embedded_controls'];life=data['life']
        if loop.terrain.map_id!=life['map_id']:
            loop.terrain=read_terrain(installation_path(r'C:\Program Files\Classic Conquer 2.0'),life['map_id'])
        if retreat_map is not None and retreat_map!=life['map_id']:
            retreat=None;retreat_map=None
        key=(life['object_address'],life['map_id'],tuple(life['position']))
        if previous and previous[0]!=key:avoided.clear()
        quiet=clear_observation(health)
        unchanged=previous and previous[0]==key and life['current_hp']>=previous[1]
        if quiet:
            if stable_since is None or not unchanged:stable_since=time.monotonic()
            if time.monotonic()-stable_since>=3:
                return {'target':health['target'],'position':life['position'],'map_id':life['map_id'],
                        'hp':life['current_hp'],'verified_at':time.time()}
        else:
            stable_since=None
            from types import SimpleNamespace
            from conquest.scene_input import memory_player_anchor
            try:
                anchor=memory_player_anchor(SimpleNamespace(adapter=loop.care.session),SimpleNamespace(**life))
                viewport=tuple(health.get('window',{}).get('client_size',(1036,793)))
                if allow_town_retreat and retreat is None and time.monotonic()-started>=15:
                    route=getattr(loop,'route',None)
                    if route:
                        candidate=(route.restock_anchor if route.restock_map_id==life['map_id'] else
                                   route.town_anchor if route.map_id==life['map_id'] else None)
                        if candidate and loop.terrain.walkable(candidate):
                            retreat=tuple(candidate);retreat_map=life['map_id']
                            notify('Local area crowded; heading toward town for a safe app reload')
                if retreat is not None and tuple(life['position'])!=retreat:
                    # Keep a fixed destination instead of chasing a new local
                    # clearance maximum every tick in a continuously dense spawn.
                    from conquest.navigation import travel_waypoint,clear_segment
                    from conquest.scene_input import visible_route_delta
                    from conquest.viewport import scene_bounds
                    source=tuple(life['position'])
                    path=loop.terrain.travel_path(source,retreat,avoid=avoided)
                    target=travel_waypoint(loop.terrain,path,avoid=avoided,viewport=viewport)
                    delta=visible_route_delta((target[0]-source[0],target[1]-source[1]),anchor,scene_bounds(viewport))
                    target=(source[0]+delta[0],source[1]+delta[1]) if delta else None
                    if target and not clear_segment(loop.terrain,source,target,avoid=avoided):target=None
                else:
                    target=nearby_escape(loop.terrain,life['position'],data.get('monsters',[]),anchor=anchor,avoid=avoided,
                                         viewport=viewport)
                if target:
                    if retreat is None:notify('Moving to a nearby clear spot before reloading')
                    # One bounded step, then inspect threats again. Never spend a
                    # whole travel deadline trying to reach a stale escape point.
                    result=loop.stepper.step_to(target,expected_position=tuple(life['position']))
                    if not result['reached']:avoided.add(target)
            except ValueError:
                # Movement/projection changed before input: keep care active and
                # obtain a fresh scene. The outer deadline bounds these retries.
                pass
        previous=(key,life['current_hp'])
        time.sleep(.2)
    raise ValueError('No quiet nearby spot verified; reload deferred')


def prepare(info,route_id,cancelled,notify):
    from conquest.merchants.delivery_operation import guard_reload
    guard_reload()
    from conquest.worker import request
    from conquest.overnight import OvernightLoop
    from conquest.route_controller import controller_guard
    stop=Path(state_path('.runtime/overnight.stop'))
    stop.write_text('Safe reload handoff',encoding='utf-8')
    request(info,'controls',{'enabled':False})
    deadline=time.monotonic()+20
    while time.monotonic()<deadline:
        if cancelled.is_set():raise ValueError('Reload canceled by user')
        status=read_json(state_path('reports/overnight/status.json'))
        health=request(info,'health')
        # A stopped controller may leave a PID that Windows cannot query.
        # Its terminal record permits trying the exclusive lock below; only
        # acquiring that lock authorizes the replacement movement controller.
        released=(process_alive(status.get('pid')) is False or not status.get('pid')
                  or status.get('phase')=='stopped')
        if not health['embedded_controls'].get('external_execution') and released:break
        time.sleep(.1)
    else:raise ValueError('Route controller did not release input; reload deferred')
    stop.unlink(missing_ok=True)
    with controller_guard() as acquired:
        if not acquired:raise ValueError('Another route owns input; reload deferred')
        loop=OvernightLoop(route_id);loop.refresh();loop.phase='reloading'
        loop.record('reload_started',activity='Moving to a safe spot for app reload')
        original=loop.check_stop
        def check_stop():
            if cancelled.is_set():raise ValueError('Reload canceled by user')
            original()
        loop.check_stop=check_stop
        try:return park(loop,cancelled,notify)
        finally:
            loop.phase='stopped'
            loop.record('reload_preparation_finished')


def validate_handoff(info,proof):
    from conquest.worker import request
    health=request(info,'health');data=health['embedded_controls'];life=data.get('life')
    if (not clear_observation(health) or data.get('external_execution') or data['control']['enabled']
            or health['target']!=proof['target'] or life['map_id']!=proof['map_id']
            or life['position']!=proof['position'] or life['current_hp']<proof['hp']
            or not 0<=time.time()-proof['verified_at']<=5):
        raise ValueError('Safe spot changed; reload deferred')


def save_resume(proof,resume):
    write_json(RESUME,{'source_pid':os.getpid(),'target':proof['target'],'resume':resume,
                       'expires_at':time.time()+120})


def resume_after_embed(app):
    pending=read_json(RESUME)
    if not pending or pending.get('source_pid')==os.getpid():return
    if pending.get('expires_at',0)<time.time():RESUME.unlink(missing_ok=True);return
    info=getattr(app,'last',{}).get('worker_info_path')
    if not info or not app.runtime:return
    from conquest.worker import request
    health=request(info,'health');data=health['embedded_controls']
    if health['target']!=pending['target']:RESUME.unlink(missing_ok=True);return
    if (not data.get('observations_available') or not data.get('life')
            or not 0<=time.time()-data.get('observed_at',0)<=1):return
    RESUME.unlink(missing_ok=True)
    if pending['resume']:app.update_control({'enabled':True})
