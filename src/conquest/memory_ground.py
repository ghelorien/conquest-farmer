"""Bounded ground-item records in the current scene; no image observations."""
from dataclasses import dataclass
import struct
import time

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
    return (drop.type_id in (1088000,1088001)
            or (gear and (drop.type_id % 10 == 9
                          or (type(drop.plus) is int and 1 <= drop.plus <= 12))))


class MemoryGroundReader:
    def __init__(self,entities):
        self.entities=entities

    def read(self):
        started=time.monotonic()
        e=self.entities
        s,p=e.session,e.layout
        base,collection,trace=e._resolve()
        header_fields=[(collection+o,'u64') for o in (p.begin_offset,p.end_offset,p.capacity_offset)]
        header=sample_fields(s,header_fields)
        begin,end,capacity=header
        if not (0<begin<=end<=capacity and (end-begin)%16==0 and (capacity-begin)%16==0
                and (capacity-begin)//16<=p.max_objects):
            raise ValueError('Ground scene vector is invalid')
        checked_address(begin,max(1,end-begin))
        entries=s.read_block(begin,end-begin) if end>begin else b''
        objects=[struct.unpack_from('<Q',entries,i+8)[0] for i in range(0,len(entries),16)]
        if len(objects)!=len(set(objects)):
            raise ValueError('Duplicate scene objects')
        vtables=sample_fields(s,[(checked_address(o),'u64') for o in objects])
        candidates=[o for o,v in zip(objects,vtables) if v==base+0x5cdaf0]
        if len(candidates)>256:
            raise ValueError('Too many ground records')
        result=[]
        for obj in candidates:
            record=s.read_block(obj,0x60)
            x,y=struct.unpack_from('<II',record,0x40)
            uid,type_id=struct.unpack_from('<II',record,0x50)
            if (struct.unpack_from('<Q',record)[0]!=base+0x5cdaf0
                    or not 1<=type_id<=100000000 or not (0<=x<2048 and 0<=y<2048)):
                raise ValueError('Ground item fields changed or are invalid')
            fresh=s.read_block(obj,0x60)
            # Render bookkeeping and animation ticks may change while a drop's
            # identity, type and tile remain stable. Compare gameplay fields.
            if any(fresh[start:end]!=record[start:end] for start,end in ((0,8),(0x40,0x59))):
                raise ValueError('Ground item changed during sampling')
            # The scene retains zero-identifier records alongside qualified
            # drops. Never click these unresolved records, but do not let one
            # suppress every other stable drop in the scene.
            if not uid:
                continue
            # Scene entry points at the shared holder, 0x10 before the actor.
            # Ground-name formatter RVA 0x15e2e3 reads actor+0x48 and appends
            # "(+{:d})" (RVA 0x5cdad8). Thus holder+0x58 is the ground plus.
            plus=record[0x58] if record[0x58]<=12 else None
            result.append(GroundItem(uid,obj,type_id,(x,y),struct.unpack_from('<I',record,0x48)[0],plus))
        if (sample_fields(s,header_fields)!=header
                or (end>begin and s.read_block(begin,end-begin)!=entries)
                or sample_fields(s,[(a,'u64') for a,_ in trace])!=[v for _,v in trace]):
            raise ValueError('Ground scene changed during sampling')
        s.assert_identity()
        if time.monotonic()-started>.5:
            raise ValueError('Ground observation expired')
        # +0x50 is a candidate identifier, not a qualified globally unique ID.
        # The object, creation tick, type and position distinguish live records.
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
