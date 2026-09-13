"""Read the native owned-booth tile hit test; never invokes client code."""
import math
import struct
import time

from conquest.addressing import checked_address
from conquest.memory_life import CLIENT_SHA256


CONTROL = {'mode': 'native_booth_tiles', 'revision': 1}
CODE = {
    0x262165: '4863490c4c8d1d00d535008b742448488d2df9d43500',
    0x262190: '418b14c34103d13bd3750e498d0c2a8b14c103d6413bd0740effc083f8057ce033c05f5e5d5bc3b8010000005f5e5d5b',
    0x19fd44: '4183beb00200000e',
    0x181a20: '488b81d8000000488902c3',
    0x96fc5: '488d0594236000',
    0x139986: '442b4148498bd92b5144',
    0x1399a7: 'f20f593591b34900',
    0x1399b6: 'f20f593d6ab34900',
    0x19fce5: '8b8b78020000990faf8b6c020000',
}


def owned_booth_target(observer, booth):
    """Select a tile accepted by the booth, with a stable camera and identity."""
    s = observer.adapter
    started = time.monotonic()
    if s.expected_sha256 != CLIENT_SHA256:
        raise ValueError('Booth hit test belongs to an unqualified client')
    modules = [m for m in s.modules if m['name'].casefold() == 'imconquer.exe']
    if len(modules) != 1 or modules[0]['size'] < 0x699600:
        raise ValueError('Booth hit-test module is invalid')
    base = modules[0]['base']
    s.assert_identity()
    samples = []

    def read(address, fmt):
        checked_address(address)
        raw = s.read_block(address, struct.calcsize(fmt))
        samples.append((address, raw))
        return struct.unpack(fmt, raw)

    for rva, value in CODE.items():
        expected = bytes.fromhex(value)
        if s.read_block(base + rva, len(expected)) != expected:
            raise ValueError('Booth hit-test instructions changed')
    address = booth['address']
    if (booth.get('model') != 406 or booth.get('type_id') != 0
            or booth.get('name') != observer.character):
        raise ValueError('Booth target is not the selected merchant')
    if (read(address, '<Q')[0] != base + 0x5c5e20
            or read(address + 0x78, '<I')[0] != booth['uid']
            or read(address + 0x84, '<I')[0] != 406
            or read(address + 0xa4, '<32s')[0].split(b'\0', 1)[0] != observer.character.encode()
            or list(read(address + 0xe8, '<2I')) != booth['position']
            or list(read(address + 0xf8, '<2i')) != booth['draw_position']
            or read(address + 0x2c0, '<I')[0] != 14):
        raise ValueError('Booth actor identity or tile hit test changed')
    actor_vtable = read(address + 0x10, '<Q')[0]
    if (actor_vtable != base + 0x5cfa70
            or read(actor_vtable + 0x28, '<Q')[0] != base + 0x181a20):
        raise ValueError('Booth position accessor changed')
    graphics = read(address + 0x2f8, '<Q')[0]
    if (read(graphics, '<Q')[0] != base + 0x5d49d0
            or read(base + 0x5d49d0 + 0x40, '<Q')[0] != base + 0x262160):
        raise ValueError('Booth does not use the native tile hit test')
    orientation = read(graphics + 0xc, '<i')[0]
    if not 0 <= orientation < 8:
        raise ValueError('Booth orientation is invalid')
    offsets = list(zip(*[iter(read(base + 0x5bf670 + orientation * 40, '<10i'))] * 2))
    if any(abs(v) > 2 for pair in offsets for v in pair) or (0, 0) not in offsets:
        raise ValueError('Booth footprint is unsupported')

    # These are the map-to-screen transform and its zoom, read by Actor::hit_test.
    camera = base + 0x699360
    map_size = read(camera + 0x30, '<2i')
    origin_x, origin_y, scroll_x, scroll_y = read(camera + 0x44, '<4i')
    zoom, width, height, percent = read(camera + 0x26c, '<4i')
    if (read(base + 0x6545a0, '<I')[0] != 1
            or read(base + 0x6766e0, '<2i') != (width, height)
            or read(base + 0x5d4d28, '<d')[0] != 1 / 64
            or read(base + 0x5d4d40, '<d')[0] != 1 / 32):
        raise ValueError('Booth camera orientation or conversion changed')
    if not 1 <= zoom <= 1024 or not 1 <= percent <= 400:
        raise ValueError('Booth zoom is invalid')
    scale = (zoom * percent // 100) / 256
    x, y = booth['position']
    if (not 300 <= width <= 16384 or not 300 <= height <= 16384
            or not .25 <= scale <= 4 or not 0 <= x < map_size[0]
            or not 0 <= y < map_size[1]):
        raise ValueError('Booth camera dimensions or position are invalid')
    unscaled = ((x - y) * 32 + origin_x - scroll_x,
                (x + y) * 16 + origin_y - scroll_y)
    point = tuple(round((v - dim // 2) * scale + dim // 2)
                  for v, dim in zip(unscaled, (width, height)))
    if any(abs(v - actual) > 1 for v, actual in zip(unscaled, booth['draw_position'])):
        raise ValueError('Booth projection differs from its native draw position')
    # Round back through the native screen-to-tile path, including integer zoom.
    px, py = (math.trunc((v - dim // 2) / scale) + dim // 2
              for v, dim in zip(point, (width, height)))
    px += scroll_x - origin_x
    py += scroll_y - origin_y
    tile = (math.floor(py / 32 + px / 64 + .5), math.floor(py / 32 - px / 64 + .5))
    if tile != (x, y) or not (0 <= point[0] < width and 0 <= point[1] < height):
        raise ValueError('Booth click does not resolve to its native footprint')
    if any(s.read_block(a, len(raw)) != raw for a, raw in samples):
        raise ValueError('Booth or camera changed during target observation')
    s.assert_identity()
    if time.monotonic() - started > 2:
        raise ValueError('Booth target observation expired')
    return {'point': point, 'tile': tile, 'orientation': orientation,
            'footprint': tuple(sorted(set(offsets)))}
