"""Mandatory destination-town arrival, verified with live map and position."""
import json
import time
from pathlib import Path
from conquest.navigation import read_terrain
from conquest.discord_notify import write_json

CITIES=Path('profiles/cities.json')
VISIT=Path('.runtime/city-visit.json')
CLIENT_ROOT=r'C:\Program Files\Classic Conquer 2.0'


def city_for(map_id):
    matches=[c for c in json.loads(CITIES.read_text(encoding='utf-8'))['cities'] if c['map_id']==map_id]
    if len(matches)!=1:raise ValueError(f'No saved destination town for map {map_id}')
    city=matches[0];terrain=read_terrain(CLIENT_ROOT,map_id)
    x,y=city['town_anchor'];left,top,right,bottom=city['town_boundary']
    if (terrain.source_sha256!=city['terrain_sha256'] or not terrain.walkable((x,y))
            or not(left<=x<=right and top<=y<=bottom)):
        raise ValueError('Destination town differs from installed terrain')
    return city


def ensure_city_visit(loop,*,new_arrival=False):
    health=loop.living();life=health['embedded_controls']['life'];city=city_for(life['map_id'])
    identity=health['target'];actor=life['object_address']
    previous=json.loads(VISIT.read_text(encoding='utf-8')) if VISIT.exists() else {}
    key=dict(identity=identity,map_id=life['map_id'],terrain_sha256=city['terrain_sha256'])
    # An explicit start at the user's prepared hunting spot takes precedence
    # over an initial town visit. Actual future city arrivals still visit town.
    from conquest.session_plan import active_plan
    plan=active_plan() or {}
    start=plan.get('start_at_hunt',{})
    if (not new_arrival and plan.get('route_id')==loop.route.id
            and start.get('identity')==identity and start.get('map_id')==life['map_id']):
        return False
    if not new_arrival and previous.get('completed') and all(previous.get(k)==v for k,v in key.items()):
        return False
    write_json(VISIT,{**key,'completed':False,'started_at':time.time()})
    loop.stop_farm()
    prior=loop.phase;loop.phase='visiting_town'
    activity=f'Heading to {city["name"]} town before hunting'
    loop.record('city_town_departing',city=city['name'],activity=activity)
    loop.terrain=read_terrain(CLIENT_ROOT,life['map_id'])
    left,top,right,bottom=city['town_boundary'];x,y=life['position']
    if not(left<=x<=right and top<=y<=bottom):
        loop.travel(tuple(city['town_anchor']),activity=activity)
    fresh=loop.living();data=fresh['embedded_controls'];after=data['life']
    left,top,right,bottom=city['town_boundary'];x,y=after['position']
    if (fresh['target']!=identity or after['object_address']!=actor or after['map_id']!=city['map_id']
            or after['dead_candidate'] or not 0<=time.time()-data.get('observed_at',0)<=1
            or not(left<=x<=right and top<=y<=bottom)):
        raise ValueError('Destination town arrival was not confirmed by memory')
    write_json(VISIT,{**key,'completed':True,'position':after['position'],'observed_at':time.time()})
    loop.record('city_town_arrived',city=city['name'],position=after['position'],
                activity=f'Arrived in {city["name"]} town; preparing to hunt')
    loop.phase=prior
    return True


def service_role(map_id,point):
    matches=[c for c in json.loads(CITIES.read_text(encoding='utf-8'))['cities'] if c['map_id']==map_id]
    if len(matches)!=1:return None
    services=matches[0].get('services',{})
    stops=[(3,services.get('pharmacist')),(5,services.get('blacksmith')),*services.get('equipment',[])]
    return next((role for role,anchor in stops if anchor and tuple(point)==tuple(anchor)),None)
