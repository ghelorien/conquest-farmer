"""Bounded, read-only evidence for an owned 1078 Market booth.

This is deliberately *not* a target selector.  In particular, the 1074
graphics vtable, camera and hit-test instructions cannot authorize a 1078
click.  The authenticated merchant observation endpoint uses this to pin the
scene actor and collect only the native methods needed for a later, separately
reviewed input qualification.
"""
import math
import struct
import time
from conquest.addressing import checked_address
from conquest.memory_build_layout import CLIENT_SHA256_1078, entity_reader_layout
from conquest.memory_entities import MemoryEntityReader, sample_fields
from conquest.merchants.memory import GuiReader
from conquest.viewport import clear_scene


# These are exact 1078 instructions, not the similar 1074 booth projection.
# The client camera getter returns module+0x6b9b40.  The tile conversion uses
# 32/16 pixel isometric axes and its inverse reads the two doubles below.
_PROJECTION_CODE = {
    0x982b5: '488d0584186200',
    0x13fe90: '8bc24103d0412bc0c1e204c1e005034144418901488b4424280351488910c3',
    0x13fe10: ('40534883ec40442b4148498bd92b51440f297424300f297c2420'
               '66410f6ef0f30fe6f6660f6efaf20f5935010a4b00f30fe6ff0f28c6'
               'f20f593dda094b00f20f58c7e829142800f20f2cc0f20f5cf78903'
               '0f28c6e817142800488b4424700f287424300f287c2420f20f2cc8'
               '89084883c4405bc3'),
    0x3c1280: ('66480f7ec0488bd048c1ea344881e2ff0700004881fa33040000731d'
               '4881faff030000721a48c7c133040000482bca48d3e84883d00048d3e0'
               '66480f6ec0c3'),
    0x1abfa4: '4183beb00200000e7571498b06488d542470498bceff5028',
    0x1abf45: ('8b8b78020000990faf8b6c0200002bc2d1f82bf7448bc8c1e608'
               'b81f85eb51412be9c1e508f7e98bc6448bc29941c1f805418bc8'
               'c1e91f4403c141f7f88d3438'),
    0x1abfc4: ('e877c2eeff4c8bf84c8d4c2460498bcf448b40508b504c4403c7'
               '488d44246403d64889442420e8213ef9'),
    0x18a770: '488b81d8000000488902c3',
    0x274cd0: ('40535556574863490c4c8d1da05c36008b742448488d2d995c3600'
               '33c08bda4c8d148949c1e2034d03da660f1f440000418b14c34103'
               'd13bd3750e498d0c2a8b14c103d6413bd0740effc083f8057ce0'
               '33c05f5e5d5bc3b8010000005f5e5d5b'),
}


def _projection_candidate(session, booth, module, actor_vtable, accessor, candidates):
    """Return arithmetic evidence only; never authorize a booth-open click."""
    base = module['base']
    for rva, expected_hex in _PROJECTION_CODE.items():
        expected = bytes.fromhex(expected_hex)
        if session.read(base + rva, len(expected)) != expected:
            raise ValueError('1078 native booth projection or hit-test code changed')
    if (_read(session, base + 0x5f0828, '<d')[0] != 1 / 64
            or _read(session, base + 0x5f0840, '<d')[0] != 1 / 32
            or actor_vtable != base + 0x5eb2d0
            or accessor != base + 0x18a770):
        raise ValueError('1078 native booth conversion or accessor differs')
    selected = [c for c in candidates
                if (c['actor_offset'] == 0x308
                    and c['graphics_vtable_rva'] == 0x5f04d0
                    and c['orientation_candidate'] == 6
                    and any(m['slot'] == 0x40 and m['method_rva'] == 0x274cd0
                            for m in c['methods']))]
    if len(selected) != 1:
        raise ValueError('1078 owned booth graphics are not uniquely pinned')
    # The pinned native hit-test runs with this = actor + 0x10. Its
    # [this + 0x2b0] kind is therefore actor + 0x2c0.
    if _read(session, booth['address'] + 0x2c0, '<I')[0] != 14:
        raise ValueError('1078 owned booth actor has a different hit-test kind')
    offsets = _read(session, base + 0x5daa70, '<10i')
    footprint = tuple(zip(offsets[::2], offsets[1::2]))
    if footprint != ((0, 1), (0, 0), (0, 0), (0, 0), (0, -1)):
        raise ValueError('1078 booth hit-test footprint changed')
    camera = base + 0x6b9b40
    camera_fields = tuple((camera + offset, session.read(camera + offset, size))
                          for offset, size in ((0x30, 8), (0x44, 16), (0x26c, 16)))
    map_width, map_height = struct.unpack('<2i', camera_fields[0][1])
    origin_x, origin_y, scroll_x, scroll_y = struct.unpack('<4i', camera_fields[1][1])
    zoom, width, height, percent = struct.unpack('<4i', camera_fields[2][1])
    viewport = tuple(GuiReader.for_session(session).viewport_size())
    scale_numerator = zoom * percent // 100
    x, y = booth['position']
    if (not 0 < x < map_width <= 4096 or not 0 < y < map_height <= 4096
            or (width, height) != viewport or not 640 <= width <= 7680
            or not 480 <= height <= 4320 or not 64 <= scale_numerator <= 1024):
        raise ValueError('1078 booth camera or viewport is outside supported bounds')
    draw = ((x - y) * 32 + origin_x - scroll_x,
            (x + y) * 16 + origin_y - scroll_y)
    if any(abs(a - b) > 1 for a, b in zip(draw, booth['draw_position'])):
        raise ValueError('1078 booth draw position differs from native tile projection')
    scale = scale_numerator / 256
    candidate = tuple(round((value - dimension // 2) * scale + dimension // 2)
                      for value, dimension in zip(draw, viewport))
    if not clear_scene(candidate, viewport):
        raise ValueError('1078 booth projection overlaps reserved screen controls')
    unscaled = tuple(math.trunc((value - dimension // 2) / scale) + dimension // 2
                     for value, dimension in zip(candidate, viewport))
    local_x = unscaled[0] + scroll_x - origin_x
    local_y = unscaled[1] + scroll_y - origin_y
    tile = (math.floor(local_y / 32 + local_x / 64 + .5),
            math.floor(local_y / 32 - local_x / 64 + .5))
    if tile != (x, y):
        raise ValueError('1078 booth candidate does not round-trip through native tile math')
    if (any(session.read(address, len(raw)) != raw for address, raw in camera_fields)
            or tuple(GuiReader.for_session(session).viewport_size()) != viewport):
        raise ValueError('1078 booth camera changed during projection')
    session.assert_identity()
    return {'candidate_point': list(candidate), 'roundtrip_tile': list(tile),
            'viewport': list(viewport), 'native_footprint': [list(v) for v in footprint],
            'camera_rva': 0x6b9b40, 'zoom_numerator': scale_numerator,
            'candidate_only': True, 'input_qualified': False}


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
    projection = _projection_candidate(
        session, booth, module, actor_vtable, accessor, candidates)
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
            'blocker': '1078 booth candidate has no live open-click outcome or input qualification',
            'owned_booth_uid': booth['uid'], 'actor_address': hex(address),
            'actor_vtable_rva': _module_rva(actor_vtable, module),
            'position_accessor_rva': _module_rva(accessor, module, minimum=0x1000),
            'scene_position': booth['position'],
            'draw_position': booth['draw_position'],
            'graphics_candidates': candidates,
            'projection_candidate': projection}
