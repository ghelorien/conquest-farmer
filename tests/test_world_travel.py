from conquest import world_travel as w
import json
from pathlib import Path
from dataclasses import dataclass
from types import SimpleNamespace
import numpy as np
import pytest
from conquest.navigation import TerrainMap
from conquest.world_travel import connection_path,approach_portal


def test_only_verified_connections_form_a_returnable_path():
    edges=[{'source_map':1002,'destination_map':1011,'verified':True},
           {'source_map':1011,'destination_map':1002,'verified':True},
           {'source_map':1002,'destination_map':1020,'verified':False}]
    assert connection_path(1011,1002,edges)==[edges[1]]
    assert connection_path(1011,1011,edges)==[]
    with pytest.raises(ValueError):connection_path(1011,1020,edges)


def test_portal_guard_is_only_entered_by_an_explicit_known_portal_action(monkeypatch):
    from conquest import route_input
    @dataclass
    class Life:
        position:tuple=(7,10)
        map_id:int=1002
        dead_candidate:bool=False
    monkeypatch.setattr(route_input,'read_life',lambda *args:Life())
    calls=[]
    monkeypatch.setattr(route_input.EmbeddedRecoveryInput,'send',lambda self,*args:calls.append(args))
    terrain=TerrainMap(1002,20,20,np.zeros((20,20),dtype=bool),'',((10,10,1),),())
    terrain.blocked[9:12,9:12]=True
    send=route_input.RouteJumpInput(SimpleNamespace(adapter=None,health_layout=None,character='Parasite'),terrain)
    body={'source':[7,10],'destination':[10,10],'map_id':1002,'expires_at':999}
    with pytest.raises(ValueError):send(body)
    assert send({**body,'portal_id':1})['movement']=='run'
    with pytest.raises(ValueError):send({**body,'portal_id':2})
    assert len(calls)==1
    point,portal=approach_portal(terrain,(7,10),1)
    assert terrain.walkable(point) and portal==(10,10,1)


def test_level_transition_requires_return_path_then_selects_new_route(monkeypatch):
    from conquest import overnight,world_travel,leveling_routes
    from conquest.routes import RouteLibrary
    old=RouteLibrary().load('poltergeist')
    new=RouteLibrary().load('wingedsnake').model_copy(update={'restock_map_id':1002})
    loop=overnight.OvernightLoop.__new__(overnight.OvernightLoop)
    loop.route=old;loop.auto_level=True;loop.next_level_check=0;loop.last_level=0;loop.info='worker'
    state={'embedded_controls':{'life':{'map_id':1002}}};loop.health=lambda:state
    events=[];calls=[]
    loop.record=lambda event,**fields:events.append(event)
    loop.stop_farm=lambda:calls.append('stop')
    monkeypatch.setattr(leveling_routes,'read_level',lambda *args:29)
    monkeypatch.setattr(leveling_routes,'desired_route',lambda level:(new,{'id':'wingedsnake','levels':[27,31],'name':'WingedSnake route'}))
    def missing(source,destination):
        if source==1011:raise ValueError('Missing return')
        return []
    monkeypatch.setattr(world_travel,'connection_path',missing)
    assert loop.select_level_route(state) is False and not calls
    assert events[-1]=='level_route_pending'
    loop.next_level_check=0
    monkeypatch.setattr(world_travel,'connection_path',lambda *args:[])
    monkeypatch.setattr(world_travel,'travel_to_map',lambda loop,destination:calls.append(destination))
    monkeypatch.setattr(overnight,'request',lambda *args:calls.append(args[-1]))
    monkeypatch.setattr(overnight,'read_status',lambda path:{'selected_route':'wingedsnake'})
    monkeypatch.setattr(overnight,'read_terrain',lambda *args:None)
    assert loop.select_level_route(state) is True
    assert calls==['stop',1011,{'route_id':'wingedsnake'}]
    assert loop.route==new and events[-1]=='level_route_changed'


def test_identical_pending_route_events_are_limited_without_staling_status(tmp_path,monkeypatch):
    from conquest import overnight
    loop=overnight.OvernightLoop.__new__(overnight.OvernightLoop)
    loop.phase='hunting';loop.cycles=0;loop.state={};loop.output=tmp_path
    loop.route=SimpleNamespace(id='bandit')
    now=[100.0]
    monkeypatch.setattr(overnight.time,'time',lambda:now[0])
    monkeypatch.setattr(overnight.time,'monotonic',lambda:now[0])
    fields={'level':73,'next_route':'Next zone','activity':'Waiting for a verified connection'}
    loop.record('level_route_pending',**fields)
    now[0]=105;loop.record('level_route_pending',**fields)
    assert len((tmp_path/'events.jsonl').read_text().splitlines())==1
    assert json.loads((tmp_path/'status.json').read_text())['updated_at']==105
    now[0]=160;loop.record('level_route_pending',**fields)
    assert len((tmp_path/'events.jsonl').read_text().splitlines())==2
    now[0]=161;loop.record('level_route_pending',**{**fields,'next_route':'Changed zone'})
    assert len((tmp_path/'events.jsonl').read_text().splitlines())==3


def test_teleport_checks_town_before_payment_and_visits_it_after_arrival(monkeypatch):
    from conquest import world_travel as w,city_travel,conductress
    calls=[];life={'map_id':1002}
    loop=SimpleNamespace(living=lambda:{'embedded_controls':{'life':life}})
    terrain=SimpleNamespace(source_sha256='same',portals=((963,557,7),))
    edge=dict(source_map=1002,destination_map=1011,portal_id=7,portal_position=[963,557],
              source_terrain_sha256='same',destination_terrain_sha256='same')
    monkeypatch.setattr(w,'connection_path',lambda *args:[edge])
    monkeypatch.setattr(w,'read_terrain',lambda *args:terrain)
    monkeypatch.setattr(city_travel,'city_for',lambda map_id:calls.append('town_checked'))
    monkeypatch.setattr(conductress,'take_saved_trip',lambda *args:calls.append('teleport') or True)
    def cross(*args):calls.append('portal');life['map_id']=1011
    monkeypatch.setattr(w,'cross_portal',cross)
    monkeypatch.setattr(city_travel,'ensure_city_visit',lambda *args,**kw:calls.append(('town',kw['new_arrival'])))
    w.travel_to_map(loop,1011)
    assert calls==['town_checked','teleport','portal',('town',True)]
    assert loop.terrain is terrain



def test_twin_city_return_never_falls_back_to_walking_without_verified_conductress(monkeypatch):
    from conquest import world_travel as w,city_travel,conductress
    life={'map_id':1011};calls=[]
    loop=SimpleNamespace(living=lambda:{'embedded_controls':{'life':life}})
    terrain=SimpleNamespace(source_sha256='same',portals=((5,376,0),))
    edge=dict(source_map=1011,destination_map=1002,portal_id=0,portal_position=[5,376],
              source_terrain_sha256='same',destination_terrain_sha256='same')
    monkeypatch.setattr(w,'connection_path',lambda *args:[edge])
    monkeypatch.setattr(w,'read_terrain',lambda *args:terrain)
    monkeypatch.setattr(city_travel,'city_for',lambda *args:None)
    monkeypatch.setattr(conductress,'take_saved_trip',lambda loop,d:calls.append(d) or False)
    monkeypatch.setattr(w,'cross_portal',lambda *args:pytest.fail('No walking substitute'))
    with pytest.raises(ValueError,match='Conductress'):w.travel_to_map(loop,1002)
    assert calls==[1002]


def test_market_start_uses_saved_return_before_city_visit(tmp_path,monkeypatch):
    from types import SimpleNamespace as NS
    from conquest import meteor_banking,city_travel
    from conquest.discord_notify import read_json
    monkeypatch.chdir(tmp_path)
    plan={'verified':True,'source_map':1036,'destination_map':1011}
    policy=tmp_path/'policy.json';policy.write_text(json.dumps({'origins':{'1011':{'return':plan}}}))
    monkeypatch.setattr(meteor_banking,'POLICY',policy)
    calls=[]
    loop=NS(route=NS(restock_map_id=1011),town=lambda *a:{'items':[]},
        living=lambda:{'target':{'pid':1}},record=lambda *a,**k:None)
    monkeypatch.setattr(meteor_banking,'trip',lambda l,p,**kw:(kw['before_submit'](),calls.append(('trip',p))))
    monkeypatch.setattr(city_travel,'ensure_city_visit',lambda l,**k:calls.append(('city',k)))
    w.return_from_market(loop,1011)
    assert calls==[('trip',plan),('city',{'new_arrival':True})]
    assert read_json('.runtime/market-route-departure.json')['phase']=='complete'


def test_market_departure_keeps_valuables_and_uncertain_transfer_safe(tmp_path,monkeypatch):
    from types import SimpleNamespace as NS
    from conquest import meteor_banking
    monkeypatch.chdir(tmp_path)
    policy=tmp_path/'policy.json';policy.write_text(json.dumps({'origins':{'1011':{'return':{
        'verified':True,'source_map':1036,'destination_map':1011}}}}))
    monkeypatch.setattr(meteor_banking,'POLICY',policy)
    monkeypatch.setattr(meteor_banking,'trip',lambda *a:pytest.fail('Unsafe departure'))
    bag=[{'uid':1,'type_id':1088000,'slot':0,'plus':0}]
    loop=NS(route=NS(restock_map_id=1011),town=lambda *a:{'items':bag})
    with pytest.raises(ValueError,match='store protected'):w.return_from_market(loop,1011)
    bag.clear();Path('.runtime').mkdir();Path('.runtime/market-route-departure.json').write_text('{"phase":"submitted"}')
    with pytest.raises(ValueError,match='uncertain'):w.return_from_market(loop,1011)
