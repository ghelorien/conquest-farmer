"""Level brackets and verified-memory level selection for reusable routes."""
import json
from pathlib import Path
import time
from conquest.worker import request
from conquest.routes import RouteLibrary,monster_family


def presets(path='profiles/leveling-presets.json'):
    rows=json.loads(Path(path).read_text(encoding='utf-8'))
    expected=1
    for row in rows:
        low,high=row['levels']
        if low!=expected or not low<=high<=140:raise ValueError('Level brackets have a gap or overlap')
        expected=high+1
    if expected!=141:raise ValueError('Level brackets must cover levels 1 through 140')
    return rows


def bracket(level,rows=None):
    if type(level) is not int or not 1<=level<=140:raise ValueError('Invalid observed character level')
    return next(r for r in (presets() if rows is None else rows) if r['levels'][0]<=level<=r['levels'][1])


def read_level(info,health):
    data=health['embedded_controls'];life=data.get('life')
    if not life or life['dead_candidate'] or not 0<=time.time()-data.get('observed_at',0)<=1:
        raise ValueError('Current living memory state required for route selection')
    result=request(info,'sample',{'fields':[{'name':'level','address':hex(life['object_address']+0x6e8),'kind':'u32'}]})
    level=result['fields'][0]['value'][0]
    fresh=request(info,'health')['embedded_controls']
    latest=fresh.get('life')
    if (not latest or latest['object_address']!=life['object_address'] or latest['dead_candidate']
            or not 0<=time.time()-fresh.get('observed_at',0)<=1):
        raise ValueError('Character changed during route level observation')
    bracket(level)
    return level


def desired_route(level,library=None):
    entry=bracket(level)
    if not entry.get('saved_route'):return None,entry
    route=(library or RouteLibrary()).load(entry['saved_route'])
    if tuple(route.recommended_levels)!=tuple(entry['levels']) or tuple(route.monster_type_ids)!=tuple(m['type_id'] for m in monster_family(entry['monster_type_id'])):
        raise ValueError('Saved route disagrees with its level bracket or monster group')
    return route,entry
