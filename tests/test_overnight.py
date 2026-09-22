from types import SimpleNamespace
import pytest

from conquest.overnight import supply_counts, needs_town, OvernightLoop, OvernightStopped
from conquest.routes import RouteLibrary
from conquest.town_trade import junk_type, TownTrade


def test_intermediate_arrival_tolerance_avoids_clicking_player_sprite(monkeypatch):
    from conquest import city_travel
    monkeypatch.setattr(city_travel,'service_role',lambda *a:None)
    loop=SimpleNamespace(terrain=SimpleNamespace(map_id=1036),
        route=RouteLibrary().load('bandit'),record=lambda *a,**kw:None,
        living=lambda:{'embedded_controls':{'life':{'position':[195,188]}}})
    # No stepper: arrival one tile from this intermediate waypoint must send
    # no input, while transport/NPC identity still has its separate guards.
    OvernightLoop._travel(loop,(194,188),arrival_radius=2)


def test_intermediate_tolerance_cannot_silently_accept_distant_arrival():
    with pytest.raises(ValueError,match='zero to two'):
        OvernightLoop._travel(None,(194,188),arrival_radius=12)


def test_resupply_counts_include_equipped_and_spare_arrows():
    route=RouteLibrary().load('turtledove')
    snapshot={'items':[{'type_id':1050000,'amount':190},{'type_id':1000020,'amount':3}],
              'equipped_ammo':{'type_id':1050000,'amount':20},'silver':1000,'capacity':40}
    counts=supply_counts(snapshot,route)
    assert counts['arrows']==210 and not needs_town(counts,route)
    snapshot['equipped_ammo']['amount']=0
    assert not needs_town(supply_counts(snapshot,route),route)


@pytest.mark.parametrize('field,value',[('potions',0),('free_slots',0),('arrows',0),('arrows',1),('arrows',2)])
def test_town_return_requires_empty_supplies_or_full_inventory(field,value):
    counts={'arrows':500,'potions':8,'free_slots':10,'silver':1000}
    counts[field]=value
    assert needs_town(counts,RouteLibrary().load('turtledove'))


@pytest.mark.parametrize('type_id',[500005,500015,130005,1050000,1000020,710001,420009,421006])
def test_automatic_sales_keep_bows_armor_supplies_special_and_high_quality_items(type_id):
    assert not junk_type(type_id)


def test_equipment_is_retained_until_instance_enhancements_can_be_verified():
    assert not any(junk_type(i) for i in (420015,421003,421004,1088000,1088001))


def test_only_identified_unwanted_consumables_are_sale_candidates():
    assert all(junk_type(i) for i in (1000000,1000010,1001000,1001010,1001020))


def test_farming_off_cancels_the_overnight_loop():
    loop=OvernightLoop.__new__(OvernightLoop)
    loop.route=RouteLibrary().load('turtledove')
    loop.info='unused'
    loop.living=lambda:None
    loop.record=lambda *args,**kwargs:None
    loop.health=lambda:{'embedded_controls':{'control':{'enabled':False}}}
    from conquest import overnight
    original=overnight.request
    overnight.request=lambda *args,**kwargs:None
    try:
        with pytest.raises(OvernightStopped,match='switched Off'):
            loop.hunt()
    finally:
        overnight.request=original


@pytest.mark.parametrize('protected_type',[500005,420005,121007,1088000,1088001,710001,1000020,1050000])
def test_town_trade_does_not_sell_a_protected_item(protected_type):
    trade=TownTrade.__new__(TownTrade)
    trade.vendor=lambda type_id:SimpleNamespace(entity_id=100123)
    trade.shop=SimpleNamespace(read=lambda uid:None)
    trade.inventory=SimpleNamespace(read=lambda:SimpleNamespace(items=[SimpleNamespace(uid=1,type_id=protected_type)]))
    trade.click=lambda *args:pytest.fail('Protected item input')
    with pytest.raises(ValueError,match='protected'):
        trade({'action':'sell','vendor_type':3,'uid':1})


def test_app_status_replacement_does_not_stop_the_farm(monkeypatch):
    from conquest.overnight import read_status
    from pathlib import Path
    calls=[]
    def read(*args,**kwargs):
        calls.append(1)
        if len(calls)<3:
            raise PermissionError('File replacement in progress')
        return '{"pid": 123}'
    monkeypatch.setattr(Path,'read_text',read)
    assert read_status('ignored') == {'pid':123}
    assert len(calls)==3


@pytest.mark.parametrize('action,fields',[
    ('open',{'vendor_type':5}),
    ('buy',{'vendor_type':5,'type_id':1050000}),
    ('sell',{'vendor_type':5,'uid':123}),
])
@pytest.mark.parametrize('detail',['NPC scene changed during observation',
                                 'Pointer is null or outside supported user address space'])
def test_busy_npc_scene_before_input_is_explicitly_retryable(action,fields,detail):
    from conquest.town_trade import TownObservationUnavailable
    trade=TownTrade.__new__(TownTrade)
    def changed(kind):
        raise ValueError(detail)
    trade.vendor=changed
    with pytest.raises(TownObservationUnavailable):
        trade({'action':action,**fields})
    assert not trade.input_attempted


@pytest.mark.parametrize('detail',['Inventory changed during observation',
    'Warehouse or inventory changed before deposit'])
def test_input_error_is_never_reclassified_as_safe_to_repeat(detail):
    from conquest.town_trade import TownObservationUnavailable
    trade=TownTrade.__new__(TownTrade)
    def execute(body):
        trade.input_attempted=True
        raise ValueError(detail)
    trade.execute=execute
    with pytest.raises(ValueError) as error:
        trade({'action':'buy'})
    assert not isinstance(error.value,TownObservationUnavailable)


def test_changed_deposit_snapshot_before_drag_is_retryable():
    from conquest.town_trade import TownObservationUnavailable
    trade=TownTrade.__new__(TownTrade)
    def execute(body):
        raise ValueError('Warehouse or inventory changed before deposit')
    trade.execute=execute
    with pytest.raises(TownObservationUnavailable):
        trade({'action':'warehouse-deposit','uid':123})
    assert not trade.input_attempted


def test_trade_receipt_read_retries_without_repeating_input(monkeypatch):
    from conquest import town_trade
    monkeypatch.setattr(town_trade.time,'sleep',lambda seconds:None)
    reads=[]
    def read():
        reads.append(1)
        if len(reads)<4:
            raise ValueError('Inventory changed during observation')
        return {'silver':800}
    trade=TownTrade.__new__(TownTrade)
    assert trade.verified_read(read,lambda value:value['silver']==800,'unverified')=={'silver':800}
    assert len(reads)==4


def test_controller_retries_busy_preinput_scene_then_continues(monkeypatch):
    from conquest import overnight
    from conquest.town_trade import TownObservationUnavailable
    loop=OvernightLoop.__new__(OvernightLoop)
    loop.info='unused'
    loop.living=lambda:None
    loop.record=lambda *args,**kwargs:None
    calls=[]
    def request(*args):
        calls.append(args)
        if len(calls)<=6:
            raise TownObservationUnavailable('NPC scene changed during observation')
        return {'opened':True}
    monkeypatch.setattr(overnight,'request',request)
    monkeypatch.setattr(overnight.time,'sleep',lambda seconds:None)
    assert loop.town('open',vendor_type=5)=={'opened':True}
    assert len(calls)==7


def test_controller_never_repeats_an_uncertain_purchase(monkeypatch):
    from conquest import overnight
    loop=OvernightLoop.__new__(OvernightLoop)
    loop.info='unused'
    loop.record=lambda *args,**kwargs:None
    loop.living=lambda:None
    calls=[]
    def request(*args):
        calls.append(args)
        raise ValueError('Purchase was not verified; no repeat purchase issued')
    monkeypatch.setattr(overnight,'request',request)
    with pytest.raises(ValueError,match='not verified'):
        loop.town('buy',vendor_type=5,type_id=1050000)
    assert len(calls)==1


def test_restart_closes_panels_and_keeps_loot_space_when_supplies_are_adequate():
    loop=OvernightLoop.__new__(OvernightLoop)
    loop.route=RouteLibrary().load('turtledove')
    loop.cycles=0
    calls=[]
    snapshot={'items':[{'type_id':1050000,'amount':1525},{'type_id':1000020,'amount':15}],
              'equipped_ammo':None,'silver':9782,'capacity':6}
    def town(action,**fields):
        calls.append((action,fields))
        if action=='supplies':return snapshot
        assert action!='buy','Do not consume reserved loot space for optional top-ups'
        return {'ok':True}
    loop.town=town
    loop.travel=lambda target:calls.append(('travel',target))
    loop.sell_junk=lambda kind:None
    loop.record=lambda *args,**kwargs:None
    loop.restock()
    assert calls[:2]==[('close',{'window':'Shop'}),('close',{'window':'Inventory'})]
    assert ('travel',loop.route.restock_anchor) not in calls
    assert ('open',{'vendor_type':3}) not in calls
    assert loop.cycles==1


def test_restock_still_rejects_insufficient_arrows_when_space_cannot_be_recovered():
    loop=OvernightLoop.__new__(OvernightLoop)
    loop.route=RouteLibrary().load('turtledove')
    loop.cycles=0
    snapshot={'items':[{'type_id':1050000,'amount':0},{'type_id':1000020,'amount':15}],
              'equipped_ammo':None,'silver':9782,'capacity':5}
    calls=[]
    def town(action,**fields):
        if action=='supplies':return snapshot
        if action=='buy':
            calls.append(fields)
            raise ValueError('Insufficient funds or inventory room to restock')
        return {}
    loop.town=town
    loop.travel=lambda target:None
    loop.sell_junk=lambda kind:None
    loop.record=lambda *args,**kwargs:None
    with pytest.raises(ValueError,match='Insufficient'):
        loop.restock()
    assert calls==[{'vendor_type':5,'type_id':1050000}]
    assert loop.cycles==0


@pytest.mark.parametrize('stored',[True,False])
def test_full_protected_bag_reaches_storage_before_final_restock_check(monkeypatch,stored):
    from conquest import banking,equipment
    loop=OvernightLoop.__new__(OvernightLoop)
    loop.route=RouteLibrary().load('turtledove');loop.cycles=0
    bag={'items':[{'type_id':1050000,'amount':1525},{'type_id':1000020,'amount':15},
                  {'uid':12,'type_id':1088001,'amount':1,'plus':0,'slot':2}],
         'equipped_ammo':None,'silver':9782,'capacity':4}
    def review(vendor):
        # An upgrade can leave the displaced protected item in the last slot.
        if len(bag['items'])==3:
            bag['items'].append({'uid':13,'type_id':500009,'amount':1,'plus':2,'slot':3})
    monkeypatch.setattr(equipment,'EquipmentReview',lambda loop:SimpleNamespace(visit=review))
    calls=[]
    def town(action,**fields):
        if action=='supplies':return bag
        assert action!='buy'
        return {'ok':True}
    def bank(current):
        calls.append('storage')
        if stored:bag['items'].pop()
        return True
    monkeypatch.setattr(banking,'after_shopping',bank)
    loop.town=town;loop.travel=lambda target:None;loop.sell_junk=lambda vendor:None
    loop.record=lambda *args,**fields:None
    if stored:
        loop.restock()
        assert loop.cycles==1
    else:
        with pytest.raises(ValueError,match='after restocking and storage'):loop.restock()
        assert loop.cycles==0
    assert calls==['storage']


@pytest.mark.parametrize('space_freed',[True,False])
def test_full_bag_is_stored_before_essential_shop_input(monkeypatch,space_freed):
    from conquest import banking
    calls=[];bag={'items':[{'uid':12,'type_id':1088001}],'capacity':1}
    loop=OvernightLoop.__new__(OvernightLoop)
    loop.town=lambda action,**fields:bag if action=='supplies' else calls.append((action,fields))
    loop.travel=lambda target:calls.append(('travel',target));loop.record=lambda *a,**kw:None
    monkeypatch.setattr(banking,'open_warehouse',lambda current:calls.append('warehouse'))
    monkeypatch.setattr(banking,'close_warehouse',lambda current:calls.append('closed'))
    def store(current):
        calls.append('stored')
        if space_freed:bag['items']=[]
    monkeypatch.setattr(banking,'stash_valuables',store)
    if space_freed:
        assert loop.shopping_space(5,(100,200))
        assert calls[-2:]==[('travel',(100,200)),('open',{'vendor_type':5})]
    else:
        with pytest.raises(ValueError,match='no purchase issued'):loop.shopping_space(5,(100,200))
        assert calls[-1]=='closed'
        assert not any(isinstance(c,tuple) and c[0]=='open' for c in calls)
    assert calls.index('warehouse')<calls.index('stored')<calls.index('closed')


def test_available_bag_space_never_triggers_extra_storage_trip():
    loop=OvernightLoop.__new__(OvernightLoop)
    loop.town=lambda action,**kw:{'items':[],'capacity':40}
    loop.record=lambda *a,**kw:pytest.fail('No extra trip when there is room')
    assert not loop.shopping_space(5,(100,200))


@pytest.mark.parametrize('change', [None,'silver','arrows','shortage'])
def test_unconfirmed_topup_only_defers_when_supplies_and_money_stay_unchanged(monkeypatch,change):
    from conquest import overnight
    loop=OvernightLoop.__new__(OvernightLoop)
    loop.route=RouteLibrary().load('turtledove')
    calls=[]
    reports=[]
    reads=[]
    def town(action,**fields):
        calls.append(action)
        if action=='buy':
            raise ValueError('Purchase was not verified; no repeat purchase issued')
        reads.append(1)
        amount=1204 if change!='shortage' else 0
        silver=14005
        if len(reads)>1:
            if change=='silver':silver-=200
            if change=='arrows':amount+=200
        return {'items':[{'type_id':1050000,'amount':amount},{'type_id':1000020,'amount':15}],
                'capacity':8,'silver':silver}
    loop.town=town
    loop.record=lambda event,**fields:reports.append(event)
    monkeypatch.setattr(overnight.time,'sleep',lambda seconds:None)
    if change is None:
        assert loop.buy_supply(5,1050000) is False
        assert reports==['optional_purchase_deferred']
        assert len(reads)==7
    else:
        with pytest.raises(ValueError,match='not verified'):
            loop.buy_supply(5,1050000)
        assert reports==[]
    assert calls.count('buy')==1


def test_default_route_has_no_deadline_but_manual_stop_still_works(monkeypatch,tmp_path):
    from conquest import overnight
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(overnight,'RouteLibrary',lambda:SimpleNamespace(load=lambda name:SimpleNamespace(map_id=1002)))
    monkeypatch.setattr(overnight,'read_terrain',lambda *args:None)
    monkeypatch.setattr(overnight.ctypes.windll.user32,'GetAsyncKeyState',lambda key:0)
    loop=OvernightLoop()
    assert loop.deadline is None and loop.state['ends_at'] is None
    monkeypatch.setattr(overnight.time,'monotonic',lambda:10**12)
    loop.check_stop()
    loop.stop_path.parent.mkdir()
    loop.stop_path.touch()
    with pytest.raises(OvernightStopped,match='Stopped by user'):loop.check_stop()


def test_time_limit_only_applies_when_explicit(monkeypatch,tmp_path):
    from conquest import overnight
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(overnight,'RouteLibrary',lambda:SimpleNamespace(load=lambda name:SimpleNamespace(map_id=1002)))
    monkeypatch.setattr(overnight,'read_terrain',lambda *args:None)
    monkeypatch.setattr(overnight.ctypes.windll.user32,'GetAsyncKeyState',lambda key:0)
    now=[0.];monkeypatch.setattr(overnight.time,'monotonic',lambda:now[0])
    loop=OvernightLoop(hours=1)
    loop.check_stop()
    now[0]=3600
    with pytest.raises(OvernightStopped,match='duration finished'):loop.check_stop()


@pytest.mark.parametrize('reason',['inventory_full','ammo_unavailable','potions_exhausted'])
def test_supply_runner_stop_hands_off_to_town_even_if_character_died(monkeypatch,reason):
    from conquest import overnight
    loop=OvernightLoop.__new__(OvernightLoop)
    loop.route=RouteLibrary().load('turtledove');loop.info='unused'
    loop.living=lambda:None;events=[];stops=[]
    loop.record=lambda event,**fields:events.append((event,fields))
    loop.stop_farm=lambda:stops.append(True)
    loop.health=lambda:{'embedded_controls':{'control':{'enabled':True,
        'execution_state':'runner_stopped','note':'Farm runner stopped: '+reason},
        'life':{'dead_candidate':True}}}
    monkeypatch.setattr(overnight,'request',lambda *args,**kwargs:None)
    loop.hunt()
    assert loop.phase=='restocking' and stops==[True]
    assert events[-1][0]=='return_required' and events[-1][1]['reason']==reason


def test_unexpected_runner_stop_still_reports_failure(monkeypatch):
    from conquest import overnight
    loop=OvernightLoop.__new__(OvernightLoop)
    loop.route=RouteLibrary().load('turtledove');loop.info='unused'
    loop.living=lambda:None;loop.record=lambda *args,**kwargs:None
    loop.health=lambda:{'embedded_controls':{'control':{'enabled':True,
        'execution_state':'runner_stopped','note':'Farm runner stopped: unknown_failure'}}}
    monkeypatch.setattr(overnight,'request',lambda *args,**kwargs:None)
    with pytest.raises(ValueError,match='unknown_failure'):loop.hunt()


@pytest.mark.parametrize('kind,amount,total,expected',[
    (1050000,25,625,True),(1050000,13,900,True),
    (1050000,25,624,False),(1050000,200,2000,False),
    (1088000,1,2000,False),(500005,1,2000,False)])
def test_partial_arrow_sales_preserve_reserve_full_stacks_and_valuables(kind,amount,total,expected):
    from conquest.town_trade import expendable_arrow
    item=SimpleNamespace(type_id=kind,amount=amount)
    inventory=SimpleNamespace(items=[item],equipped_ammo=None,count=lambda kind:total)
    assert expendable_arrow(item,inventory) is expected


def test_partial_arrow_cleanup_stops_at_four_free_slots():
    loop=OvernightLoop.__new__(OvernightLoop);loop.route=RouteLibrary().load('turtledove')
    rows=[{'type_id':1050000,'amount':25,'uid':i} for i in range(4)]
    rows += [{'type_id':1050000,'amount':200,'uid':i+10} for i in range(4)]
    sales=[]
    def town(action,**fields):
        if action=='supplies':return {'items':list(rows),'capacity':8,'silver':100}
        assert action=='sell_partial_arrow'
        sales.append(fields['uid']);rows[:]=[i for i in rows if i['uid']!=fields['uid']]
        return {'sold':fields['uid']}
    loop.town=town;loop.record=lambda *args,**kwargs:None
    loop.recycle_small_arrows()
    assert sales==[0,1,2,3] and len(rows)==4


@pytest.mark.parametrize('kind,plus,slot,expected',[
    (490003,0,0,True),(500005,0,1,True),(530006,0,4,True),
    (500005,1,0,False),(500005,12,0,False),(500005,None,0,False),
    (500005,False,0,False),(500005,0,None,False),
    (430007,0,0,False),(420009,0,0,False),(1088000,0,0,False),
    (1088001,0,0,False),(700001,0,0,False),(1050000,0,0,False),(1000020,0,0,False)])
def test_equipment_sales_require_known_zero_plus_and_preserve_valuables(kind,plus,slot,expected):
    from conquest.town_trade import sale_candidate
    assert sale_candidate({'type_id':kind,'plus':plus,'slot':slot}) is expected


@pytest.mark.parametrize('enabled',[True,False])
@pytest.mark.parametrize('full,low_arrows,expected',[(False,False,False),(True,False,True),(False,True,True)])
def test_restart_restock_depends_on_supplies_not_farming_switch(enabled,full,low_arrows,expected):
    loop=OvernightLoop.__new__(OvernightLoop);loop.route=RouteLibrary().load('turtledove')
    loop.living=lambda:{'embedded_controls':{'control':{'enabled':enabled}}}
    rows=[{'type_id':1050000,'amount':0 if low_arrows else 600},
          {'type_id':1000020,'amount':10}]
    panels=[]
    def town(action,**fields):
        if action=='gear':return {'level':10,'equipment':{}}
        if action=='close':panels.append(fields['window'])
        return {'items':rows,'capacity':2 if full else 40,'silver':1000}
    loop.town=town
    calls=[];loop.stop_farm=lambda:calls.append('off');loop.restock=lambda:calls.append('town')
    loop.record=lambda *args,**kwargs:None
    loop.prepare_supplies()
    assert calls==(['off','town'] if expected else ['off'])
    assert panels==['Shop','Inventory']


def test_arrow_refill_reopens_shop_after_deferred_equipment_review(monkeypatch):
    from conquest import equipment
    loop=OvernightLoop.__new__(OvernightLoop)
    loop.route=RouteLibrary().load('turtledove');loop.cycles=0
    opened=[False];purchases=[]
    rows=[{'type_id':1050000,'amount':200},{'type_id':1000020,'amount':15}]
    def review(vendor):
        opened[0]=False  # equip succeeds but subsequent Shop reopen was deferred
        return False
    monkeypatch.setattr(equipment,'EquipmentReview',lambda loop:SimpleNamespace(visit=review))
    def town(action,**fields):
        if action=='supplies':return {'items':rows,'capacity':40,'silver':10000}
        if action=='open':opened[0]=True
        if action=='close' and fields['window']=='Shop':opened[0]=False
        if action=='buy':
            assert opened[0], 'Refill attempted through a closed shop'
            purchases.append(fields['type_id']);rows.append({'type_id':1050000,'amount':200})
            return {'verified':True}
        return {}
    loop.town=town;loop.travel=lambda p:None;loop.sell_junk=lambda v:None
    loop.recycle_small_arrows=lambda:None;loop.record=lambda *a,**kw:None
    loop.restock()
    assert purchases==[1050000] and loop.cycles==1


def test_optional_equipment_shop_failure_does_not_block_supplied_route(monkeypatch):
    from conquest import equipment
    monkeypatch.setattr(equipment,'EquipmentReview',lambda loop:SimpleNamespace(visit=lambda vendor:None))
    loop=OvernightLoop.__new__(OvernightLoop);loop.route=RouteLibrary().load('poltergeist');loop.cycles=0
    calls=[];events=[]
    def town(action,**fields):
        calls.append((action,fields))
        if action=='open' and fields['vendor_type']==1:
            raise ValueError('Shop opening was not verified; no repeat input issued')
        if action=='supplies':
            return {'items':[{'type_id':1050000,'amount':1600},{'type_id':1000020,'amount':15}],
                    'capacity':40,'silver':10000}
        return {}
    loop.town=town;loop.travel=lambda p:None;loop.sell_junk=lambda v:None;loop.recycle_small_arrows=lambda:None
    loop.record=lambda event,**fields:events.append(event)
    loop.restock()
    assert loop.cycles==1 and 'equipment_review_deferred' in events
    assert events[-1]=='restock_complete'
    input_calls=[call for call in calls if call[0]!='supplies']
    assert input_calls[-2:]==[('close',{'window':'Shop'}),('close',{'window':'Inventory'})]


def test_temporary_obstruction_of_only_town_corridor_retries_with_running_steps(monkeypatch):
    from conquest import scene_input
    monkeypatch.setattr(scene_input,"memory_player_anchor",lambda *args:(518,396))
    import numpy as np
    from conquest.navigation import TerrainMap
    loop=OvernightLoop.__new__(OvernightLoop);loop.route=RouteLibrary().load('turtledove')
    blocked=np.ones((30,30),dtype=bool);blocked[10,5:19]=False
    loop.terrain=TerrainMap(1002,30,30,blocked,'',(),())
    position=[5,10];steps=[];events=[]
    loop.living=lambda:{'embedded_controls':{'life':{'position':list(position)}}}
    loop.care=SimpleNamespace(check=lambda h:None,session=None)
    loop.record=lambda event,**fields:events.append(event)
    def step(destination,expected_position):
        steps.append((expected_position,destination))
        if len(steps)==1:return {'reached':False}
        position[:]=destination
        return {'reached':True}
    loop.stepper=SimpleNamespace(step_to=step)
    loop.travel((18,10))
    assert position==[18,10] and 'town_path_retry' in events
    assert max(abs(a-b) for a,b in zip(*steps[0]))>=8
    assert max(abs(a-b) for a,b in zip(*steps[1]))<=4
    assert any(max(abs(a-b) for a,b in zip(*pair))>=8 for pair in steps[2:])


def test_town_arrival_uses_nearby_interaction_position_without_repeated_steps():
    loop=OvernightLoop.__new__(OvernightLoop)
    loop.route=RouteLibrary().load('apparition')
    loop.terrain=SimpleNamespace(map_id=1002)
    loop.record=lambda *a,**k:None
    loop.living=lambda:{'embedded_controls':{'life':{'position':[459,334]}}}
    loop.town=lambda *a,**k:{'reachable':True}
    loop.stepper=SimpleNamespace(step_to=lambda *a,**k:pytest.fail('Already in shop interaction range'))
    loop.care=SimpleNamespace(check=lambda *a:None)
    loop.travel((466,333))


def test_manual_mouse_priority_prevents_route_controller_refocus():
    from conquest.overnight import OvernightLoop
    loop=OvernightLoop.__new__(OvernightLoop)
    assert loop.focus({'embedded_controls':{'manual_mouse':True}}) is False


@pytest.fixture(autouse=True)
def existing_city_visit(monkeypatch):
    from conquest import city_travel,world_travel
    monkeypatch.setattr(city_travel,'ensure_city_visit',lambda *a,**kw:False)
    monkeypatch.setattr(world_travel,'travel_to_map',lambda *a,**kw:None)


def test_empty_travel_potions_do_not_strand_character_before_town():
    from conquest.travel_care import TravelCare
    care=TravelCare.__new__(TravelCare);care.pending=None;care.last_heal=-float('inf')
    care.next_panel_check=float('inf')  # This fixture isolates potion behavior.
    care.inventory=SimpleNamespace(read=lambda:SimpleNamespace(count=lambda type_id:0))
    events=[];care.notify=events.append;care.xp_step=lambda health:None
    health={'embedded_controls':{'control':{'enabled':False},'life':dict(dead_candidate=False,current_hp=100,max_hp=500)}}
    care.check(health);care.check(health)
    assert len(events)==1 and events[0]['event']=='travel_healing_empty'


@pytest.mark.parametrize('progress',[True,False])
def test_town_travel_tracks_actual_progress_instead_of_lifetime_failures(monkeypatch,progress):
    from conquest import scene_input,overnight
    import numpy as np
    from conquest.navigation import TerrainMap
    monkeypatch.setattr(scene_input,'memory_player_anchor',lambda *a:(518,396))
    clock=[0.0]
    monkeypatch.setattr(overnight.time,'monotonic',lambda:clock[0])
    blocked=np.ones((30,30),dtype=bool);blocked[10,5:19]=False
    loop=OvernightLoop.__new__(OvernightLoop);loop.route=RouteLibrary().load('turtledove')
    loop.terrain=TerrainMap(1002,30,30,blocked,'',(),())
    position=[5,10]
    loop.living=lambda:{'embedded_controls':{'life':{'position':list(position)}}}
    loop.care=SimpleNamespace(check=lambda h:None,session=None)
    loop.record=lambda *a,**k:None
    def step(destination,expected_position):
        clock[0]+=30 if progress else 5
        if progress:position[0]+=1
        return {'reached':False}
    loop.stepper=SimpleNamespace(step_to=step)
    loop.focus=lambda h:True
    if progress:
        loop.travel((18,10))
        assert position==[18,10] and clock[0]>240
    else:
        with pytest.raises(ValueError,match='no improving progress'):
            loop.travel((18,10))
        assert clock[0]==15



def test_camera_edge_routes_around_hud_using_visible_walking_prefix(monkeypatch):
    from conquest import scene_input
    import numpy as np
    from conquest.navigation import TerrainMap
    loop=OvernightLoop.__new__(OvernightLoop);loop.route=RouteLibrary().load('turtledove')
    loop.terrain=TerrainMap(1002,30,30,np.zeros((30,30),dtype=bool),'',(),())
    position=[10,10];steps=[]
    def anchor(*a):return (518,128) if position==[10,10] else (518,396)
    monkeypatch.setattr(scene_input,'memory_player_anchor',anchor)
    loop.living=lambda:{'embedded_controls':{'life':{'position':list(position)}}}
    loop.care=SimpleNamespace(check=lambda h:None,session=None);loop.record=lambda *a,**k:None
    def step(destination,expected_position):
        x,y=anchor();dx=destination[0]-position[0];dy=destination[1]-position[1]
        assert 80<x+(dx-dy)*32<956 and 140<y+(dx+dy)*16<667
        steps.append(destination);position[:]=destination
        return {'reached':True}
    loop.stepper=SimpleNamespace(step_to=step)
    loop.travel((10,2))
    assert steps[0]!=(10,2) and position==[10,2]



def test_camera_clamped_above_hud_uses_clear_progressing_straight_detour():
    import numpy as np
    from conquest.navigation import TerrainMap,visible_cardinal_step
    blocked=np.zeros((50,50),dtype=bool)
    t=TerrainMap(1011,50,50,blocked,'',(),())
    assert visible_cardinal_step(t,(19,36),(40,20),(518,64))==(31,36)
    blocked[36,24]=True
    assert visible_cardinal_step(t,(19,36),(40,20),(518,64)) is None



@pytest.mark.parametrize('status,pending,checks',[(0,None,0),(0x8000000,None,0),(0x10,None,1),(0x8000000,{'at':1},1)])
def test_travel_xp_does_not_rescan_idle_state_after_first_activation(status,pending,checks):
    from conquest.travel_care import TravelCare
    care=TravelCare.__new__(TravelCare);calls=[]
    care._xp_skill=SimpleNamespace(pending=pending,step=lambda dispatch:calls.append(True) or False)
    care.xp_step({'embedded_controls':{'life':{'status':status}}})
    assert len(calls)==checks



def test_town_handoff_cancels_old_death_return_before_new_travel(tmp_path,monkeypatch):
    from conquest import overnight
    from conquest.discord_notify import write_json
    checkpoint=tmp_path/'recovery.json';monkeypatch.setattr(overnight,'RECOVERY_CHECKPOINT',checkpoint)
    write_json(checkpoint,{'identity':{'pid':1},'phase':'returning_after_revive'})
    loop=OvernightLoop.__new__(OvernightLoop);loop.info='unused';loop.route=RouteLibrary().load('bandit')
    loop.health=lambda:{'target':{'pid':1},'embedded_controls':{'external_execution':False}}
    calls=[]
    def request(info,op,body):
        calls.append(body)
        if 'route_id' in body:write_json(checkpoint,{'phase':'cancelled'})
    monkeypatch.setattr(overnight,'request',request)
    loop.stop_farm()
    assert calls==[{'enabled':False},{'route_id':'bandit'}]


def test_route_shutdown_ignores_removed_bridge_receipt(monkeypatch):
    from conquest import overnight
    loop=OvernightLoop.__new__(OvernightLoop)
    loop.phase='starting';loop.info='removed-bridge.json'
    loop.check_stop=lambda:None;loop.refresh=lambda:None
    loop.record=lambda *args,**kwargs:None
    loop.health=lambda:(_ for _ in ()).throw(overnight.OvernightStopped('Stopped by user'))
    monkeypatch.setattr(overnight,'request',lambda *args:(_ for _ in ()).throw(FileNotFoundError()))
    monkeypatch.setattr(overnight.ctypes,'windll',SimpleNamespace(kernel32=SimpleNamespace(
        SetThreadExecutionState=lambda *_:None)),raising=False)
    loop.run()
    assert loop.phase=='stopped'


def test_non_market_typed_stall_keeps_survival_and_recovery_active(monkeypatch):
    from conquest.merchants import delivery_route
    from conquest.travel_progress import TravelStalled
    monkeypatch.setattr(delivery_route,'pending',lambda:False)
    calls=[];loop=OvernightLoop.__new__(OvernightLoop)
    loop.living=lambda:(calls.append('living') or
        {'embedded_controls':{'life':{'map_id':1002,'position':[300,200]}}})
    loop.protect_during_movement_retry=lambda:calls.append('protect')
    loop.recover_travel_stall(TravelStalled('stalled'))
    assert calls==['living','protect']


@pytest.mark.parametrize('reason,map_id,pending',[
    ('service_deadline',1002,False),('no_progress',1036,False),('no_progress',1002,True)])
def test_market_deadline_or_unresolved_trade_cannot_enter_route_retry(monkeypatch,reason,map_id,pending):
    from conquest.merchants import delivery_route
    from conquest.travel_progress import TravelStalled
    monkeypatch.setattr(delivery_route,'pending',lambda:pending)
    loop=OvernightLoop.__new__(OvernightLoop);calls=[]
    loop.living=lambda:{'embedded_controls':{'life':{'map_id':map_id}}}
    loop.protect_during_movement_retry=lambda:calls.append('protect')
    error=TravelStalled('stalled',code=reason)
    with pytest.raises(TravelStalled) as raised:loop.recover_travel_stall(error)
    assert raised.value is error and calls==[]

def test_warehouse_travel_finishes_before_exact_approach_tile():
    loop=OvernightLoop.__new__(OvernightLoop)
    loop.route=RouteLibrary().load('poltergeist')
    loop.terrain=SimpleNamespace(map_id=1002)
    loop.record=lambda *a,**k:None
    loop.living=lambda:{'embedded_controls':{'life':{'position':[419,346]}}}
    calls=[]
    def town(action,**fields):
        calls.append((action,fields))
        return {'reachable':True}
    loop.town=town
    loop.travel((413,351),vendor_type=0)
    assert calls==[('vendor-status',{'vendor_type':0})]


def test_service_travel_finishes_when_memory_npc_is_reachable():
    loop=OvernightLoop.__new__(OvernightLoop)
    loop.route=RouteLibrary().load('bandit');loop.terrain=SimpleNamespace(map_id=1036)
    loop.record=lambda *a,**k:None
    loop.living=lambda:{'embedded_controls':{'life':{'position':[212,212]}}}
    calls=[]
    def town(action,**fields):
        calls.append((action,fields))
        return {'npc':{'position':[215,220],'draw_position':[486,444]}}
    loop.town=town
    loop.travel((213,219),service_name='Mark.Controller')
    assert calls==[('service-locate',{'name':'Mark.Controller'})]


def test_failed_corner_run_changes_path_instead_of_repeating(monkeypatch):
    from conquest import scene_input
    monkeypatch.setattr(scene_input,'memory_player_anchor',lambda *a:(518,396))
    loop=OvernightLoop.__new__(OvernightLoop);loop.route=RouteLibrary().load('poltergeist')
    position=[10,10];steps=[];avoids=[]
    def path(source,goal,*,avoid=()):
        avoids.append(set(avoid))
        if (9,10) in avoid:
            return [source,(10,11),(10,12),(9,12),(8,12)]
        return [source,(9,10),(9,11),(8,11),(8,12)]
    loop.terrain=SimpleNamespace(map_id=1002,path=path,straight_path=path)
    loop.living=lambda:{'embedded_controls':{'life':{'position':list(position)}}}
    loop.care=SimpleNamespace(check=lambda h:None,session=None)
    loop.record=lambda *a,**k:None
    def step(destination,expected_position):
        steps.append(destination)
        if len(steps)==1:return {'reached':False}
        position[:]=destination
        return {'reached':True}
    loop.stepper=SimpleNamespace(step_to=step)
    loop.travel((8,12))
    assert (9,10) in avoids[1]
    assert position==[8,12]


def test_travel_healing_focus_race_retries_without_marking_unsent_potion_used(monkeypatch):
    from types import SimpleNamespace as NS
    from conquest import travel_care as t
    care=t.TravelCare.__new__(t.TravelCare)
    care.next_panel_check=float('inf')  # This fixture isolates potion behavior.
    care.info='worker';care.session=None;care.layout=None;care.pending=None
    care.last_heal=-float('inf');care.notify=lambda e:None
    care.inventory=NS(read=lambda:NS(count=lambda item:3,items=[NS(uid=42,type_id=1000020,amount=1)]))
    health={'embedded_controls':{'control':{'enabled':False},'life':{
        'current_hp':300,'max_hp':834,'dead_candidate':False}}}
    monkeypatch.setattr(t,'resolve_player',lambda *a:{'name':100,'max_hp':200})
    def no_key(*a,**k):raise ValueError('Game lost focus; no key sent')
    monkeypatch.setattr(t,'request',no_key)
    with pytest.raises(t.TravelStateChanged,match='Regaining focus'):care.check(health)
    assert care.pending is None and care.last_heal==-float('inf')
    sent=[]
    def verified(*args):
        sent.append(args)
        return {'consumed':True,'hp_after':800,'remaining':2}
    monkeypatch.setattr(t,'request',verified)
    care.check(health)
    assert [a[2]['action'] for a in sent]==['consume-healing','close'] and care.pending is None
    assert care.last_heal>0


def test_travel_healing_uncertain_input_failure_is_not_blindly_retried(monkeypatch):
    from types import SimpleNamespace as NS
    from conquest import travel_care as t
    care=t.TravelCare.__new__(t.TravelCare)
    care.next_panel_check=float('inf')  # This fixture isolates potion behavior.
    care.info='worker';care.session=None;care.layout=None;care.pending=None
    care.last_heal=-float('inf');care.notify=lambda e:None
    care.inventory=NS(read=lambda:NS(count=lambda item:3,items=[NS(uid=42,type_id=1000020,amount=1)]))
    health={'embedded_controls':{'control':{'enabled':False},'life':{
        'current_hp':300,'max_hp':834,'dead_candidate':False}}}
    monkeypatch.setattr(t,'resolve_player',lambda *a:{'name':100,'max_hp':200})
    def failed(*a,**k):raise ValueError('Input completion uncertain')
    monkeypatch.setattr(t,'request',failed)
    with pytest.raises(ValueError,match='uncertain'):care.check(health)


def test_travel_heals_at_seventy_percent_and_does_not_stop_when_damage_masks_potion(monkeypatch):
    from types import SimpleNamespace as NS
    from conquest import travel_care as t
    care=t.TravelCare.__new__(t.TravelCare)
    care.next_panel_check=float('inf')  # This fixture isolates potion behavior.
    care.info='worker';care.session=None;care.layout=None;care.pending=None
    care.last_heal=-float('inf');events=[];care.notify=events.append
    count=[3]
    def inventory():
        value=count[0]
        return NS(count=lambda item:value,items=[NS(uid=42,type_id=1000020,amount=1)])
    care.inventory=NS(read=inventory)
    health={'embedded_controls':{'control':{'enabled':False},'life':{
        'current_hp':700,'max_hp':1000,'dead_candidate':False}}}
    now=[100];monkeypatch.setattr(t.time,'monotonic',lambda:now[0]);sent=[]
    def masked(info,operation,body):
        sent.append(body['action'])
        if body['action']=='consume-healing':
            count[0]=2
            raise ValueError('Healing consumption unverified; no repeat input issued')
    monkeypatch.setattr(t,'request',masked)
    care.check(health)
    assert sent==['consume-healing','close'] and care.pending is None
    assert events[-1]['event']=='travel_heal_unconfirmed' and events[-1]['consumed']
    assert care.last_heal==100


@pytest.mark.parametrize('arrows,potions',[(199,10),(3,1),(20,5)])
def test_remaining_supplies_never_trigger_a_proactive_town_trip(arrows,potions):
    route=RouteLibrary().load('bandit')
    route=route.model_copy(update={'supplies':route.supplies.model_copy(update={
        'arrows_return_below':200,'healing_return_below':6})})
    assert not needs_town({'arrows':arrows,'potions':potions,'free_slots':1},route)


def test_pharmacist_skipped_when_potions_full_and_only_protected_loot():
    from conquest.overnight import pharmacist_needed
    route=RouteLibrary().load('bandit')
    snapshot={'items':[{'type_id':route.supplies.healing_type,'amount':route.supplies.healing_restock_to},
                       {'type_id':1088001,'amount':1,'slot':1,'plus':0}],
              'capacity':40,'silver':200}
    assert not pharmacist_needed(snapshot,route)
    snapshot['items'][0]['amount']=0
    assert pharmacist_needed(snapshot,route)


@pytest.mark.parametrize('failure,change,arrows,potions,allowed',[
    ('Shop opening was not verified; no repeat input issued',None,5000,5,True),
    ('Shop opening was not verified; no repeat input issued','silver',5000,5,False),
    ('Shop opening was not verified; no repeat input issued','uid',5000,5,False),
    ('Shop opening was not verified; no repeat input issued',None,2,5,False),
    ('Shop opening was not verified; no repeat input issued',None,5000,0,False),
    ('Purchase was not verified; no repeat purchase issued',None,5000,5,False),
])
def test_optional_arrow_panel_failure_requires_unchanged_stock(monkeypatch,failure,change,arrows,potions,allowed):
    import copy
    from conquest import overnight
    monkeypatch.setattr(overnight.time,'sleep',lambda _:None)
    loop=OvernightLoop.__new__(OvernightLoop);loop.route=RouteLibrary().load('bandit')
    bag={'items':[{'uid':2,'type_id':loop.route.supplies.healing_type,'amount':potions}],
         'equipped_ammo':{'uid':1,'type_id':loop.route.supplies.arrow_type,'amount':arrows},
         'capacity':40,'silver':3000}
    calls=[];events=[]
    def town(action,**fields):
        calls.append(action)
        if action=='open':raise ValueError(failure)
        current=copy.deepcopy(bag)
        if len(calls)>1:
            if change=='silver':current['silver']-=1
            if change=='uid':current['equipped_ammo']['uid']=3
        return current
    loop.town=town;loop.record=lambda event,**fields:events.append(event)
    if allowed:
        assert loop.open_arrow_refill() is False
        assert events==['optional_arrow_refill_deferred']
    else:
        with pytest.raises(ValueError,match=failure):loop.open_arrow_refill()
        assert not events
    assert calls.count('open')==1 and 'buy' not in calls

@pytest.mark.parametrize('still_carried',[False,True])
def test_urgent_bank_deposits_before_hunting_without_supply_shopping(monkeypatch,still_carried):
    from conquest import banking,return_scroll,world_travel
    from conquest.merchants import handoff
    monkeypatch.setattr(handoff,'service_window',lambda loop,**kw:calls.append(('refill',kw)))
    calls=[];item={'uid':123,'type_id':500003,'plus':2,'slot':0,'amount':1}
    bag={'items':[item],'capacity':40,'silver':200,'equipped_ammo':{'type_id':1050001,'amount':900}}
    def town(action,**kw):
        calls.append(action)
        if action=='supplies':return bag
    def bank(loop):
        calls.append('deposit')
        if not still_carried:bag['items']=[{'type_id':1000020,'amount':5}]
        return True
    monkeypatch.setattr(banking,'after_shopping',bank)
    monkeypatch.setattr(return_scroll,'return_to_town',lambda loop:calls.append('return'))
    monkeypatch.setattr(world_travel,'travel_to_map',lambda *a:calls.append('travel'))
    loop=SimpleNamespace(route=RouteLibrary().load('bandit'),town=town,record=lambda *a,**kw:None,
                         restock=lambda:pytest.fail('Stocked bank trip must not shop'))
    if still_carried:
        with pytest.raises(ValueError,match='remain carried'):OvernightLoop.bank_urgent_valuables(loop)
    else:OvernightLoop.bank_urgent_valuables(loop)
    assert calls.index('travel')<calls.index('deposit')
    assert 'buy' not in calls
    if still_carried:assert not any(isinstance(c,tuple) and c[0]=='refill' for c in calls)
    else:assert calls[-1]==('refill',{'town':True})


@pytest.mark.parametrize('manual_stop',[False,True])
def test_input_handoff_during_travel_reobserves_or_honors_stop(monkeypatch,manual_stop):
 from conquest import scene_input,overnight
 from conquest.capture import CaptureUnavailable
 from conquest.navigation import TerrainMap
 import numpy as np
 monkeypatch.setattr(scene_input,'memory_player_anchor',lambda *a:(518,396))
 loop=OvernightLoop.__new__(OvernightLoop);loop.route=RouteLibrary().load('turtledove')
 loop.terrain=TerrainMap(1002,30,30,np.zeros((30,30),dtype=bool),'',(),())
 position=[10,10];steps=[];stops=[]
 loop.living=lambda:{'embedded_controls':{'life':{'position':list(position)}}}
 loop.care=SimpleNamespace(check=lambda h:None,session=None);loop.record=lambda *a,**k:None
 def stop():
  stops.append(True)
  if manual_stop:raise OvernightStopped('Stopped by user')
 loop.check_stop=stop
 def step(destination,expected_position):
  steps.append(expected_position)
  if len(steps)==1:
   position[:]=[11,10]
   raise CaptureUnavailable('Automation stopped or manual input active')
  assert expected_position==(11,10)
  position[:]=destination
  return {'reached':True}
 loop.stepper=SimpleNamespace(step_to=step)
 if manual_stop:
  with pytest.raises(OvernightStopped):loop.travel((14,10))
  assert len(steps)==1
 else:
  loop.travel((14,10));assert position==[14,10] and len(steps)==2
 assert stops


def test_initial_blacksmith_open_failure_still_completes_storage_when_stocked(monkeypatch):
    from conquest import equipment,banking,overnight
    monkeypatch.setattr(overnight.time,'sleep',lambda _:None)
    monkeypatch.setattr(equipment,'EquipmentReview',lambda loop:SimpleNamespace(visit=lambda vendor:None))
    stored=[]
    monkeypatch.setattr(banking,'after_shopping',lambda loop:stored.append(True))
    loop=OvernightLoop.__new__(OvernightLoop)
    loop.route=RouteLibrary().load('poltergeist');loop.cycles=0
    calls=[];events=[]
    def town(action,**fields):
        calls.append((action,fields))
        if action=='open' and fields['vendor_type']==5:
            raise ValueError('Shop opening was not verified; no repeat input issued')
        if action=='supplies':
            return {'items':[{'type_id':1050000,'amount':1600},{'type_id':1000020,'amount':15}],
                    'capacity':40,'silver':10000}
        return {}
    loop.town=town;loop.travel=lambda p:None
    loop.sell_junk=lambda v:None;loop.recycle_small_arrows=lambda:None
    loop.record=lambda event,**fields:events.append(event)
    loop.restock()
    assert stored==[True] and loop.cycles==1
    assert 'optional_arrow_refill_deferred' in events
    assert events[-1]=='restock_complete'
    assert not any(action=='buy' for action,fields in calls)
