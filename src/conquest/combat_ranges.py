"""Read the equipped bow and learned Scatter definition from pinned client memory."""
import struct
from conquest.addressing import checked_address
from conquest.memory_life import CLIENT_SHA256, read_life


def _read_combat_ranges(s,life,layout,*,require_scatter=True):
    base=next(m['base'] for m in s.modules if m['name'].lower()=='imconquer.exe')
    if life.dead_candidate:raise ValueError('Living archer required for combat ranges')
    actor=life.object_address
    bow_pointer=s.read_block(actor+layout.bow_offset,8)
    bow_address=checked_address(struct.unpack('<Q',bow_pointer)[0])
    bow=s.read_block(bow_address,0x74)
    bow_type=struct.unpack_from('<I',bow,0x10)[0];bow_range=struct.unpack_from('<H',bow,0x70)[0]
    if struct.unpack_from('<Q',bow)[0]!=base+layout.item_vtable_rva or bow_type//1000!=500 or not 1<=bow_range<=20:
        raise ValueError('Equipped bow range is invalid')
    header=s.read_block(actor+layout.learned_skills_offset,24)
    start,end,capacity=struct.unpack('<3Q',header)
    if (not start<=end<=capacity or not 0<=end-start<=capacity-start<=128*16
            or (end-start)%16 or (capacity-start)%16 or (start==0 and capacity!=0)):
        raise ValueError('Learned skill vector is invalid')
    if start:checked_address(start,max(1,capacity-start))
    entries=s.read_block(checked_address(start,end-start),end-start) if end>start else b'';matches=[]
    for offset in range(0,len(entries),16):
        pointer=checked_address(struct.unpack_from('<Q',entries,offset)[0]);raw=s.read_block(pointer,0x68)
        if struct.unpack_from('<Q',raw)[0]!=base+layout.skill_vtable_rva:raise ValueError('Learned skill object type changed')
        if struct.unpack_from('<I',raw,0x10)[0]!=8001:continue
        length,cap=struct.unpack_from('<QQ',raw,0x28)
        if length!=7 or cap!=15 or raw[0x18:0x20]!=b'Scatter\0':raise ValueError('Learned Scatter name differs')
        level=struct.unpack_from('<I',raw,0x48)[0];radius,distance=struct.unpack_from('<II',raw,0x60)
        if not 1<=radius<=distance<=20:raise ValueError('Learned Scatter range is invalid')
        fresh=s.read_block(pointer,0x68)
        if raw[:8]!=fresh[:8] or raw[0x10:]!=fresh[0x10:]:
            raise ValueError('Scatter changed during range observation')
        matches.append({'type_id':8001,'level':level,'range':radius,'distance':distance})
    if len(matches)>1 or (require_scatter and not matches):raise ValueError('Exactly one learned Scatter is required')
    fresh_bow=s.read_block(bow_address,0x74)
    if (fresh_bow[:0x14]!=bow[:0x14] or fresh_bow[0x70:0x74]!=bow[0x70:0x74]
            or s.read_block(actor+layout.bow_offset,8)!=bow_pointer
            or s.read_block(actor+layout.learned_skills_offset,24)!=header
            or (end>start and s.read_block(start,end-start)!=entries)):
        raise ValueError('Combat range identity changed during observation')
    s.assert_identity()
    return {'bow':{'type_id':bow_type,'range':bow_range},'scatter':matches[0] if matches else None,
            'source':'read_only_memory'}


def read_combat_ranges(observer, *, require_scatter=True):
    s=observer.adapter
    if s.expected_sha256!=CLIENT_SHA256:
        raise ValueError('Combat range client differs from the qualified profile')
    life=read_life(s,observer.health_layout,observer.character)
    from conquest.memory_build_layout import read_build_layout
    result=_read_combat_ranges(s,life,read_build_layout(s),require_scatter=require_scatter)
    latest=read_life(s,observer.health_layout,observer.character)
    if latest.object_address!=life.object_address or latest.dead_candidate:
        raise ValueError('Character changed during range observation')
    return result


def read_combat_ranges_for_session(session,character,*,require_scatter=True):
    """Explicit exact-build observation only; it has no dispatch capability."""
    from conquest.memory_build_layout import read_build_layout
    from conquest.memory_life import MemoryLifeReader
    reader=MemoryLifeReader.for_session(session,character);s=reader.session;life=reader.read()
    result=_read_combat_ranges(s,life,read_build_layout(s),require_scatter=require_scatter)
    latest=MemoryLifeReader.for_session(session,character).read()
    if latest.object_address!=life.object_address or latest.dead_candidate:raise ValueError('Character changed during range observation')
    return result


def route_combat_settings(route,ranges):
    """Select existing single-shot behavior when memory proves Scatter absent."""
    scatter=ranges['scatter']
    return {'attack_button':'right' if scatter else 'left',
            'adaptive_scatter':bool(scatter),'single_isolated_targets':True,
            'single_attack_range_tiles':min(route.attack_range_tiles,ranges['bow']['range']),
            'attack_range_tiles':min(route.attack_range_tiles,(scatter or ranges['bow'])['range']),
            'jump_scatter':route.jump_scatter if scatter else False}
