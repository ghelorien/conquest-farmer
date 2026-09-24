"""Read-only exact-1078 Trade header Close target; never sends input.

Loaded code observed on Dutch PID 635124 / creation 134345856693697656.
113805 passes p_open to 744D0. Its window-seeded #CLOSE widget clears
p_open; 114A15 then invokes native cancellation 18A280 and model slot +70.
The protected cancellation implementation is not a transaction receipt.
Callers must prove an exact empty bilateral session, retain their once-only
journal/input lease, and assert_hovered(window, '#CLOSE') before mouse-down.
"""
import hashlib
import math
import struct

from conquest.merchants.trade_reader_1078 import assert_trade_code


CODE_BLOCKS = (
    (0x744D0, 895, 'a6a7caa67e29ab5f68afb4d37b8c279a6e49dd0e59791e22fc0e3a681a9b2eb8'),
    (0x75230, 777, 'ae5ada42fb2c658fabaee159ac37b26ab4dfdc57fe8bec1734abe74a1afb6517'),
    (0x74430, 157, 'd7209d5c71b562ae8124de6ce21b6a2741e0d6bd23efccb58287cb6b1eea31a6'),
    (0x63870, 3, 'e18aacce29affcdddaa5f641ac1ec12552e0fe9d066e6408941b1792818b8619'),
    (0x18A280, 14, '17886f1283f5a716566c1b7eee83be55d393865da23ad3d99af5a5fb3f6dc4d8'),
)
TRADE_VTABLE = 0x5E6A80


def control(driver, snapshot):
    """Return the fresh native window and Close point, without authorizing input."""
    gui = driver.memory.gui
    session = gui.session
    base = assert_trade_code(session)
    read = session.read_block
    for rva, size, digest in CODE_BLOCKS:
        if hashlib.sha256(read(base+rva, size)).hexdigest() != digest:
            raise ValueError('1078 native empty-trade Close code changed')
    constants = {0x5D10DC: b'#CLOSE\0', 0x5E6880: b'Trade##TradeWindow\0',
                 0x5F0A20: bytes.fromhex('00009040'),
                 0x5F0A78: bytes.fromhex('0100704100008041'),
                 0x5F0F80: struct.pack('<4f', 25, 25, 25, 25)}
    if any(read(base+rva, len(value)) != value for rva, value in constants.items()):
        raise ValueError('1078 native Trade Close label or dimensions changed')
    methods = {0x10: 0x113740, 0x58: 0x63870, 0x60: 0x63870, 0x70: 0x74430}
    if any(struct.unpack('<Q', read(base+TRADE_VTABLE+slot, 8))[0] != base+rva
           for slot, rva in methods.items()):
        raise ValueError('1078 Trade model Close dispatch changed')
    model = gui.model(14, TRADE_VTABLE)
    trade = snapshot.get('trade')
    if (read(model+12, 1) != b'\x01' or not isinstance(trade, dict)
            or snapshot.get('request') or trade.get('own_items') != [] or trade.get('items') != []
            or trade.get('own_silver') != 0 or trade.get('other_silver') != 0
            or trade.get('accepted') is not False or trade.get('other_accepted') is not False):
        raise ValueError('1078 Close target requires an open empty unaccepted Trade')
    matches = [w for w in snapshot['windows'] if w['name'] == 'Trade##TradeWindow']
    if len(matches) != 1:
        raise ValueError('1078 Trade window is absent or ambiguous')
    window = matches[0]
    raw = read(window['address'], 0xE0)
    geometry = struct.unpack_from('<4f', raw, 0x18)
    if tuple(window['geometry']) != geometry or not all(math.isfinite(v) for v in geometry):
        raise ValueError('1078 Trade geometry changed')
    # Header uses the top native ID-stack seed. Requiring the window seed
    # makes the caller's existing assert_hovered(window, '#CLOSE') exact.
    count = struct.unpack_from('<I', raw, 0xD0)[0]
    stack = struct.unpack_from('<Q', raw, 0xD8)[0]
    if (not 1 <= count <= 64 or not stack
            or read(stack+(count-1)*4, 4) != raw[8:12]):
        raise ValueError('1078 Trade Close hover seed is not its owning window')
    x, y, width, height = geometry
    # Native origin: right-16-15.0000009537, top+4.5; .6*25px icon.
    # Its interior center remains within the button regardless of padding.
    point = (round(x+width-23.5), round(y+12))
    viewport = gui.viewport_size()
    if (width <= 0 or height <= 0 or not x < point[0] < x+width
            or not y < point[1] < y+height
            or not 0 <= point[0] < viewport[0] or not 0 <= point[1] < viewport[1]):
        raise ValueError('1078 Trade Close point is outside its native window')
    current = [w for w in gui.windows() if w['name'] == 'Trade##TradeWindow']
    if (current != matches or read(model+12, 1) != b'\x01'
            or read(window['address'], 12) != raw[:12]
            or read(window['address']+0x18, 16) != raw[0x18:0x28]
            or read(window['address']+0xD0, 16) != raw[0xD0:0xE0]
            or read(stack+(count-1)*4, 4) != raw[8:12]):
        raise ValueError('1078 native Trade Close target changed during observation')
    session.assert_identity()
    return window, point
