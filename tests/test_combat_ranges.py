import struct
from types import SimpleNamespace as NS
import pytest
from conquest import combat_ranges
from conquest.memory_life import CLIENT_SHA256


def fixture(monkeypatch):
    base=0x140000000;actor=0x100000;bow_address=0x200000;array=0x300000;skill_address=0x400000
    bow=bytearray(0x74);struct.pack_into('<Q',bow,0,base+0x5cf220)
    struct.pack_into('<II',bow,8,123,0);struct.pack_into('<I',bow,0x10,500035)
    struct.pack_into('<H',bow,0x70,12)
    skill=bytearray(0x68);struct.pack_into('<Q',skill,0,base+0x5cff78)
    struct.pack_into('<I',skill,0x10,8001);skill[0x18:0x20]=b'Scatter\0'
    struct.pack_into('<QQ',skill,0x28,7,15);struct.pack_into('<II',skill,0x60,8,15)
    blobs={actor+0xc08:struct.pack('<Q',bow_address),bow_address:bow,
           actor+0x1968:struct.pack('<3Q',array,array+16,array+16),
           array:struct.pack('<2Q',skill_address,skill_address-16),skill_address:skill}
    session=NS(expected_sha256=CLIENT_SHA256,modules=[{'name':'ImConquer.exe','base':base}],
               read_block=lambda address,size:bytes(blobs[address][:size]),assert_identity=lambda:None)
    life=NS(object_address=actor,dead_candidate=False)
    monkeypatch.setattr(combat_ranges,'read_life',lambda *a:life)
    return NS(adapter=session,health_layout=None,character='Parasite'),blobs,skill


def test_range_and_aim_distance_are_separate_learned_memory_fields(monkeypatch):
    observer,_,_=fixture(monkeypatch)
    result=combat_ranges.read_combat_ranges(observer)
    assert result['bow']['range']==12
    assert result['scatter']=={'type_id':8001,'level':0,'range':8,'distance':15}


@pytest.mark.parametrize('level',[0,4,5,6,10,255,65535,0xffffffff])
def test_scatter_rank_never_rejects_an_otherwise_valid_learned_skill(monkeypatch,level):
    observer,_,skill=fixture(monkeypatch)
    struct.pack_into('<I',skill,0x48,level)
    struct.pack_into('<II',skill,0x60,12,20)
    result=combat_ranges.read_combat_ranges(observer)
    assert result['scatter']=={'type_id':8001,'level':level,'range':12,'distance':20}


@pytest.mark.parametrize('offset,value',[(0x10,8002),(0x60,0),(0x60,16),(0x64,99)])
def test_unlearned_changed_or_invalid_skill_does_not_authorize_a_range(monkeypatch,offset,value):
    observer,_,skill=fixture(monkeypatch)
    struct.pack_into('<I',skill,offset,value)
    with pytest.raises(ValueError):combat_ranges.read_combat_ranges(observer)


def test_swapped_bow_during_read_is_rejected(monkeypatch):
    observer,blobs,_=fixture(monkeypatch)
    read=observer.adapter.read_block;calls=[0]
    def changing(address,size):
        if address==0x200000:
            calls[0]+=1
            if calls[0]>1:struct.pack_into('<I',blobs[address],8,456)
        return read(address,size)
    observer.adapter.read_block=changing
    with pytest.raises(ValueError,match='identity changed'):combat_ranges.read_combat_ranges(observer)


def test_rank_up_mid_read_requires_a_fresh_observation(monkeypatch):
    observer,_,skill=fixture(monkeypatch)
    read=observer.adapter.read_block;calls=[0]
    def changing(address,size):
        if address==0x400000:
            calls[0]+=1
            if calls[0]>1:struct.pack_into('<I',skill,0x48,6)
        return read(address,size)
    observer.adapter.read_block=changing
    with pytest.raises(ValueError,match='changed during range observation'):
        combat_ranges.read_combat_ranges(observer)
    assert combat_ranges.read_combat_ranges(observer)['scatter']['level']==6
