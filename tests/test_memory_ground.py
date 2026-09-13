import struct
from types import SimpleNamespace

import pytest
from conquest import memory_ground


def fixture(monkeypatch):
    base,c,begin,obj=0x140000000,0x100000,0x200000,0x300000
    owner=0x400000
    record=bytearray(0x60)
    struct.pack_into('<Q',record,0,base+0x5cdaf0)
    struct.pack_into('<II',record,0x40,601,585)
    struct.pack_into('<II',record,0x50,0,1090020)
    registry=bytearray(struct.pack('<4I2Q',1996,1090020,601,585,obj+16,obj))
    entries=struct.pack('<QQ',owner+16,owner)
    lookup={base+0x6994d8:begin,base+0x6994e0:begin+16,
            base+0x6994e8:begin+16,owner:base+0x5ccc08,0x500000:0x600000}
    def fields(session,specs):
        return [lookup[address] for address,_ in specs]
    monkeypatch.setattr(memory_ground,'sample_fields',fields)
    def read(address,size):
        data={obj:record,owner+16:registry,begin:entries}[address]
        assert len(data)==size
        return bytes(data)
    from conquest.memory_life import CLIENT_SHA256
    session=SimpleNamespace(read_block=read,assert_identity=lambda:None,
                            expected_sha256=CLIENT_SHA256)
    layout=SimpleNamespace(begin_offset=0x58,end_offset=0x60,capacity_offset=0x68,max_objects=4096)
    entities=SimpleNamespace(session=session,layout=layout,_resolve=lambda:(base,c,[(0x500000,0x600000)]))
    entities.test_registry=registry
    entities.test_fields=lookup
    return memory_ground.MemoryGroundReader(entities),record


def test_ground_identity_type_and_position_are_read_without_images(monkeypatch):
    reader,record=fixture(monkeypatch)
    drop,=reader.read()
    assert (drop.uid,drop.type_id,drop.position,drop.silver)==(1996,1090020,(601,585),True)


def test_recycled_ground_record_is_rejected(monkeypatch):
    reader,record=fixture(monkeypatch)
    old=reader.entities.session.read_block
    def read(address,size):
        value=old(address,size)
        if address==0x400010:
            struct.pack_into('<I',reader.entities.test_registry,0,1997)
        return value
    reader.entities.session.read_block=read
    with pytest.raises(ValueError,match='changed'):
        reader.read()


def test_disappearance_without_silver_increase_is_not_a_pickup(monkeypatch):
    reader,_=fixture(monkeypatch)
    drop,=reader.read()
    assert memory_ground.pickup_delta(drop,SimpleNamespace(silver=100),SimpleNamespace(silver=100))==0
    assert memory_ground.pickup_delta(drop,SimpleNamespace(silver=100),SimpleNamespace(silver=113))==13


def test_render_reference_counts_do_not_invalidate_stable_ground_identity(monkeypatch):
    reader,record=fixture(monkeypatch)
    old=reader.entities.session.read_block
    def read(address,size):
        value=old(address,size)
        if address==0x300000:
            struct.pack_into('<I',record,8,struct.unpack_from('<I',record,8)[0]+1)
        return value
    reader.entities.session.read_block=read
    assert reader.read()[0].position==(601,585)


def test_weapon_durability_is_not_reported_as_quantity():
    from conquest.memory_inventory import Item
    drop=memory_ground.GroundItem(10,1000,421003,(10,10))
    before=SimpleNamespace(items=())
    after=SimpleNamespace(items=(Item(99,421003,1706,2000,0),))
    assert memory_ground.pickup_delta(drop,before,after)==1


def test_type_zero_registry_placeholder_does_not_block_stable_drops(monkeypatch):
    reader,record=fixture(monkeypatch)
    session=reader.entities.session
    old_read=session.read_block
    old_fields=memory_ground.sample_fields
    other=struct.pack('<4I2Q',1997,0,0,0,0,0)
    entries=struct.pack('<QQQQ',0x400010,0x400000,0x410010,0x410000)
    def fields(s,specs):
        return [0x200020 if a in (0x1406994e0,0x1406994e8) else
                0x1405ccc08 if a==0x410000 else old_fields(s,[(a,k)])[0]
                for a,k in specs]
    def read(a,n):
        if a==0x200000:return entries
        if a==0x410010:return other
        return old_read(a,n)
    monkeypatch.setattr(memory_ground,'sample_fields',fields)
    session.read_block=read
    drop,=reader.read()
    assert drop.uid==1996 and drop.silver


def test_render_counter_changes_do_not_change_drop_identity(monkeypatch):
    reader,record=fixture(monkeypatch)
    old=reader.entities.session.read_block
    def read(a,n):
        value=old(a,n)
        if a==0x300000:
            counter=struct.unpack_from('<I',record,0x50)[0]
            struct.pack_into('<I',record,0x50,counter+1)
        return value
    reader.entities.session.read_block=read
    first,=reader.read()
    assert first.uid==1996
    assert reader.read()==(first,)


@pytest.mark.parametrize('offset,value',[(0,0),(4,123456),(8,600),(12,584),
                                        (16,0x310010),(24,0x310000)])
def test_registry_must_match_actor_identity_and_geometry(monkeypatch,offset,value):
    reader,_=fixture(monkeypatch)
    struct.pack_into('<Q' if offset>=16 else '<I',reader.entities.test_registry,offset,value)
    with pytest.raises(ValueError):
        reader.read()


def test_creation_time_high_word_is_rechecked(monkeypatch):
    reader,record=fixture(monkeypatch)
    old=reader.entities.session.read_block
    def read(a,n):
        value=old(a,n)
        if a==0x300000:struct.pack_into('<I',record,0x4c,1)
        return value
    reader.entities.session.read_block=read
    with pytest.raises(ValueError,match='changed'):
        reader.read()


def test_empty_ground_registry_is_valid(monkeypatch):
    reader,_=fixture(monkeypatch)
    for address in (0x1406994d8,0x1406994e0,0x1406994e8):
        reader.entities.test_fields[address]=0
    assert reader.read()==()


def test_ground_registry_bound_is_checked_before_reading_entries(monkeypatch):
    reader,_=fixture(monkeypatch)
    for address in (0x1406994e0,0x1406994e8):
        reader.entities.test_fields[address]=0x200000+257*16
    with pytest.raises(ValueError,match='Too many'):
        reader.read()


def test_unqualified_client_is_rejected(monkeypatch):
    reader,_=fixture(monkeypatch)
    reader.entities.session.expected_sha256='other-client'
    with pytest.raises(ValueError,match='Unqualified'):
        memory_ground.MemoryGroundReader(reader.entities)


def test_slow_ground_observation_still_expires(monkeypatch):
    reader,_=fixture(monkeypatch)
    times=iter((1,1.51))
    monkeypatch.setattr(memory_ground.time,'monotonic',lambda:next(times))
    with pytest.raises(ValueError,match='expired'):
        reader.read()


@pytest.mark.parametrize('kind,plus,wanted',[
    (480003,0,False),(480006,0,False),(480003,None,False),
    (480007,0,False),(480008,0,False),(480009,0,True),
    (480008,1,True),(480008,2,True),
    (480007,1,True),
    (480003,1,True),(480003,12,True),(480003,13,False),
    (1088000,None,True),(1088001,None,True),
    (1090000,None,False),(1090010,None,False),(1090020,None,False),
    (1091000,None,False),(1091010,None,False),(1091020,None,False),
    (1000000,0,False),(1000020,0,False),(1050000,0,False),
    (700001,0,False),(1001007,0,False)])
def test_explicit_ground_loot_allowlist(kind,plus,wanted):
    drop=memory_ground.GroundItem(1,1000,kind,(10,10),plus=plus)
    assert memory_ground.wanted_drop(drop) is wanted


def test_ground_plus_uses_ground_formatter_field_and_is_rechecked(monkeypatch):
    reader,record=fixture(monkeypatch)
    record[0x58]=1
    assert reader.read()[0].plus==1
    old=reader.entities.session.read_block
    def read(a,n):
        value=old(a,n)
        if a==0x300000:record[0x58]=2
        return value
    reader.entities.session.read_block=read
    with pytest.raises(ValueError,match='changed'):
        reader.read()
