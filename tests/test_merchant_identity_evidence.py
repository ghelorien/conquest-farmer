import struct
from types import SimpleNamespace as NS
import pytest
from conquest.merchants import identity_evidence,farmer_trade


@pytest.fixture
def scene(monkeypatch):
    base=0x10000000;collection=0x20000000;begin=0x30000000;actor=0x40000000
    raw=bytearray(0x120)
    struct.pack_into('<Q',raw,0,base+0x5c5e20)
    struct.pack_into('<I',raw,0x78,1173856)
    raw[0xa4:0xaa]=b'Dutch\0'
    struct.pack_into('<2I',raw,0xe8,211,196)
    struct.pack_into('<2i',raw,0xf8,700,434)
    entries=struct.pack('<2Q',0,actor)
    def read(address,size):
        if address==begin:return entries[:size]
        if address==actor:return bytes(raw[:size])
        pytest.fail('Unexpected memory read')
    p=NS(begin_offset=0x58,end_offset=0x60,capacity_offset=0x68,
         entry_stride=16,entry_object_offset=8,max_objects=1000)
    headers=[(collection+o,'u64') for o in (0x58,0x60,0x68)]
    def sample(session,fields):
        if fields==headers:return [begin,begin+16,begin+16]
        if fields==[(actor,'u64')]:return [base+0x5c5e20]
        if fields==[]:return []
        pytest.fail('Unexpected scalar read')
    for module in (identity_evidence,farmer_trade):monkeypatch.setattr(module,'sample_fields',sample)
    s=NS(read_block=read,assert_identity=lambda:None,identity={'pid':1})
    observer=NS(adapter=s,character='Spiritual',entities=NS(layout=p,_resolve=lambda:(base,collection,[])))
    peer={'character_uid':1173856,'character':'Dutch','position':[211,196],'identity':{'pid':2}}
    return NS(observer=observer,peer=peer,raw=raw)


@pytest.mark.parametrize('mismatch',[None,'uid','name','position'])
def test_cross_client_peer_requires_exact_identity_and_position(scene,mismatch):
    if mismatch=='uid':scene.peer['character_uid']+=1
    if mismatch=='name':scene.peer['character']='Spiritual'
    if mismatch=='position':scene.peer['position']=[212,196]
    if mismatch:
        with pytest.raises(ValueError):identity_evidence.peer_evidence(scene.observer,scene.peer)
    else:
        evidence=identity_evidence.peer_evidence(scene.observer,scene.peer)
        assert evidence['input_qualified'] is False
        assert evidence['peer']['draw_i32']==[700,434]
        assert evidence['peer']['uid_offset']==0x78


def test_recipient_uses_observed_integer_draw_coordinates(scene):
    evidence=identity_evidence.peer_evidence(scene.observer,scene.peer)
    profile={'recipient':evidence['peer'],'gui_size':[1400,900]}
    result=farmer_trade.recipient_record(scene.observer,profile,scene.peer)
    assert result['point']==[700,434]
    profile['recipient']['draw_format']='f32'
    with pytest.raises(ValueError,match='projection format'):
        farmer_trade.recipient_record(scene.observer,profile,scene.peer)
