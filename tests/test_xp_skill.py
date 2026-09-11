import struct
from types import SimpleNamespace as NS
import pytest
from conquest import xp_skill as xp
from conquest.memory_life import CLIENT_SHA256


def fixture(monkeypatch):
    actor=0x100000;array=0x200000;pointer=0x300000;base=0x140000000
    skill=bytearray(0x68);struct.pack_into('<Q',skill,0,base+0x5cff78)
    struct.pack_into('<I',skill,8,1);struct.pack_into('<I',skill,0x10,8002)
    skill[0x18:0x1c]=b'Fly\0';struct.pack_into('<QQ',skill,0x28,3,15)
    struct.pack_into('<I',skill,0x44,2)
    blobs={actor+0x3cc:struct.pack('<I',100),actor+0x30:struct.pack('<Q',0x10),
           actor+0x1998:struct.pack('<3Q',array,array+16,array+16),
           array:struct.pack('<2Q',pointer,0),pointer:skill}
    session=NS(expected_sha256=CLIENT_SHA256,modules=[{'name':'ImConquer.exe','base':base}],
        read_block=lambda address,size:bytes(blobs[address][:size]),assert_identity=lambda:None)
    life=NS(object_address=actor,dead_candidate=False)
    monkeypatch.setattr(xp,'read_life',lambda *args:life)
    window=NS(position=(490.,613.),size=(56.,56.),scroll=(0.,0.))
    monkeypatch.setattr(xp,'MemoryGui',lambda s:NS(read=lambda name:window))
    return NS(adapter=session,health_layout=None,character='Parasite'),blobs,window


def test_charge_read_separate_from_experience_and_verified_flight(monkeypatch):
    observer,blobs,_=fixture(monkeypatch);events=[];clicks=[]
    runner=xp.XpSkill(observer,lambda *args:events.append(args))
    assert runner.step(clicks.append)
    assert clicks==[(518,641)] and events[-1][0]=='xp_fly_attempt'
    blobs[0x100030]=struct.pack('<Q',0x8000000);blobs[0x1003cc]=struct.pack('<I',0)
    assert not runner.step(clicks.append)
    assert events[-1][0]=='xp_fly_verified' and len(clicks)==1


@pytest.mark.parametrize('charge,status',[(99,0x10),(100,0),(100,0x8000010),(0,0)])
def test_not_ready_or_already_flying_never_clicks(monkeypatch,charge,status):
    observer,blobs,_=fixture(monkeypatch)
    blobs[0x1003cc]=struct.pack('<I',charge);blobs[0x100030]=struct.pack('<Q',status)
    assert not xp.XpSkill(observer,lambda *args:None).step(lambda p:pytest.fail('Unexpected click'))


def test_changed_popup_or_wrong_skill_never_uses_old_coordinates(monkeypatch):
    observer,blobs,window=fixture(monkeypatch)
    runner=xp.XpSkill(observer,lambda *args:None)
    window.size=(112.,56.)
    assert not runner.step(lambda p:pytest.fail('Unexpected click'))
    window.size=(56.,56.)
    struct.pack_into('<I',blobs[0x300000],0x10,8001)
    assert not runner.step(lambda p:pytest.fail('Unexpected click'))


def test_unconfirmed_click_does_not_claim_flight_or_spam(monkeypatch):
    observer,_,_=fixture(monkeypatch);events=[];clicks=[];now=[10.]
    monkeypatch.setattr(xp.time,'monotonic',lambda:now[0])
    runner=xp.XpSkill(observer,lambda *args:events.append(args))
    for _ in range(100):
        runner.step(clicks.append);now[0]+=.1
    assert len(clicks)==3
    assert not any(event=='xp_fly_verified' for event,_ in events)
