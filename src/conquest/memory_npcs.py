"""Known town vendors identified by bounded, read-only scene observations.

The actor layout is tied to the fingerprint in EntityLayout. NPC type at +0x7c
was observed alongside matching IDs, names and models in the saved town sample.
An NPC observation does not establish that its shop is open or identify items.
"""
from dataclasses import dataclass
import time

from conquest.addressing import checked_address
from conquest.memory_entities import sample_fields


@dataclass(frozen=True)
class VendorIdentity:
    map_id: int
    type_id: int
    name: str
    model: int
    position: tuple[int, int]


TOWN_VENDORS = (
    VendorIdentity(1002, 1, 'Shopkeeper', 10, (415, 351)),
    VendorIdentity(1002, 4, 'Armorer', 40, (412, 366)),
    VendorIdentity(1002, 3, 'Pharmacist', 30, (466, 327)),
    VendorIdentity(1002, 5, 'Blacksmith', 50, (452, 330)),
    VendorIdentity(1002, 0, 'Warehouseman', 80, (409, 351)),
    VendorIdentity(1011, 10014, 'Pharmacist', 230, (189, 252)),
    VendorIdentity(1011, 10013, 'Blacksmith', 220, (197, 226)),
    VendorIdentity(1011, 11, 'Armorer', 116, (202, 242)),
    VendorIdentity(1011, 0, 'Warehouseman', 210, (227, 246)),
    VendorIdentity(1020, 10030, 'Pharmacist', 30, (550, 542)),
    VendorIdentity(1020, 10029, 'Blacksmith', 50, (560, 508)),
    VendorIdentity(1020, 0, 'Warehouseman', 80, (576, 542)),
)


def warehouse_identity(entities,map_id):
    """Discover the warehouse model/name in the current town, then pin its tile."""
    known=next((v for v in TOWN_VENDORS if v.map_id==map_id and v.name=='Warehouseman'),None)
    if known:return known
    if map_id==1036:
        from conquest.market_services import discover
        return discover(entities,map_id,'Warehouseman')[0]
    from conquest.city_travel import city_for
    left,top,right,bottom=city_for(map_id)['town_boundary']
    s,p=entities.session,entities.layout
    base,collection,trace=entities._resolve()
    headers=[(collection+o,'u64') for o in (p.begin_offset,p.end_offset,p.capacity_offset)]
    bounds=sample_fields(s,headers);begin,end,capacity=bounds
    if (not begin<=end<=capacity or (end-begin)%p.entry_stride
            or (capacity-begin)%p.entry_stride or (capacity-begin)//p.entry_stride>p.max_objects):
        raise ValueError('Warehouse NPC scene bounds changed')
    entries=[(a+p.entry_object_offset,'u64') for a in range(begin,end,p.entry_stride)]
    objects=sample_fields(s,entries)
    vtables=sample_fields(s,[(checked_address(a),'u64') for a in objects])
    actors=[a for a,vt in zip(objects,vtables) if vt==base+p.monster_vtable_rva]
    models=sample_fields(s,[(a+0x84,'u32') for a in actors])
    candidates=[a for a,m in zip(actors,models) if m==80]
    if len(candidates)>8:raise ValueError('Ambiguous warehouse model population')
    matches=[]
    for a in candidates:
        fields=[(a+0x7c,'u32'),(a+p.name_offset,'utf8'),(a+p.position_offset,'xy_u32')]
        values=sample_fields(s,fields);kind,name,point=values
        if sample_fields(s,fields)!=values:raise ValueError('Warehouse NPC changed during observation')
        if name=='Warehouseman' and left<=point[0]<=right and top<=point[1]<=bottom:
            matches.append(VendorIdentity(map_id,kind,name,80,tuple(point)))
    if sample_fields(s,headers)!=bounds or sample_fields(s,entries)!=objects:
        raise ValueError('Warehouse scene changed during observation')
    if len(matches)!=1:raise ValueError('One memory-identified Warehouseman is required in the town scene')
    # Full model/name/map/ID/position checks are then made by MemoryNpcReader.
    return matches[0]


@dataclass(frozen=True)
class NpcObservation:
    object_address: int
    entity_id: int
    type_id: int
    name: str
    map_id: int
    position: tuple[int, int]
    draw_position: tuple[int, int]


@dataclass(frozen=True)
class NpcSnapshot:
    started_at: float
    timestamp: float
    npcs: tuple[NpcObservation, ...]


def interaction_point(npc):
    # Market's model-87 Warehouseman has a higher interaction surface than
    # the town vendors. Qualified against an actual Warehouse memory opening.
    market_bank = (npc.map_id == 1036 and npc.type_id == 0
                   and npc.name == 'Warehouseman' and tuple(npc.position) == (182, 180))
    return npc.draw_position[0], npc.draw_position[1] - (64 if market_bank else 32)


class MemoryNpcReader:
    def __init__(self, entities, *, vendors=TOWN_VENDORS, clock=time.monotonic):
        self.entities, self.clock = entities, clock
        self.vendors = {(v.map_id, v.type_id): v for v in vendors}
        if len(self.vendors) != len(vendors):
            raise ValueError('Duplicate vendor identity')

    def read(self, map_id):
        if type(map_id) is not int or map_id <= 0:
            raise ValueError('NPC observations require a current map ID')
        started = self.clock()
        known = {kind:v for (world, kind),v in self.vendors.items() if world == map_id}
        if not known:
            return NpcSnapshot(started, self.clock(), ())
        e = self.entities
        s, p = e.session, e.layout
        base, collection, trace = e._resolve()
        headers = [(collection+offset, 'u64') for offset in
                   (p.begin_offset, p.end_offset, p.capacity_offset)]
        header = sample_fields(s, headers)
        begin, end, capacity = header
        if header != [0, 0, 0]:
            for address in header:
                checked_address(address)
        if (not begin <= end <= capacity or (end-begin) % p.entry_stride
                or (capacity-begin) % p.entry_stride
                or (capacity-begin)//p.entry_stride > p.max_objects):
            raise ValueError('NPC scene vector is invalid')
        entries = [(a+p.entry_object_offset, 'u64') for a in range(begin, end, p.entry_stride)]
        objects = sample_fields(s, entries)
        if len(set(objects)) != len(objects):
            raise ValueError('Duplicate NPC scene objects')
        vtable_fields = [(checked_address(obj), 'u64') for obj in objects]
        vtables = sample_fields(s, vtable_fields)
        actors = [obj for obj, vt in zip(objects, vtables) if vt == base+p.monster_vtable_rva]
        id_fields = [(obj+p.id_offset, 'u32') for obj in actors]
        ids = sample_fields(s, id_fields)
        type_fields = [(obj+0x7c, 'u32') for obj in actors]
        types = sample_fields(s, type_fields)
        # IDs changed after a verified reconnect. Discover the current ID from
        # the NPC type, model, name and saved map/tile, then pin it for actions.
        # This server's Warehouseman has type 0, shared with players. Its
        # model distinguishes the candidate; name/map/tile are checked below.
        zero_actors = [obj for obj,kind in zip(actors,types) if kind==0 and 0 in known]
        zero_models = dict(zip(zero_actors,sample_fields(s,[(obj+0x84,'u32') for obj in zero_actors])))
        candidates = [obj for obj, kind in zip(actors, types) if kind in known
                      and (kind!=0 or zero_models[obj]==known[0].model)]
        if len(candidates) > len(known):
            raise ValueError('Duplicate vendor IDs')
        specs = ((p.id_offset, 'u32'), (0x7c, 'u32'), (p.kind_offset, 'u32'),
                 (0x84, 'u32'), (p.name_offset, 'utf8'),
                 (p.position_offset, 'xy_u32'), (p.draw_position_offset, 'i32'),
                 (p.draw_position_offset+4, 'i32'))
        fields = [(obj+offset, kind) for obj in candidates for offset, kind in specs]
        values = sample_fields(s, fields)
        if sample_fields(s, fields) != values:
            raise ValueError('NPC changed during observation')
        if (sample_fields(s, entries) != objects or sample_fields(s, vtable_fields) != vtables
                or sample_fields(s, id_fields) != ids or sample_fields(s, type_fields) != types
                or sample_fields(s, headers) != header
                or sample_fields(s, [(a, 'u64') for a, _ in trace]) != [v for _, v in trace]
                or sample_fields(s, [(collection, 'u64')]) != [base+p.collection_vtable_rva]):
            raise ValueError('NPC scene changed during observation')
        result = []
        for index, obj in enumerate(candidates):
            uid, kind, species, model, name, position, draw_x, draw_y = values[index*8:index*8+8]
            drawing = (draw_x, draw_y)
            expected = known.get(kind)
            if (expected is None or not uid or species != 0
                    or model != expected.model or name != expected.name
                    or position != expected.position
                    or any(not 0 <= v < 2048 for v in position)
                    or any(not -32768 <= v <= 32767 for v in drawing)):
                raise ValueError('Vendor identity or position differs from its memory profile')
            result.append(NpcObservation(obj, uid, kind, name, map_id, position, drawing))
        if len({v.entity_id for v in result}) != len(result):
            raise ValueError('Duplicate vendor IDs')
        s.assert_identity()
        finished = self.clock()
        if not 0 <= finished-started <= .5:
            raise ValueError('NPC observation expired')
        return NpcSnapshot(started, finished, tuple(result))

    def require(self, map_id, entity_id):
        matches = [npc for npc in self.read(map_id).npcs if npc.entity_id == entity_id]
        if len(matches) != 1:
            raise ValueError('Selected vendor is not present in the current memory scene')
        return matches[0]


def vendor_identity(map_id,role):
    kind={1011:{3:10014,5:10013,4:11},
          1020:{3:10030,5:10029}}.get(map_id,{}).get(role,role)
    matches=[v for v in TOWN_VENDORS if v.map_id==map_id and v.type_id==kind]
    if len(matches)!=1:raise ValueError('Vendor role has not been verified in this city')
    return matches[0]
