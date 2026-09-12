"""Reusable map connections learned from ordinary portal entry and memory arrival."""
from collections import deque
import json
from pathlib import Path
import time
from conquest.navigation import read_terrain
from conquest.worker import request

CLIENT_ROOT=r'C:\Program Files\Classic Conquer 2.0'
CONNECTIONS=Path('profiles/map-connections.json')


def connections(path=CONNECTIONS):
    if not Path(path).exists():return []
    value=json.loads(Path(path).read_text(encoding='utf-8'))
    return value['connections']


def connection_path(source,destination,edges=None):
    queue=deque([(source,[])])
    seen={source}
    for_map=connections() if edges is None else edges
    while queue:
        here,path=queue.popleft()
        if here==destination:return path
        for edge in for_map:
            if edge.get('verified') is not True or edge['source_map']!=here or edge['destination_map'] in seen:continue
            seen.add(edge['destination_map'])
            queue.append((edge['destination_map'],path+[edge]))
    raise ValueError(f'No memory-verified map connection from {source} to {destination}')


def approach_portal(terrain,source,portal_id):
    matches=[p for p in terrain.portals if p[2]==portal_id]
    if len(matches)!=1:raise ValueError('Portal ID is absent or ambiguous')
    x,y,_=matches[0]
    # Try nearest approach first; all paths remain outside portal guard tiles.
    candidates=sorted(((x+dx,y+dy) for dx,dy in ((2,0),(-2,0),(0,2),(0,-2),
        (3,0),(-3,0),(0,3),(0,-3))),key=lambda p:abs(p[0]-source[0])+abs(p[1]-source[1]))
    for point in candidates:
        try:terrain.path(tuple(source),point)
        except ValueError:continue
        return point,matches[0]
    raise ValueError('No walkable approach to this portal')


def cross_portal(loop,portal_id,expected_map=None):
    before=loop.living()['embedded_controls']['life']
    source_map=before['map_id'];actor=before['object_address']
    terrain=read_terrain(CLIENT_ROOT,source_map)
    loop.terrain=terrain
    approach,portal=approach_portal(terrain,before['position'],portal_id)
    loop.travel(approach)
    before=loop.living()['embedded_controls']['life']
    if before['map_id']!=source_map or before['object_address']!=actor:
        raise ValueError('Character or map changed before portal entry')
    loop.record('entering_portal',source_map=source_map,portal_id=portal_id,
                activity=f'Entering portal to map {expected_map}' if expected_map else 'Checking map portal destination')
    request(loop.info,'route-jump',{'source':before['position'],'destination':list(portal[:2]),
            'map_id':source_map,'portal_id':portal_id,'expires_at':time.time()+4})
    deadline=time.monotonic()+12
    stable=0;last_stamp=None;arrival=None
    while time.monotonic()<deadline:
        data=loop.health()['embedded_controls'];life=data.get('life')
        if (life and life['object_address']==actor and not life['dead_candidate']
                and life['map_id']!=source_map and 0<=time.time()-data.get('observed_at',0)<=1):
            if life['timestamp']!=last_stamp:
                stable=stable+1 if arrival and arrival['map_id']==life['map_id'] else 1
                arrival=life;last_stamp=life['timestamp']
            if stable>=3:break
        time.sleep(.1)
    else:raise ValueError('Portal arrival was not confirmed by memory')
    if expected_map is not None and arrival['map_id']!=expected_map:
        raise ValueError(f'Portal destination changed: expected {expected_map}, observed {arrival["map_id"]}')
    destination=read_terrain(CLIENT_ROOT,arrival['map_id'])
    edge={'source_map':source_map,'portal_id':portal_id,'portal_position':list(portal[:2]),
          'destination_map':arrival['map_id'],'arrival_position':arrival['position'],
          'source_terrain_sha256':terrain.source_sha256,'destination_terrain_sha256':destination.source_sha256,
          'verified':True,'observed_at':time.time()}
    loop.terrain=destination
    loop.record('map_arrived',connection=edge,activity=f'Arrived on map {arrival["map_id"]}')
    return edge


def save_connection(edge,path=CONNECTIONS):
    if edge.get('verified') is not True:raise ValueError('Only observed map arrivals may be saved')
    rows=[r for r in connections(path) if (r['source_map'],r['portal_id'])!=(edge['source_map'],edge['portal_id'])]
    from conquest.discord_notify import write_json
    write_json(Path(path),{'connections':rows+[edge]})


def travel_to_map(loop,destination):
    for _ in range(8):
        life=loop.living()['embedded_controls']['life']
        if life['map_id']==destination:
            loop.terrain=read_terrain(CLIENT_ROOT,destination)
            return
        if life['map_id']==1036:
            return_from_market(loop,destination)
            continue
        edge=connection_path(life['map_id'],destination)[0]
        terrain=read_terrain(CLIENT_ROOT,life['map_id'])
        target=read_terrain(CLIENT_ROOT,edge['destination_map'])
        if (terrain.source_sha256!=edge['source_terrain_sha256']
                or target.source_sha256!=edge['destination_terrain_sha256']
                or tuple(edge['portal_position'])+(edge['portal_id'],) not in terrain.portals):
            raise ValueError('Saved map connection differs from installed terrain')
        from conquest.city_travel import city_for,ensure_city_visit
        city_for(edge['destination_map'])  # Check before any teleport payment.
        if life['map_id']==1002 or edge['destination_map']==1002:
            from conquest.conductress import take_saved_trip
            if not take_saved_trip(loop,edge['destination_map']):
                raise ValueError('A memory-verified Conductress trip is required for Twin City travel; walking fallback is disabled')
            arrival=loop.living()['embedded_controls']['life']
            if arrival['map_id']==edge['destination_map']:
                ensure_city_visit(loop,new_arrival=True)
                continue
        cross_portal(loop,edge['portal_id'],edge['destination_map'])
        ensure_city_visit(loop,new_arrival=True)
    raise ValueError('Map travel exceeded the connection limit')


def return_from_market(loop,destination):
    """Resume hunting from Market using a qualified service, never a portal guess."""
    from conquest.discord_notify import read_json,write_json
    from conquest.meteor_banking import POLICY,trip
    from conquest.town_trade import stash_candidate
    from conquest.city_travel import ensure_city_visit
    policy=read_json(POLICY)
    origin=getattr(loop.route,'restock_map_id',destination)
    plan=policy.get('origins',{}).get(str(origin),{}).get('return')
    if not plan or not plan.get('verified') or plan.get('source_map')!=1036 or plan.get('destination_map')!=origin:
        raise ValueError('Market departure needs a verified return itinerary')
    if any(stash_candidate(item) for item in loop.town('supplies')['items']):
        raise ValueError('Stay in Market: store protected valuables before returning to the route')
    journal=Path('.runtime/market-route-departure.json')
    old=read_json(journal)
    if old.get('phase')=='submitted':
        raise ValueError('Market departure is uncertain; reconcile arrival before retrying')
    before=loop.living()
    state={'phase':'prepared','identity':before['target'],'origin':1036,
           'destination':origin,'started_at':time.time()}
    write_json(journal,state)
    trip(loop,plan,before_submit=lambda:write_json(journal,{**state,'phase':'submitted'}))
    write_json(journal,{**state,'phase':'complete','completed_at':time.time()})
    loop.record('market_route_returned',activity='Returned from Market; resuming the saved farming route')
    ensure_city_visit(loop,new_arrival=True)
