"""Bounded ground-item records in the current scene; no image observations."""
from dataclasses import dataclass
import struct
import time

from conquest.valuables import SPECIAL_LOOT_TYPES
from conquest.addressing import checked_address
from conquest.memory_entities import sample_fields

MONEY_TYPES=frozenset((1090000,1090010,1090020,1091000,1091010,1091020))


@dataclass(frozen=True)
class GroundItem:
    uid: int
    object_address: int
    type_id: int
    position: tuple[int,int]
    spawn_tick: int = 0
    plus: int | None = None

    @property
    def silver(self):
        return self.type_id in MONEY_TYPES


def wanted_drop(drop):
    """User's ground allowlist; unknown enhancement never authorizes pickup."""
    gear = 100000 <= drop.type_id < 600000
    return (drop.type_id in SPECIAL_LOOT_TYPES
            or (gear and (drop.type_id % 10 == 9
                          or (type(drop.plus) is int and 1 <= drop.plus <= 12))))


class MemoryGroundReader:
    def __init__(self,entities,*,layout=None):
        from conquest.memory_build_layout import read_build_layout
        if layout is None:
            layout=read_build_layout(entities.session)
        if entities.session.expected_sha256 != layout.expected_sha256:
            raise ValueError('Ground layout differs from client')
        self.entities=entities
        self.layout=layout

    @classmethod
    def for_session(cls,session):
        from conquest.memory_build_layout import read_build_layout
        from conquest.memory_entities import MemoryEntityReader
        return cls(MemoryEntityReader.for_session(session),layout=read_build_layout(session))

    def read(self):
        started=time.monotonic()
        e=self.entities
        s,p=e.session,e.layout
        layout=self.layout
        base,_,trace=e._resolve()
        # World singleton 96fc5 -> 699360; drop manager at world+170.
        # 145200/145388 store UID, type, tile and actor/holder in its records.
        # The scene actor's +40 (holder+50) is a RENDER COUNTER, not a UID.
        registry=(layout.ground_registry_rva if layout is not None else 0x6994d8)
        holder_vtable=(layout.ground_holder_vtable_rva if layout is not None else 0x5ccc08)
        actor_vtable=(layout.ground_actor_vtable_rva if layout is not None else 0x5cdaf0)
        header_fields=[(base+registry+o,'u64') for o in (0,8,16)]
        header=sample_fields(s,header_fields)
        begin,end,capacity=header
        if not (0<=begin<=end<=capacity and (begin>0 or end==capacity==0)
                and (end-begin)%16==0 and (capacity-begin)%16==0
                and (capacity-begin)//16<=p.max_objects):
            raise ValueError('Ground registry vector is invalid')
        if (end-begin)//16>256:
            raise ValueError('Too many ground records')
        if begin:checked_address(begin,max(1,end-begin))
        entries=s.read_block(begin,end-begin) if end>begin else b''
        result=[];seen_uids=set();seen_objects=set();record_checks=[]
        for at in range(0,len(entries),16):
            address,owner=struct.unpack_from('<QQ',entries,at)
            checked_address(owner,0x30)
            if address!=owner+0x10 or sample_fields(s,[(owner,'u64')])[0]!=base+holder_vtable:
                raise ValueError('Ground registry record ownership changed')
            data=s.read_block(checked_address(address,0x20),0x20)
            uid,type_id,x,y,actor,obj=struct.unpack('<4I2Q',data)
            if (not uid or uid in seen_uids or not 0<=type_id<=100000000
                    or not (0<=x<2048 and 0<=y<2048)):
                raise ValueError('Ground registry identity is invalid or duplicated')
            seen_uids.add(uid);record_checks.append((address,data,owner))
            # The manager also permits type-zero entries with no item actor.
            if not type_id and not actor and not obj:continue
            checked_address(obj,0x60)
            if actor!=obj+0x10 or obj in seen_objects:
                raise ValueError('Ground actor ownership changed or duplicated')
            seen_objects.add(obj)
            record=s.read_block(obj,0x60)
            if (struct.unpack_from('<Q',record)[0]!=base+actor_vtable
                    or struct.unpack_from('<II',record,0x40)!=(x,y)
                    or struct.unpack_from('<I',record,0x54)[0]!=type_id):
                raise ValueError('Ground item fields changed or are invalid')
            fresh=s.read_block(obj,0x60)
            # Reference counts and the animation counter may change independently
            # of item identity. Preserve tile, creation time, type and plus checks.
            if any(fresh[a:b]!=record[a:b] for a,b in ((0,8),(0x40,0x50),(0x54,0x5a))):
                raise ValueError('Ground item changed during sampling')
            # Scene entry points at the shared holder, 0x10 before the actor.
            # Ground-name formatter RVA 0x15e2e3 reads actor+0x48 and appends
            # "(+{:d})" (RVA 0x5cdad8). Thus holder+0x58 is the ground plus.
            plus=record[0x58] if record[0x58]<=12 else None
            result.append(GroundItem(uid,obj,type_id,(x,y),struct.unpack_from('<Q',record,0x48)[0],plus))
        if any(s.read_block(address,0x20)!=data or sample_fields(s,[(owner,'u64')])[0]!=base+holder_vtable
               for address,data,owner in record_checks):
            raise ValueError('Ground registry item changed during sampling')
        if (sample_fields(s,header_fields)!=header
                or (end>begin and s.read_block(begin,end-begin)!=entries)
                or sample_fields(s,[(a,'u64') for a,_ in trace])!=[v for _,v in trace]):
            raise ValueError('Ground scene changed during sampling')
        s.assert_identity()
        if time.monotonic()-started>.5:
            raise ValueError('Ground observation expired')
        return tuple(result)


def pickup_delta(drop,before,after):
    if drop.silver:
        return after.silver-before.silver
    previous={i.uid:i for i in before.items if i.type_id==drop.type_id}
    current={i.uid:i for i in after.items if i.type_id==drop.type_id}
    added=set(current)-set(previous)
    if added:
        return len(added)  # Equipment amount is durability, not item quantity.
    return sum(max(0,item.amount-previous[uid].amount) for uid,item in current.items() if uid in previous)
