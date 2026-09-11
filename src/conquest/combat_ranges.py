"""Read the equipped bow and learned Scatter definition from pinned client memory."""
import struct
from conquest.addressing import checked_address
from conquest.memory_life import CLIENT_SHA256, read_life


def read_combat_ranges(observer):
    s=observer.adapter
    if s.expected_sha256!=CLIENT_SHA256:
        raise ValueError('Combat range client differs from the qualified profile')
    base=next(m['base'] for m in s.modules if m['name'].lower()=='imconquer.exe')
    life=read_life(s,observer.health_layout,observer.character)
    if life.dead_candidate:raise ValueError('Living archer required for combat ranges')
    actor=life.object_address
    bow_pointer=s.read_block(actor+0xc08,8)
    bow_address=checked_address(struct.unpack('<Q',bow_pointer)[0])
    bow=s.read_block(bow_address,0x74)
    bow_type=struct.unpack_from('<I',bow,0x10)[0]
    bow_range=struct.unpack_from('<H',bow,0x70)[0]
    if struct.unpack_from('<Q',bow)[0]!=base+0x5cf220 or bow_type//1000!=500 or not 1<=bow_range<=20:
        raise ValueError('Equipped bow range is invalid')
    # Character learned-magic vector; each entry is a shared_ptr (16 bytes).
    header=s.read_block(actor+0x1968,24)
    start,end,capacity=struct.unpack('<3Q',header)
    if not start<=end<=capacity or not 0<end-start<=128*16 or (end-start)%16 or (capacity-start)%16:
        raise ValueError('Learned skill vector is invalid')
    entries=s.read_block(checked_address(start,end-start),end-start)
    matches=[]
    for offset in range(0,len(entries),16):
        pointer=checked_address(struct.unpack_from('<Q',entries,offset)[0])
        raw=s.read_block(pointer,0x68)
        if struct.unpack_from('<Q',raw)[0]!=base+0x5cff78:
            raise ValueError('Learned skill object type changed')
        if struct.unpack_from('<I',raw,0x10)[0]!=8001:continue
        length,cap=struct.unpack_from('<QQ',raw,0x28)
        if length!=7 or cap!=15 or raw[0x18:0x20]!=b'Scatter\0':
            raise ValueError('Learned Scatter name differs')
        level=struct.unpack_from('<I',raw,0x48)[0]
        radius,distance=struct.unpack_from('<II',raw,0x60)
        # Rank is unsigned metadata, not a capability gate. Accept every rank
        # represented by this learned skill record; validate identity, stable
        # reads and the independently supported combat range instead.
        if not 1<=radius<=distance<=20:
            raise ValueError('Learned Scatter range is invalid')
        fresh=s.read_block(pointer,0x68)
        if raw[:8]!=fresh[:8] or raw[0x10:]!=fresh[0x10:]:
            raise ValueError('Scatter changed during range observation')
        matches.append({'type_id':8001,'level':level,'range':radius,'distance':distance})
    if len(matches)!=1:raise ValueError('Exactly one learned Scatter is required')
    fresh_bow=s.read_block(bow_address,0x74)
    if (fresh_bow[:0x14]!=bow[:0x14] or fresh_bow[0x70:0x74]!=bow[0x70:0x74]
            or s.read_block(actor+0xc08,8)!=bow_pointer
            or s.read_block(actor+0x1968,24)!=header or s.read_block(start,end-start)!=entries):
        raise ValueError('Combat range identity changed during observation')
    latest=read_life(s,observer.health_layout,observer.character)
    if latest.object_address!=actor or latest.dead_candidate:
        raise ValueError('Character changed during range observation')
    s.assert_identity()
    return {'bow':{'type_id':bow_type,'range':bow_range},'scatter':matches[0],
            'source':'read_only_memory'}
