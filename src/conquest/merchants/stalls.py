"""Read-only scene identities used to qualify vacant Market flags."""
import time
from conquest.memory_entities import sample_fields


def scene_flags(observer):
    from conquest.memory_life import read_life
    life=read_life(observer.adapter,observer.health_layout,observer.character)
    if life.map_id!=1036 or life.dead_candidate:
        raise ValueError('Shop flags require a living merchant in Market')
    s,p=observer.adapter,observer.entities.layout
    base,collection,trace=observer.entities._resolve()
    headers=[(collection+o,'u64') for o in (p.begin_offset,p.end_offset,p.capacity_offset)]
    def members():
        begin,end,capacity=sample_fields(s,headers)
        if not (begin<=end<=capacity and (end-begin)%p.entry_stride==0
                and (capacity-begin)%p.entry_stride==0 and (capacity-begin)//p.entry_stride<=p.max_objects):
            raise ValueError('Shop flag scene bounds changed')
        return sample_fields(s,[(a+p.entry_object_offset,'u64') for a in range(begin,end,p.entry_stride)])
    started=time.monotonic();objects=members()
    vtables=sample_fields(s,[(a,'u64') for a in objects])
    result=[]
    for a,vtable in zip(objects,vtables):
        if vtable!=base+p.monster_vtable_rva:continue
        fields=[(a+0x7c,'u32'),(a+0x84,'u32'),(a+p.name_offset,'utf8'),
                (a+p.position_offset,'xy_u32'),(a+p.id_offset,'u32'),(a+p.kind_offset,'u32'),
                (a+p.draw_position_offset,'i32'),(a+p.draw_position_offset+4,'i32')]
        values=sample_fields(s,fields)
        kind,model,name,position,uid,species,dx,dy=values
        if name!='ShopFlag':continue
        if not uid or species!=0 or any(not 0<=v<2048 for v in position):
            raise ValueError('Shop flag identity is invalid')
        if sample_fields(s,fields)!=values or members().count(a)!=1:
            raise ValueError('Shop flag changed while reading')
        result.append({'address':a,'uid':uid,'type_id':kind,'model':model,'name':name,
                       'position':list(position),'draw_position':[dx,dy]})
    if sample_fields(s,[(a,'u64') for a,_ in trace])!=[v for _,v in trace] or time.monotonic()-started>2:
        raise ValueError('Shop flag observation expired')
    s.assert_identity()
    return result


def vacant_flags(observer, spec):
    """The vacancy field must come from a separately live-qualified profile."""
    offset=spec.get('vacancy_offset');mask=spec.get('vacancy_mask');value=spec.get('vacancy_value')
    if (type(offset) is not int or not 0<=offset<=0x4000 or offset%4
            or type(mask) is not int or not 0<mask<=0xffffffff
            or type(value) is not int or not 0<=value<=mask):
        raise ValueError('Stall occupancy memory layout needs live qualification')
    from conquest.merchants.memory import unpack
    flags=scene_flags(observer);available=[]
    for flag in flags:
        if (flag['type_id'],flag['model'])!=(0,1086):continue
        address=flag['address']+offset
        before=unpack(observer.adapter,address,'<I')[0]
        if before&mask!=value:continue
        current=next((r for r in scene_flags(observer) if r['uid']==flag['uid']),None)
        if current!=flag or unpack(observer.adapter,address,'<I')[0]!=before:
            raise ValueError('Stall availability changed during observation')
        available.append(flag)
    return available
