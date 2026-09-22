"""Bounded, read-only evidence for an owned 1078 Market booth.

This is deliberately *not* a target selector.  In particular, the 1074
graphics vtable, camera and hit-test instructions cannot authorize a 1078
click.  The authenticated merchant observation endpoint uses this to pin the
scene actor and collect only the native methods needed for a later, separately
reviewed input qualification.
"""
import struct
import time
from types import SimpleNamespace

from conquest.addressing import checked_address
from conquest.memory_build_layout import (
    CLIENT_SHA256_1078, entity_reader_layout, health_reader_layout,
)
from conquest.memory_entities import MemoryEntityReader
from conquest.merchants.stalls import owned_booth


def _read(session, address, fmt):
    return struct.unpack(fmt, session.read(checked_address(address), struct.calcsize(fmt)))


def _module_rva(value, module, *, minimum=0):
    base, size = module['base'], module['size']
    if not base + minimum <= value < base + size:
        return None
    return value - base


def _native_method(session, address, module):
    """Read only one module-vtable method and a bounded instruction prefix."""
    vtable = _read(session, address, '<Q')[0]
    if _module_rva(vtable, module) is None:
        return None
    method = _read(session, vtable + 0x40, '<Q')[0]
    rva = _module_rva(method, module, minimum=0x1000)
    if rva is None or rva + 48 > module['size']:
        return None
    first = session.read(method, 48)
    if session.read(method, 48) != first:
        raise ValueError('Booth graphics method changed during read-only observation')
    return {'graphics_vtable_rva': _module_rva(vtable, module),
            'method_slot': 0x40, 'method_rva': rva,
            'method_prefix_hex': first.hex()}


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
    adapter = SimpleNamespace(expected_sha256=session.expected_sha256,
        modules=session.modules, read=session.read, read_block=session.read,
        assert_identity=session.assert_identity)
    observer = SimpleNamespace(adapter=adapter, character=snapshot['character'],
        health_layout=health_reader_layout(session),
        entities=MemoryEntityReader(session, layout))
    booth = owned_booth(observer, snapshot)
    address = booth['address']
    if (_read(session, address, '<Q')[0] != module['base'] + layout.monster_vtable_rva
            or _read(session, address + layout.id_offset, '<I')[0] != snapshot['own_booth_uid']):
        raise ValueError('1078 owned booth scene actor changed')
    actor_vtable = _read(session, address + 0x10, '<Q')[0]
    accessor = (_read(session, actor_vtable + 0x28, '<Q')[0]
                if _module_rva(actor_vtable, module) is not None else 0)
    candidates = []
    for offset in (0x2f8, 0x300):
        graphics = _read(session, address + offset, '<Q')[0]
        if not 0x10000 <= graphics <= 0x7fffffffffff:
            continue
        try:
            method = _native_method(session, graphics, module)
            orientation = _read(session, graphics + 0xc, '<i')[0]
        except OSError:
            continue
        if method is not None and 0 <= orientation < 8:
            candidates.append({'actor_offset': offset, 'orientation_candidate': orientation,
                               **method})
    if owned_booth(observer, snapshot) != booth:
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
