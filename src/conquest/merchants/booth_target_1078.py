"""Bounded, read-only evidence for an owned 1078 Market booth.

This is deliberately *not* a target selector.  In particular, the 1074
graphics vtable, camera and hit-test instructions cannot authorize a 1078
click.  The authenticated merchant observation endpoint uses this to pin the
scene actor and collect only the native methods needed for a later, separately
reviewed input qualification.
"""
import struct
import time
from conquest.addressing import checked_address
from conquest.memory_build_layout import CLIENT_SHA256_1078, entity_reader_layout
from conquest.memory_entities import MemoryEntityReader, sample_fields


def _read(session, address, fmt):
    return struct.unpack(fmt, session.read(checked_address(address), struct.calcsize(fmt)))


def _module_rva(value, module, *, minimum=0):
    base, size = module['base'], module['size']
    if not base + minimum <= value < base + size:
        return None
    return value - base


def _graphics_methods(session, address, module):
    """Inspect three fixed vtable slots, never a caller-supplied address."""
    vtable = _read(session, address, '<Q')[0]
    vtable_rva = _module_rva(vtable, module)
    if vtable_rva is None or vtable_rva + 0x48 > module['size']:
        return None
    methods = []
    for slot in (0x20, 0x28, 0x40):
        method = _read(session, vtable + slot, '<Q')[0]
        rva = _module_rva(method, module, minimum=0x1000)
        if rva is None or rva + 24 > module['size']:
            continue
        prefix = session.read(method, 24)
        if (session.read(method, 24) != prefix
                or _read(session, vtable + slot, '<Q')[0] != method):
            raise ValueError('Booth graphics method changed during read-only observation')
        methods.append({'slot': slot, 'method_rva': rva,
                        'method_prefix_hex': prefix.hex()})
    if not methods:
        return None
    return {'graphics_vtable_rva': vtable_rva, 'methods': methods}


def _owned_scene_actor(session, entities, snapshot, module):
    """Enumerate this exact 1078 scene without the 1074 life reader."""
    p = entities.layout
    base, collection, trace = entities._resolve()
    if base != module['base']:
        raise ValueError('1078 scene module differs from selected client')

    def members():
        begin, end, capacity = sample_fields(session, [
            (collection + offset, 'u64') for offset in
            (p.begin_offset, p.end_offset, p.capacity_offset)])
        if (not begin <= end <= capacity or (end - begin) % p.entry_stride
                or (capacity - begin) % p.entry_stride
                or (capacity - begin) // p.entry_stride > p.max_objects):
            raise ValueError('1078 scene membership bounds changed')
        objects = sample_fields(session, [
            (address + p.entry_object_offset, 'u64')
            for address in range(begin, end, p.entry_stride)])
        if len(objects) != len(set(objects)) or any(a < 0x10000 for a in objects):
            raise ValueError('1078 scene membership is ambiguous')
        return objects

    objects = members()
    vtables = sample_fields(session, [(a, 'u64') for a in objects])
    typed = [a for a, vtable in zip(objects, vtables)
             if vtable == base + p.monster_vtable_rva]
    ids = sample_fields(session, [(a + p.id_offset, 'u32') for a in typed])
    matches = [a for a, uid in zip(typed, ids)
               if uid == snapshot['own_booth_uid']]
    if len(matches) != 1:
        raise ValueError('Expected exactly one 1078 owned booth scene actor')
    address = matches[0]
    fields = [(address, 'u64'), (address + p.id_offset, 'u32'),
              (address + 0x7c, 'u32'), (address + 0x84, 'u32'),
              (address + p.kind_offset, 'u32'),
              (address + p.name_offset, 'utf8'),
              (address + p.position_offset, 'xy_u32'),
              (address + p.draw_position_offset, 'i32'),
              (address + p.draw_position_offset + 4, 'i32')]
    values = sample_fields(session, fields)
    vtable, uid, type_id, model, species, name, position, dx, dy = values
    if (vtable != base + p.monster_vtable_rva
            or uid != snapshot['own_booth_uid'] or type_id != 0
            or model != 406 or species != 0
            or name != snapshot['character']
            or any(not 0 <= v < 2048 for v in position)
            or max(abs(a - b) for a, b in zip(position, snapshot['position'])) > 8
            or any(abs(v) > 32768 for v in (dx, dy))):
        raise ValueError('1078 owned booth actor identity or location changed')
    if (sample_fields(session, fields) != values or members() != objects
            or sample_fields(session, [(a, 'u64') for a, _ in trace])
            != [v for _, v in trace]):
        raise ValueError('1078 owned booth scene changed during observation')
    return {'address': address, 'uid': uid, 'position': list(position),
            'draw_position': [dx, dy]}


def collect(session, snapshot):
    """Return scene/method evidence, never a point or permission to press it."""
    started = time.monotonic()
    if session.expected_sha256 != CLIENT_SHA256_1078:
        raise ValueError('Booth target preflight requires the exact 1078 executable')
    if (snapshot['map_id'] != 1036 or snapshot['hp'] <= 0
            or snapshot['trade'] is not None or snapshot['request'] is not None
            or not snapshot['own_booth_uid']):
        raise ValueError('Booth target preflight requires a living, idle Market owner')
    modules = [m for m in session.modules if m['name'].casefold() == 'imconquer.exe']
    if len(modules) != 1 or modules[0]['size'] < 0x6b9b58:
        raise ValueError('1078 booth module is missing or truncated')
    module = modules[0]
    layout = entity_reader_layout(session)
    entities = MemoryEntityReader(session, layout)
    booth = _owned_scene_actor(session, entities, snapshot, module)
    address = booth['address']
    if (_read(session, address, '<Q')[0] != module['base'] + layout.monster_vtable_rva
            or _read(session, address + layout.id_offset, '<I')[0] != snapshot['own_booth_uid']):
        raise ValueError('1078 owned booth scene actor changed')
    actor_vtable = _read(session, address + 0x10, '<Q')[0]
    accessor = (_read(session, actor_vtable + 0x28, '<Q')[0]
                if _module_rva(actor_vtable, module) is not None else 0)
    offsets = tuple(range(0x2d0, 0x351, 8))
    pointers = tuple(_read(session, address + offset, '<Q')[0] for offset in offsets)
    candidates = []
    for offset, graphics in zip(offsets, pointers):
        if not 0x10000 <= graphics <= 0x7fffffffffff:
            continue
        try:
            method = _graphics_methods(session, graphics, module)
            orientation = _read(session, graphics + 0xc, '<i')[0]
        except OSError:
            continue
        if method is not None:
            candidates.append({'actor_offset': offset,
                'orientation_candidate': orientation if 0 <= orientation < 8 else None,
                **method})
    if tuple(_read(session, address + offset, '<Q')[0] for offset in offsets) != pointers:
        raise ValueError('1078 booth actor pointer fields changed during observation')
    if _owned_scene_actor(session, entities, snapshot, module) != booth:
        raise ValueError('1078 owned booth scene membership changed')
    if (_read(session, address + 0x10, '<Q')[0] != actor_vtable
            or (_module_rva(actor_vtable, module) is not None
                and _read(session, actor_vtable + 0x28, '<Q')[0] != accessor)):
        raise ValueError('1078 booth actor accessor changed')
    session.assert_identity()
    if time.monotonic() - started > 2:
        raise ValueError('1078 booth target preflight expired')
    return {'read_only': True, 'input_qualified': False,
            'target_point': None, 'native_hit_test_qualified': False,
            'blocker': '1078 booth hit test and projection are not yet live-qualified',
            'owned_booth_uid': booth['uid'], 'actor_address': hex(address),
            'actor_vtable_rva': _module_rva(actor_vtable, module),
            'position_accessor_rva': _module_rva(accessor, module, minimum=0x1000),
            'scene_position': booth['position'],
            'draw_position': booth['draw_position'],
            'graphics_candidates': candidates}
