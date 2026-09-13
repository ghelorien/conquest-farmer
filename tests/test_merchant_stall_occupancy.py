from types import SimpleNamespace as NS
import pytest
from conquest.merchants import stalls


@pytest.mark.parametrize('case',['known_pair','new_occupant','moved','out_of_range','unknown_layout'])
def test_scene_booth_occupancy_uses_owner_entity_not_flag_name(monkeypatch,case):
    occupied={'uid':101485,'name':'ShopFlag','type_id':0,'model':1086,'position':[262,206]}
    empty={'uid':101486,'name':'ShopFlag','type_id':0,'model':1086,'position':[262,210]}
    booth={'uid':103060,'name':'Dutch','type_id':0,'model':406,'position':[265,206]}
    snapshots=[[occupied,empty,booth],[occupied,empty,booth]]
    if case=='new_occupant':snapshots[1]=[*snapshots[1],{**booth,'uid':103061,'name':'Spiritual','position':[265,210]}]
    monkeypatch.setattr(stalls,'scene_stalls',lambda o:snapshots.pop(0))
    positions=[(260,210),(260,210)]
    if case=='moved':positions[1]=(261,210)
    if case=='out_of_range':positions=[(240,210),(240,210)]
    monkeypatch.setattr('conquest.memory_life.read_life',lambda *a:NS(map_id=1036,dead_candidate=False,position=positions.pop(0)))
    observer=NS(adapter=None,health_layout=None,character='Spiritual')
    spec={'occupancy_mode':'scene_booth','booth_model':406,'booth_offset':[3,0],'vacancy_radius':8}
    if case=='unknown_layout':spec['booth_offset']=[2,0]
    if case in ('new_occupant','moved','unknown_layout'):
        with pytest.raises(ValueError):stalls.vacant_flags(observer,spec)
    else:assert stalls.vacant_flags(observer,spec)==([] if case=='out_of_range' else [empty])


@pytest.mark.parametrize('change',[{}, {'name':'Stranger'}, {'uid':55}, {'model':1086}, {'position':[100,100]}])
def test_owned_booth_requires_exact_owner_identity_and_nearby_scene(monkeypatch,change):
    booth={'uid':900,'name':'Dutch','type_id':0,'model':406,'position':[265,206]}
    monkeypatch.setattr(stalls,'scene_stalls',lambda o:[{**booth,**change}])
    snapshot={'map_id':1036,'own_booth_uid':900,'position':[264,206]}
    if change:
        with pytest.raises(ValueError):stalls.owned_booth(NS(character='Dutch'),snapshot)
    else:assert stalls.owned_booth(NS(character='Dutch'),snapshot)==booth


@pytest.mark.parametrize('message',['Shop flag changed while reading','Shop flag identity is invalid'])
def test_scene_retry_is_limited_to_torn_snapshot(monkeypatch,message):
    calls=[]
    def read(observer):
        calls.append(1)
        if len(calls)==1:raise ValueError(message)
        return ['fresh_complete_snapshot']
    monkeypatch.setattr(stalls,'_scene_stalls',read)
    if 'changed while reading' in message:
        assert stalls.scene_stalls(None)==['fresh_complete_snapshot']
        assert len(calls)==2
    else:
        with pytest.raises(ValueError,match='identity'):stalls.scene_stalls(None)
        assert len(calls)==1
