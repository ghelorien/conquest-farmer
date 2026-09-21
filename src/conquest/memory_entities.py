"""Bounded scene observations from read-only memory, with no image dependency.

Collection membership is not evidence that an actor is alive. These candidate
observations deliberately do not authorize combat until current HP is mapped.
"""
from dataclasses import asdict, dataclass
import time
import struct
from typing import Literal, Annotated

from pydantic import BaseModel, ConfigDict, Field

from conquest.addressing import Offset, checked_address


class EntityLayout(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    expected_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    module: str = Field(min_length=1, max_length=128)
    root_rva: Offset
    pointer_offsets: tuple[Offset, ...] = Field(min_length=1, max_length=8)
    collection_vtable_rva: Offset
    begin_offset: Offset
    end_offset: Offset
    capacity_offset: Offset
    entry_stride: Literal[16] = 16
    entry_object_offset: Literal[8] = 8
    monster_vtable_rva: Offset
    monster_kind: int = Field(ge=0, le=255)
    monster_type_ids: tuple[Annotated[int,Field(strict=True,ge=1,le=65535)], ...] = Field(default=(),max_length=1024)
    id_offset: Offset
    kind_offset: Offset
    name_offset: Offset
    position_offset: Offset
    draw_position_offset: Offset
    max_hp_offset: Offset
    level_offset: Offset
    attribute_pointer_offset: Offset = 0x978
    max_objects: int = Field(default=4096, ge=1, le=8192)
    max_monsters: int = Field(default=256, ge=1, le=512)
    max_sample_seconds: float = Field(default=3, gt=0, le=5)
    qualification: Literal["candidate"] = "candidate"


@dataclass(frozen=True)
class MonsterObservation:
    object_address: int
    entity_id: int
    name: str
    position: tuple[int, int]
    draw_position: tuple[int, int]
    max_hp: int
    level: int
    # Neither a live-looking object nor max HP establishes current HP.
    current_hp: None = None
    alive: None = None
    type_id: int = 0


@dataclass(frozen=True)
class EntitySnapshot:
    started_at: float
    timestamp: float
    monsters: tuple[MonsterObservation, ...]
    object_count: int


def sample_fields(session, fields):
    """Preserve numeric tuples and strings; reject incomplete/reordered replies."""
    if not hasattr(session,'request'):
        reader=getattr(session,'read_block',None) or session.read
        formats={'u64':'<Q','u32':'<I','i32':'<i','xy_u32':'<II'}
        values=[]
        for address,kind in fields:
            if kind=='utf8':
                values.append(reader(checked_address(address),64).split(b'\0',1)[0].decode('utf-8',errors='replace'))
            elif kind in formats:
                values.append(struct.unpack(formats[kind],reader(checked_address(address),struct.calcsize(formats[kind]))))
                if kind!='xy_u32':values[-1]=values[-1][0]
            else:raise ValueError('Unsupported direct entity sample kind')
        return values
    values = []
    for start in range(0, len(fields), 64):
        batch = fields[start:start + 64]
        response = session.request("sample", {"fields": [
            {"name": str(i), "address": hex(checked_address(address)), "kind": kind}
            for i, (address, kind) in enumerate(batch)]})
        result = response["fields"]
        if len(result) != len(batch):
            raise ValueError("Incomplete entity sample")
        for i, (field, (address, kind)) in enumerate(zip(result, batch)):
            if field["name"] != str(i) or int(field["address"], 0) != address:
                raise ValueError("Entity sample response does not match request")
            value = field["value"]
            if kind == "utf8":
                if not isinstance(value, str):
                    raise ValueError("Invalid entity name encoding")
            else:
                length = 2 if kind.startswith("xy_") else 1
                if not isinstance(value, list) or len(value) != length or any(type(x) is not int for x in value):
                    raise ValueError("Invalid numeric entity field")
                value = tuple(value) if length == 2 else value[0]
            values.append(value)
    return values


def record_values(session, actors, specs, *, packed=False):
    if not packed:
        return sample_fields(session, [(obj+offset,kind) for obj in actors for offset,kind in specs])
    formats={'u32':'<I','i32':'<i','xy_u32':'<II'}
    sizes={kind:struct.calcsize(fmt) for kind,fmt in formats.items()}
    sizes['utf8']=64
    begin=min(offset for offset,kind in specs)
    end=max(offset+sizes[kind] for offset,kind in specs)
    if not 0<end-begin<=4096:
        raise ValueError('Monster record span exceeds bounded read')
    values=[]
    for obj in actors:
        data=session.read_block(checked_address(obj+begin,end-begin),end-begin)
        if len(data)!=end-begin:raise ValueError('Incomplete monster record')
        for offset,kind in specs:
            relative=offset-begin
            if kind=='utf8':
                value=data[relative:relative+64].split(b'\0',1)[0].decode('utf-8',errors='replace')
            else:
                decoded=struct.unpack_from(formats[kind],data,relative)
                value=decoded if kind=='xy_u32' else decoded[0]
            values.append(value)
    return values


class MemoryEntityReader:
    def __init__(self, session, layout, *, clock=time.monotonic):
        if session.expected_sha256 != layout.expected_sha256:
            raise ValueError("Entity profile fingerprint differs from client")
        self.session, self.layout, self.clock = session, layout, clock

    @classmethod
    def for_session(cls,session,*,clock=time.monotonic):
        from conquest.memory_build_layout import entity_reader_layout
        return cls(session,entity_reader_layout(session),clock=clock)

    def _resolve(self):
        s, p = self.session, self.layout
        s.assert_identity()
        modules = [m for m in s.modules if m["name"].casefold() == p.module.casefold()]
        if len(modules) != 1:
            raise ValueError("Expected exactly one entity module")
        module = modules[0]
        if any(rva + 8 > module["size"] for rva in
               (p.root_rva, p.collection_vtable_rva, p.monster_vtable_rva)):
            raise ValueError("Entity RVA outside loaded module")
        address, trace = checked_address(module["base"] + p.root_rva), []
        for offset in p.pointer_offsets:
            value = sample_fields(s, [(address, "u64")])[0]
            checked_address(value)
            trace.append((address, value))
            address = checked_address(value + offset)
        if sample_fields(s, [(address, "u64")])[0] != module["base"] + p.collection_vtable_rva:
            raise ValueError("Scene collection type changed")
        if sample_fields(s, [(a, "u64") for a, _ in trace]) != [v for _, v in trace]:
            raise ValueError("Scene pointer path changed")
        return module["base"], address, trace

    def read(self, *, selected=None, packed=False):
        if selected is not None:
            uid,address=selected
            if type(uid) is not int or uid<=0:raise ValueError("Invalid selected monster ID")
            checked_address(address)
        started = self.clock()
        s, p = self.session, self.layout
        module, collection, trace = self._resolve()
        header_fields = [(collection + offset, "u64") for offset in
                         (p.begin_offset, p.end_offset, p.capacity_offset)]
        header = sample_fields(s, header_fields)
        begin, end, capacity = header
        if header != [0, 0, 0]:
            checked_address(begin)
            checked_address(end)
            checked_address(capacity)
        if (not begin <= end <= capacity or (end - begin) % p.entry_stride
                or (capacity - begin) % p.entry_stride
                or (capacity - begin) // p.entry_stride > p.max_objects):
            raise ValueError("Scene vector bounds are invalid")
        entries = [(a + p.entry_object_offset, "u64") for a in range(begin, end, p.entry_stride)]
        objects = sample_fields(s, entries)
        if len(set(objects)) != len(objects):
            raise ValueError("Duplicate scene object pointers")
        if selected is not None and objects.count(selected[1])!=1:
            raise ValueError('Selected monster is not a unique scene member')
        inspected=objects if selected is None else [selected[1]]
        vtable_fields = [(checked_address(obj), "u64") for obj in inspected]
        vtables = sample_fields(s, vtable_fields)
        typed = [obj for obj, vtable in zip(inspected, vtables) if vtable == module + p.monster_vtable_rva]
        kind_fields = [(obj + p.kind_offset, "u32") for obj in typed]
        kinds = sample_fields(s, kind_fields)
        accepted_types = set(p.monster_type_ids or (p.monster_kind,))
        actors = [obj for obj, kind in zip(typed, kinds) if kind in accepted_types]
        if len(actors) > p.max_monsters:
            raise ValueError("Scene exceeds bounded monster count")
        specs = [(p.id_offset, "u32"), (p.kind_offset, "u32"), (p.name_offset, "utf8"),
                 (p.position_offset, "xy_u32"), (p.draw_position_offset, "i32"),
                 (p.draw_position_offset + 4, "i32"), (p.max_hp_offset, "u32"), (p.level_offset, "u32")]
        values = record_values(s, actors, specs, packed=packed)
        # Re-read complete records: moving/recycled entities cannot yield mixed
        # IDs and coordinates. The caller may retry on the next observation.
        if record_values(s, actors, specs, packed=packed) != values:
            raise ValueError("Monster changed during observation")
        if (sample_fields(s, entries) != objects or sample_fields(s, vtable_fields) != vtables
                or sample_fields(s, kind_fields) != kinds
                or sample_fields(s, header_fields) != header
                or sample_fields(s, [(a, "u64") for a, _ in trace]) != [v for _, v in trace]
                or sample_fields(s, [(collection, "u64")])[0] != module + p.collection_vtable_rva):
            raise ValueError("Scene collection changed during observation")
        s.assert_identity()
        finished = self.clock()
        if not 0 <= finished - started <= p.max_sample_seconds:
            raise ValueError("Entity observation expired")
        monsters = []
        for i, obj in enumerate(actors):
            uid, kind, name, position, draw_x, draw_y, max_hp, level = values[i * 8:i * 8 + 8]
            if kind not in accepted_types:
                continue  # Cleared actor storage and other actor kinds are not monsters.
            if (not uid or not name or not name.isprintable() or len(name) > 63
                    or any(not 0 <= coordinate <= 65535 for coordinate in position)
                    or not 1 <= max_hp <= 100000000 or not 1 <= level <= 255):
                raise ValueError("Monster identity or attributes are invalid")
            monsters.append(MonsterObservation(obj, uid, name, position, (draw_x, draw_y), max_hp, level,type_id=kind))
        if len({m.entity_id for m in monsters}) != len(monsters):
            raise ValueError("Duplicate monster IDs")
        if selected is not None and (len(monsters)!=1 or monsters[0].entity_id!=selected[0]):
            raise ValueError('Selected monster identity changed')
        return EntitySnapshot(started, finished, tuple(monsters), len(objects))

    def report(self):
        snapshot = self.read()
        return {"schema_version": 1, "stage": "memory_entity_candidate_sample",
                "source": "read_only_memory", "qualified": False,
                "autonomous_actions_enabled": False, "process_identity": self.session.identity,
                "snapshot": asdict(snapshot)}
