"""Read the open shop, item metadata and GUI geometry from process memory.

Layout observed in Classic Conquer c2b53437. No rendering, screenshots, hooks,
memory writes or network packet input. GUI geometry alone never selects items.
"""
from dataclasses import dataclass
import math
import struct
import time

from conquest.addressing import checked_address
from conquest.memory_life import CLIENT_SHA256


@dataclass(frozen=True)
class GuiWindow:
    address: int
    name: str
    position: tuple[float, float]
    size: tuple[float, float]
    scroll: tuple[float, float]


class MemoryGui:
    def __init__(self, session, *, layout=None):
        # 1078 GUI observation is deliberately opt-in through a reader layout.
        # Every existing caller retains the pinned 1074-only default.
        if layout is None and session.expected_sha256 != CLIENT_SHA256:
            raise ValueError('GUI profile differs from client')
        if layout is not None and layout.expected_sha256 != session.expected_sha256:
            raise ValueError('GUI layout differs from client')
        self.session = session
        modules = [m for m in session.modules if m['name'].lower() == 'imconquer.exe']
        if len(modules) != 1:
            raise ValueError('Expected one client module')
        self.base = modules[0]['base']
        self.layout = layout
        self.context_rva = layout.gui_context_rva if layout is not None else 0x6966f0
        self.names = {}

    def read(self, name):
        s = self.session
        from conquest.viewport import size_for
        viewport=size_for(s)
        s.assert_identity()
        context = struct.unpack('<Q', s.read_block(self.base+self.context_rva, 8))[0]
        checked_address(context)
        header = s.read_block(context+0x3e58, 16)
        count, capacity, array = struct.unpack('<IIQ', header)
        if not 0 < count <= capacity <= 256:
            raise ValueError('GUI window list is invalid')
        entries = s.read_block(checked_address(array, count*8), count*8)
        addresses = struct.unpack('<'+'Q'*count, entries)
        if len(set(addresses)) != count:
            raise ValueError('Duplicate GUI windows')
        matches = []
        for address in addresses:
            pointer = struct.unpack('<Q', s.read_block(checked_address(address), 8))[0]
            key = (address, pointer)
            if key not in self.names:
                self.names[key] = s.read_block(checked_address(pointer), 96).split(b'\0')[0].decode('utf-8')
            if self.names[key] == name or (name.endswith(('/', '_')) and self.names[key].startswith(name)):
                matches.append((address, pointer, self.names[key]))
        if len(matches) != 1:
            raise ValueError('Requested GUI window is absent or ambiguous')
        address, pointer, title = matches[0]
        record = s.read_block(address, 0x250)
        frame = struct.unpack('<I', s.read_block(context+0x3e38, 4))[0]
        active_frame = struct.unpack_from('<I', record, 0x248)[0]
        if not record[0x97] or not 0 <= frame-active_frame <= 3:
            raise ValueError('Requested GUI window is not active')
        x, y, width, height = struct.unpack_from('<4f', record, 0x18)
        scroll = struct.unpack_from('<2f', record, 0x64)
        if (not all(math.isfinite(v) for v in (x,y,width,height,*scroll))
                or not 0 <= x < viewport[0] or not 0 <= y < viewport[1]
                or not 1 <= width <= viewport[0] or not 1 <= height <= viewport[1]):
            raise ValueError('GUI window geometry is invalid')
        latest_count, latest_capacity, latest_array = struct.unpack('<IIQ',s.read_block(context+0x3e58,16))
        if not 0 < latest_count <= latest_capacity <= 256:
            raise ValueError('GUI window list changed to invalid bounds')
        latest_entries = struct.unpack('<'+'Q'*latest_count,
            s.read_block(checked_address(latest_array,latest_count*8),latest_count*8))
        fresh = s.read_block(address,0x80)
        current_name = s.read_block(pointer,96).split(b'\0')[0].decode('utf-8')
        if (address not in latest_entries or current_name != title
                or fresh[:8] != record[:8] or fresh[0x18:0x28] != record[0x18:0x28]
                or fresh[0x64:0x6c] != record[0x64:0x6c]
                or s.read_block(self.base+self.context_rva,8) != struct.pack('<Q',context)):
            raise ValueError('GUI window changed during observation')
        if size_for(s)!=viewport:raise ValueError('Client resized during GUI observation')
        return GuiWindow(address,title,(x,y),(width,height),scroll)


@dataclass(frozen=True)
class ShopProduct:
    index: int
    address: int
    type_id: int
    name: str
    price: int
    healing: int
    level: int = 0
    profession: int = 0
    sex: int = 0
    strength: int = 0
    agility: int = 0
    weapon_skill: int = 0
    attack_min: int = 0
    attack_max: int = 0
    defense: int = 0
    dodge: int = 0


@dataclass(frozen=True)
class ShopSnapshot:
    vendor_id: int
    products: tuple[ShopProduct, ...]
    window: GuiWindow
    grid: GuiWindow

    def point(self, product):
        # Live ImGui table: five 48px columns, 80px rows (icon plus price).
        y=self.grid.position[1]+32+80*(product.index//5)-self.grid.scroll[1]
        if product not in self.products or self.grid.scroll[0]!=0 or not self.grid.position[1]+8<y<self.grid.position[1]+self.grid.size[1]-12:
            raise ValueError('Product is outside the qualified visible shop row')
        # Vertical resizing changes the visible row count, not the qualified
        # five-column/80px-row pitch. Keep widths, padding and visibility pinned.
        if (self.window.size[0]!=288. or self.grid.size[0]!=248.
                or self.window.size[1]-self.grid.size[1]!=68.
                or self.grid.position[0]-self.window.position[0]!=20.
                or self.grid.position[1]-self.window.position[1]!=38.):
            raise ValueError('Shop layout differs from the qualified grid')
        return (round(self.grid.position[0]+19+48*(product.index%5)), round(y))


class MemoryShopReader:
    def __init__(self, session):
        self.session = session
        self.gui = MemoryGui(session)
        self.base = self.gui.base

    def read(self, vendor_id):
        s = self.session
        root = self.base+0x69a740
        record = s.read_block(root,0x60)
        if struct.unpack_from('<Q',record)[0] != self.base+0x5d0088:
            raise ValueError('Shop object type changed')
        selected = struct.unpack_from('<I',record,8)[0]
        if selected != vendor_id:
            raise ValueError('Open shop belongs to a different vendor ID')
        array, capacity, offset, count = struct.unpack_from('<4Q',record,0x40)
        if not 0 < count <= capacity <= 256 or capacity & (capacity-1):
            raise ValueError('Shop item deque is invalid')
        data = s.read_block(checked_address(array,capacity*8), capacity*8)
        products = []
        for index in range(count):
            slot = struct.unpack_from('<Q',data,((offset+index)&(capacity-1))*8)[0]
            shared = s.read_block(checked_address(slot),16)
            item = struct.unpack_from('<Q',shared)[0]
            raw = s.read_block(checked_address(item),0x64)
            if struct.unpack_from('<Q',raw)[0] != self.base+0x5cf220:
                raise ValueError('Shop product type changed')
            type_id = struct.unpack_from('<I',raw,0x10)[0]
            length, allocated = struct.unpack_from('<QQ',raw,0x28)
            if not 1 <= length <= 63 or not length <= allocated <= 1024:
                raise ValueError('Shop product name is invalid')
            name_data = (raw[0x18:0x28] if allocated <= 15 else
                         s.read_block(checked_address(struct.unpack_from('<Q',raw,0x18)[0]),length))
            name = name_data[:length].decode('utf-8')
            price, healing = struct.unpack_from('<I',raw,0x48)[0], struct.unpack_from('<I',raw,0x5c)[0]
            if not name.isprintable() or not 0 < type_id < 100000000 or not 0 <= price < 100000000:
                raise ValueError('Shop product identity or price is invalid')
            fresh = s.read_block(item,0x64)
            # Ignore unrelated floating render state between these fields.
            if any(raw[a:b] != fresh[a:b] for a,b in ((0,8),(0x10,0x14),(0x18,0x48),(0x48,0x4c),(0x50,0x64))):
                raise ValueError('Shop product changed during observation')
            if s.read_block(slot,16) != shared:
                raise ValueError('Shop product reference changed')
            products.append(ShopProduct(index,item,type_id,name,price,healing,raw[0x3a],raw[0x38],raw[0x3b],
                struct.unpack_from('<H',raw,0x3c)[0],struct.unpack_from('<H',raw,0x3e)[0],raw[0x39],
                struct.unpack_from('<H',raw,0x52)[0],struct.unpack_from('<H',raw,0x50)[0],
                struct.unpack_from('<H',raw,0x54)[0],struct.unpack_from('<H',raw,0x58)[0]))
        if len({p.type_id for p in products}) != len(products):
            raise ValueError('Duplicate shop product types')
        if s.read_block(root,0x60) != record or s.read_block(array,capacity*8) != data:
            raise ValueError('Shop changed during observation')
        window = self.gui.read('Shop')
        grid = self.gui.read('Shop/##ShopGrid_')
        s.assert_identity()
        return ShopSnapshot(selected,tuple(products),window,grid)
