"""Travel must not depend on booth IDs or produce a delivery snapshot."""
from types import SimpleNamespace as NS
import struct
import pytest
from conquest.merchants import memory


@pytest.mark.parametrize('change',[None,'position','death','server','silver','dialog'])
def test_travel_snapshot_is_scoped_and_rechecks_mutable_state(monkeypatch,change):
    reader=memory.MerchantMemory.__new__(memory.MerchantMemory)
    reader.base=0x100000;reader.player=object()
    reader.inventory=NS(layout=NS(silver=0x20))
    reader.observer=NS(character='Dutch',health_layout=object())
    counts={}
    def block(address,size):
        counts[address]=counts.get(address,0)+1
        changed=counts[address]>1
        if address==reader.base+0x697860:
            return (b'wrong' if change=='server' and changed else b'Classic_US').ljust(64,b'\0')
        if address==0x200020:return struct.pack('<I',200 if change=='silver' and changed else 100)
        if address in (0x30000c,0x40000c):return bytes([int(change=='dialog' and changed)])
        pytest.fail('Travel unexpectedly inspected another memory field')
    reader.s=NS(read_block=block,identity={'pid':7},assert_identity=lambda:None)
    reader.gui=NS(model=lambda model,vtable:{14:0x300000,15:0x400000}[model],windows=lambda:[])
    calls=[]
    def life(*args):
        calls.append(1);changed=len(calls)>1
        return NS(object_address=0x500000,map_id=1036,current_hp=100,
                  position=(101,100) if change=='position' and changed else (100,100),
                  dead_candidate=change=='death' and changed)
    monkeypatch.setattr(memory,'read_life',life)
    monkeypatch.setattr(memory,'resolve_player',lambda *args:{'object':0x200000})
    if change:
        with pytest.raises(ValueError,match='changed'):reader.read_travel()
    else:
        result=reader.read_travel()
        assert result['observation']=='travel_only' and result['map_id']==1036
        assert not {'inventory','booth','capacity','character_uid'} & result.keys()
        assert result['silver']==100


def test_transit_waypoint_uses_camera_anchor_and_current_viewport(monkeypatch):
    from conquest.merchants import return_driver as route
    monkeypatch.setattr(route,'clear_segment',lambda *args:True)
    path=[(100,100+i) for i in range(20)]
    assert route.transit_waypoint(None,path,(700,300),(1400,900))==(100,112)
    # Near the left camera edge, the same twelve-tile click is off screen.
    assert route.transit_waypoint(None,path,(220,300),(1400,900))==(100,104)
    with pytest.raises(ValueError,match='No visible'):
        route.transit_waypoint(None,path,(90,300),(1400,900))


def test_stall_approach_does_not_target_the_assumed_booth_standing_tile():
    from conquest.merchants.return_driver import stall_approach
    from conquest.navigation import line_tiles
    terrain=NS(travel_path=lambda start,end:line_tiles(start,end))
    distance,target=stall_approach(terrain,(223,185),{'position':[230,185]})
    assert target==(228,185) and distance==6


@pytest.mark.parametrize('variant',['valid','missing_uid','changed_accessor'])
def test_character_identity_uses_pinned_self_field_not_booth_field(variant):
    base=0x1000000;actor=0x2000000
    values={base+0x8dc8:bytes.fromhex('e8638d17008b486841394f10'),
            base+0x97bc:bytes.fromhex('e86f8317008b4868394e687520'),
            actor+0x68:struct.pack('<I',123456),actor+0x3258:bytes(4)}
    if variant=='missing_uid':values[actor+0x68]=bytes(4)
    if variant=='changed_accessor':values[base+0x8dc8]=bytes(12)
    session=NS(read_block=lambda address,size:values[address])
    if variant=='valid':assert memory.character_uid(session,base,actor)==123456
    else:
        with pytest.raises(ValueError):memory.character_uid(session,base,actor)


@pytest.mark.parametrize('transient',[False,True])
def test_travel_retries_only_torn_observations_without_sending_input(monkeypatch,transient):
    from conquest.merchants import return_driver as route
    calls=[]
    def read():
        calls.append('read')
        if len(calls)==1:
            if transient:raise memory.TransitObservationChanged('position changed')
            raise ValueError('wrong character')
        return {'position':[223,185]}
    monkeypatch.setattr(route.time,'sleep',lambda _:None)
    driver=route.ReturnDriver(NS(observer=object(),memory=NS(read_travel=read)),travel_only=True)
    if transient:
        assert driver.read()=={'position':[223,185]} and calls==['read','read']
    else:
        with pytest.raises(ValueError,match='wrong character'):driver.read()
        assert calls==['read']
