"""Read the inspected monster attribute table without modifying the client.

The decoded value remains a candidate until damage/death transitions have been
validated. Zero excludes a target; a positive value alone does not prove life.
"""
import struct
import time

from conquest.addressing import checked_address
from conquest.memory_health import decode_attribute


def read_monster_health(session, layout, monster):
    started=time.monotonic()
    session.assert_identity()
    module=next(m for m in session.modules if m['name'].casefold()==layout.module.casefold())
    obj=checked_address(monster.object_address,0x988)
    record=session.read_block(obj,0x988)
    if (struct.unpack_from('<Q',record)[0]!=module['base']+layout.monster_vtable_rva
            or struct.unpack_from('<I',record,layout.id_offset)[0]!=monster.entity_id
            or struct.unpack_from('<I',record,layout.kind_offset)[0]!=monster.type_id
            or struct.unpack_from('<II',record,layout.position_offset)!=tuple(monster.position)):
        raise ValueError('Monster changed before health observation')
    pointer_bytes=record[0x978:0x980]
    pointer=checked_address(struct.unpack('<Q',pointer_bytes)[0],24)
    header=session.read_block(pointer,24)
    mode,count=struct.unpack_from('<II',header,8)
    if mode not in range(4) or not 2<=count<=1024:
        raise ValueError('Monster attribute table is unsupported')
    table_pointer=checked_address(struct.unpack_from('<Q',header,16)[0],count*4)
    table=session.read_block(table_pointer,count*4)
    hp=decode_attribute(table,mode,count,1)
    maximum=struct.unpack_from('<I',record,layout.max_hp_offset)[0]
    if maximum!=monster.max_hp or not 0<=hp<=maximum:
        raise ValueError('Monster health is outside bounds')
    if (session.read_block(table_pointer,count*4)!=table
            or session.read_block(pointer,24)!=header
            or session.read_block(obj+0x978,8)!=pointer_bytes
            or session.read_block(obj,8)!=record[:8]
            or session.read_block(obj+layout.id_offset,16)!=record[layout.id_offset:layout.id_offset+16]
            # Camera scrolling changes draw coordinates without changing this
            # actor's health. Attack dispatch separately revalidates projection;
            # the HP read needs stable identity, world position and attributes.
            or session.read_block(obj+layout.position_offset,8)!=record[layout.position_offset:layout.position_offset+8]
            or session.read_block(obj+layout.max_hp_offset,4)!=record[layout.max_hp_offset:layout.max_hp_offset+4]):
        raise ValueError('Monster changed during health observation')
    session.assert_identity()
    if time.monotonic()-started>.35:
        raise ValueError('Monster health observation expired')
    return hp
