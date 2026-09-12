from types import SimpleNamespace as NS
import json
import pytest
from conquest import session_plan as p
from conquest.overnight import OvernightLoop
from conquest.routes import RouteLibrary


def plan():return {'active':True,'route_id':'bandit','upgrade_maps':[1002,1011],'started_at':123}


def test_hold_survives_restart_until_explicit_resume(tmp_path,monkeypatch):
    monkeypatch.setattr(p,'PLAN',tmp_path/'plan.json')
    p.write_json(p.PLAN,plan())
    assert p.active_plan()==plan()
    # Time of day does not stop farming or silently release the requested hold.
    monkeypatch.setattr(p.time,'time',lambda:9999999999)
    assert p.active_plan()==plan()
    p.resume_leveling()
    assert p.active_plan() is None


def test_level_42_does_not_leave_bandits_while_night_hold_is_active(monkeypatch):
    from conquest import leveling_routes as levels
    loop=OvernightLoop.__new__(OvernightLoop)
    loop.route=RouteLibrary().load('bandit');loop.auto_level=True;loop.next_level_check=0;loop.last_level=36;loop.info=None
    loop.health=lambda:{};events=[];loop.record=lambda event,**kw:events.append(event)
    monkeypatch.setattr(p,'active_plan',plan)
    monkeypatch.setattr(levels,'read_level',lambda *a:42)
    assert not loop.select_level_route()
    assert loop.route.id=='bandit' and loop.last_level==42 and 'route_hold_active' in events


def test_pheasant_hold_prevents_level_progression_and_keeps_local_restock(tmp_path,monkeypatch):
    from conquest import leveling_routes as levels
    monkeypatch.setattr(p,'PLAN',tmp_path/'plan.json')
    data={'active':True,'mode':'hold_route','route_id':'pheasant','upgrade_maps':[1002],'started_at':123}
    p.write_json(p.PLAN,data)
    assert p.active_plan()==data and 'Pheasant route' in p.plan_note()
    loop=OvernightLoop.__new__(OvernightLoop)
    loop.route=RouteLibrary().load('pheasant');loop.auto_level=True;loop.next_level_check=0;loop.last_level=1;loop.info=None
    loop.health=lambda:{};events=[];loop.record=lambda event,**kw:events.append(event)
    monkeypatch.setattr(levels,'read_level',lambda *a:7)
    assert not loop.select_level_route()
    assert loop.route.id=='pheasant' and 'route_hold_active' in events
    assert p.upgrade_circuit(loop) is False
    p.resume_leveling();assert p.active_plan() is None


def test_route_hold_cannot_substitute_an_unrelated_restock_map(tmp_path,monkeypatch):
    monkeypatch.setattr(p,'PLAN',tmp_path/'plan.json')
    p.write_json(p.PLAN,{'active':True,'mode':'hold_route','route_id':'pheasant','upgrade_maps':[1011]})
    with pytest.raises(ValueError,match='own restock town'):p.active_plan()


def test_circuit_only_repeats_at_new_equipment_tier_or_after_incomplete_review():
    previous={'completed':True,'plan_started_at':123,'tier':35}
    assert not p.circuit_due(plan(),36,previous)
    assert p.circuit_due(plan(),37,previous)
    assert p.circuit_due(plan(),36,{**previous,'completed':False})
    assert p.circuit_due({**plan(),'started_at':124},36,previous)


@pytest.mark.parametrize('failed_vendor',[None,1])
def test_circuit_checks_all_shops_in_both_cities_then_returns_to_bandits(tmp_path,monkeypatch,failed_vendor):
    from conquest import world_travel,city_travel,equipment
    monkeypatch.setattr(p,'CIRCUIT',tmp_path/'circuit.json');monkeypatch.setattr(p,'active_plan',plan)
    calls=[];current=[1011]
    services={1002:{'blacksmith':[452,335],'equipment':[(1,[415,358]),(4,[416,369])]},
              1011:{'blacksmith':[199,229],'equipment':[(4,[204,245])]}}
    monkeypatch.setattr(world_travel,'connection_path',lambda *a:[])
    monkeypatch.setattr(city_travel,'city_for',lambda m:{'name':str(m),'services':services[m]})
    def travel(loop,m):current[0]=m;calls.append(('city',m))
    monkeypatch.setattr(world_travel,'travel_to_map',travel)
    def review(self,v):calls.append(('shop',current[0],v));return v!=failed_vendor
    monkeypatch.setattr(equipment.EquipmentReview,'visit',review)
    loop=NS(route=RouteLibrary().load('bandit'),phase='restocking',
        town=lambda action,**kw:{'level':36} if action=='gear' else {},travel=lambda point:None,record=lambda *a,**k:None)
    assert p.upgrade_circuit(loop)
    assert [c for c in calls if c[0]=='shop']==[('shop',1002,5),('shop',1002,1),('shop',1002,4),('shop',1011,5),('shop',1011,4)]
    assert calls[-1]==('city',1011) and loop.route.id=='bandit'
    saved=p.read_json(p.CIRCUIT)
    assert saved['completed']==(failed_vendor is None)


def test_circuit_attempts_return_home_after_shop_failure(tmp_path,monkeypatch):
    from conquest import world_travel,city_travel
    monkeypatch.setattr(p,'CIRCUIT',tmp_path/'circuit.json');monkeypatch.setattr(p,'active_plan',plan)
    monkeypatch.setattr(world_travel,'connection_path',lambda *a:[])
    monkeypatch.setattr(city_travel,'city_for',lambda m:{'name':str(m),'services':{'blacksmith':[1,1],'equipment':[]}})
    cities=[]
    monkeypatch.setattr(world_travel,'travel_to_map',lambda loop,m:cities.append(m))
    def town(action,**kw):
        if action=='gear':return {'level':36}
        if cities:raise RuntimeError('Interrupted shop connection')
        return {}
    loop=NS(route=RouteLibrary().load('bandit'),phase='restocking',town=town,travel=lambda point:None,record=lambda *a,**kw:None)
    with pytest.raises(RuntimeError):p.upgrade_circuit(loop)
    assert cities[-1]==1011
    assert not p.read_json(p.CIRCUIT)['completed']


@pytest.mark.parametrize('finished',[False,True])
def test_restart_resumes_interrupted_upgrade_circuit_without_repeating_supply_trip(monkeypatch,finished):
    monkeypatch.setattr(p,'active_plan',plan)
    p.write_json(p.CIRCUIT,{'plan_started_at':123,'completed':False,**({'finished_at':124} if finished else {})})
    calls=[]
    monkeypatch.setattr(p,'upgrade_circuit',lambda loop:calls.append('circuit'))
    loop=OvernightLoop.__new__(OvernightLoop);loop.route=RouteLibrary().load('turtledove')
    loop.living=lambda:None;loop.stop_farm=lambda:None;loop.adopt_ammunition=lambda:None
    loop.town=lambda action,**kw:{'items':[{'type_id':1050000,'amount':1000},{'type_id':1000020,'amount':10}], 'capacity':40,'silver':1000}
    loop.record=lambda *a,**k:None
    loop.restock=lambda:pytest.fail('Already supplied; resume the interrupted crossing')
    loop.prepare_supplies()
    assert calls==([] if finished else ['circuit'])



def test_interrupted_circuit_continues_at_next_city(monkeypatch):
    from conquest import world_travel,city_travel,equipment
    monkeypatch.setattr(p,'active_plan',plan)
    p.write_json(p.CIRCUIT,{'plan_started_at':123,'tier':35,'completed':False,
        'maps':[{'map_id':1002,'reviewed':False}],'started_at':124})
    monkeypatch.setattr(world_travel,'connection_path',lambda *a:[])
    monkeypatch.setattr(city_travel,'city_for',lambda m:{'name':str(m),'services':{'blacksmith':[1,1],'equipment':[]}})
    cities=[];shops=[]
    monkeypatch.setattr(world_travel,'travel_to_map',lambda loop,m:cities.append(m))
    monkeypatch.setattr(equipment.EquipmentReview,'visit',lambda self,v:shops.append(v) or True)
    loop=NS(route=RouteLibrary().load('bandit'),phase='starting',town=lambda *a,**k:{'level':36},
        travel=lambda p:None,record=lambda *a,**k:None)
    assert p.upgrade_circuit(loop)
    assert cities==[1011,1011] and shops==[5]
    assert not p.read_json(p.CIRCUIT)['completed']
    assert p.read_json(p.CIRCUIT)['finished_at']>124



def test_phoenix_only_night_has_no_cross_city_circuit(monkeypatch):
    data={**plan(),'upgrade_maps':[1011]}
    p.write_json(p.PLAN,data)
    assert p.active_plan()==data
    assert p.plan_note()=='Tonight: Bandits; Phoenix shops only'
    loop=NS(route=RouteLibrary().load('bandit'),town=lambda *a,**k:{'level':37},
            travel=lambda *a:pytest.fail('Phoenix vendors are covered by local restock'))
    assert p.upgrade_circuit(loop) is False
