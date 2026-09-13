"""One memory-qualified walking edge missing from the Phoenix terrain graph."""
import struct
import time
from pathlib import Path
import yaml
from conquest.addressing import PlayerLayout,resolve_player
from conquest.memory_health import HealthWorkerSession
from conquest.memory_life import CLIENT_SHA256
from conquest.viewport import clear_scene
from conquest.worker import request

TERRAIN_SHA256='433b3163a38d068e978fa6609c2d8e32f48bd27b188e15cddc3b3583ca9e9921'
SOURCE=(195,227)
DESTINATION=(196,228)


def recover_corner(loop,goal):
    terrain=loop.terrain
    if getattr(terrain,'map_id',None)!=1011 or getattr(terrain,'source_sha256',None)!=TERRAIN_SHA256:return False
    health=loop.living();life=health['embedded_controls']['life']
    if (life.get('character')!='Parasite' or life['map_id']!=1011 or tuple(life['position'])!=SOURCE
            or terrain.map_id!=1011 or terrain.source_sha256!=TERRAIN_SHA256
            or life['dead_candidate'] or life.get('ghost_candidate')
            or not terrain.walkable(SOURCE) or not terrain.walkable(DESTINATION)):
        return False
    # The edge was observed twice through memory; do not generalize it into
    # permission to cross other blocked corners or change collision tiles.
    terrain.travel_path(DESTINATION,tuple(goal))
    for _ in range(3):
        if not loop.town('clear-travel-panels').get('closed_panel'):break
    else:raise ValueError('Town panels still block the qualified corner exit')
    health=loop.living();life=health['embedded_controls']['life']
    if (tuple(life['position'])!=SOURCE or life['map_id']!=1011 or life['character']!='Parasite'
            or life['dead_candidate'] or life.get('ghost_candidate')
            or health['embedded_controls'].get('manual_mouse')
            or health['embedded_controls']['control']['enabled']):
        raise ValueError('Qualified corner departure state changed')
    session=HealthWorkerSession(loop.info,CLIENT_SHA256)
    layout=PlayerLayout.model_validate(yaml.safe_load(Path('profiles/classic-1074-player-candidate.yaml').read_text(encoding='utf-8')))
    addresses=resolve_player(session,layout)
    address=life['object_address']+0xd8
    raw=session.read_block(address,24)
    position=struct.unpack_from('<II',raw);anchor=struct.unpack_from('<ii',raw,16)
    if position!=SOURCE or session.read_block(address,24)!=raw:
        raise ValueError('Qualified corner projection changed')
    point=(anchor[0],anchor[1]+32)
    if not clear_scene(point,tuple(health['window']['client_size'])):
        raise ValueError('Qualified corner exit is outside the clear input area')
    loop.check_stop()
    loop.record('town_corner_recovery',source=SOURCE,destination=DESTINATION,
        activity='Leaving the verified Blacksmith corner before continuing town travel')
    request(loop.info,'foreground-click',{'guard':{'name_address':hex(addresses['name']),
        'name':'Parasite','hp_address':hex(addresses['max_hp']),'max_hp':life['max_hp']},
        'point':list(point),'button':'left','control':False,'require_foreground':True,
        'expected_size':health['window']['client_size'],'expires_at':time.time()+4})
    deadline=time.monotonic()+3;consecutive=0;previous=None
    while time.monotonic()<deadline:
        fresh=loop.living()['embedded_controls']['life']
        if fresh['character']!='Parasite' or fresh['map_id']!=1011 or fresh['dead_candidate']:
            raise ValueError('Life or map changed during corner recovery')
        if fresh['timestamp']!=previous:
            previous=fresh['timestamp']
            consecutive=consecutive+1 if tuple(fresh['position'])==DESTINATION else 0
            if consecutive>=2:
                loop.record('town_corner_recovered',source=SOURCE,destination=DESTINATION,
                    activity='Blacksmith corner cleared; resuming town travel')
                return True
        time.sleep(.05)
    raise ValueError('Qualified Blacksmith corner exit was not verified')
