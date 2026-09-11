"""Read the active warehouse item deque; no injected calls or memory writes."""
from dataclasses import dataclass
import struct

from conquest.addressing import checked_address
from conquest.memory_inventory import Item
from conquest.memory_shop import MemoryGui


@dataclass(frozen=True)
class WarehouseSnapshot:
    items: tuple[Item, ...]
    capacity: int


class MemoryWarehouseReader:
    def __init__(self, session):
        self.session = session
        self.gui = MemoryGui(session)  # Enforces the observed client fingerprint.

    def read(self):
        s, base = self.session, self.gui.base
        window = self.gui.read('Warehouse')
        observed = []

        def block(address, size):
            data = s.read_block(checked_address(address, size), size)
            observed.append((address, data))
            return data

        # Getter 181b30 returns **(module+69c730), not resolve_player's base.
        shared = struct.unpack('<Q', block(base+0x69c730, 8))[0]
        owner = struct.unpack('<Q', block(shared, 8))[0]
        table, size, start, count = struct.unpack('<4Q', block(owner+0x1008, 32))
        capacity = struct.unpack('<I', block(owner+0x1030, 4))[0]
        if (not 1 <= capacity <= 1000 or not 0 <= count <= capacity
                or not 0 <= size <= 2048 or size & (size-1) or count > size
                or (size == 0 and (table or start or count))):
            raise ValueError('Warehouse deque is invalid')
        items = []
        pointers = set()
        for slot in range(count):
            entry = struct.unpack('<Q', block(table+((start+slot)&(size-1))*8, 8))[0]
            pointer = struct.unpack('<Q', block(entry, 8))[0]
            if pointer in pointers:
                raise ValueError('Duplicate warehouse item pointer')
            pointers.add(pointer)
            vtable = struct.unpack('<Q', block(pointer, 8))[0]
            uid = struct.unpack('<I', block(pointer+8, 4))[0]
            kind = struct.unpack('<I', block(pointer+0x10, 4))[0]
            amount, limit = struct.unpack('<HH', block(pointer+0x62, 4))
            plus = block(pointer+0x6b, 1)[0]
            if vtable != base+0x5cf220 or not uid or not kind or not 0 < amount <= limit:
                raise ValueError('Warehouse item identity is invalid')
            items.append(Item(uid, kind, amount, limit, slot, plus))
        if len({i.uid for i in items}) != count:
            raise ValueError('Duplicate warehouse item identity')
        if any(s.read_block(address, len(data)) != data for address, data in observed):
            raise ValueError('Warehouse changed during observation')
        if self.gui.read('Warehouse') != window:
            raise ValueError('Warehouse window changed during observation')
        s.assert_identity()
        return WarehouseSnapshot(tuple(items), capacity)


def deposit_received(item, before_bag, before_stash, bag, stash):
    """Require the same UID/type in storage, with no other item or silver loss."""
    identity = lambda i: (i.uid, i.type_id, i.amount, i.limit, i.plus)
    return (identity(item) in {identity(i) for i in before_bag.items}
            and item.uid not in {i.uid for i in before_stash.items}
            and bag.silver == before_bag.silver
            and {identity(i) for i in bag.items} ==
                {identity(i) for i in before_bag.items if i.uid != item.uid}
            and {identity(i) for i in stash.items} ==
                {identity(i) for i in before_stash.items} | {identity(item)})


def withdrawal_received(item,before_bag,before_stash,bag,stash):
    return (deposit_received(item,bag,stash,before_bag,before_stash)
            and getattr(bag,'equipped_ammo',None)==getattr(before_bag,'equipped_ammo',None))


@dataclass(frozen=True)
class WarehouseMoney:
    silver: int
    amount: str
    controller: int
    window: object
    grid: object


def normalized_money_amount(value):
    import re
    # The client formats amounts with thousands separators while typing.
    if not value:return ''
    if not re.fullmatch(r'(?:[0-9]{1,10}|[0-9]{1,3}(?:,[0-9]{3}){1,3})',value):
        raise ValueError('Warehouse amount formatting changed')
    return str(int(value.replace(',','')))


class WarehouseMoneyReader:
    """Pinned renderer 1169e0: owner+1044 bank silver, controller+48 amount."""
    def __init__(self,session):
        self.session=session;self.gui=MemoryGui(session)

    def read(self):
        s=self.session;base=self.gui.base;checks=[]
        def read(address,size):
            data=s.read_block(checked_address(address,size),size);checks.append((address,data));return data
        window=self.gui.read('Warehouse');grid=self.gui.read('Warehouse/ScrollingRegion_')
        shared=struct.unpack('<Q',read(base+0x69c730,8))[0]
        owner=struct.unpack('<Q',read(shared,8))[0]
        silver=struct.unpack('<I',read(owner+0x1044,4))[0]
        head,count=struct.unpack('<QQ',read(base+0x6986c0,16))
        if not 1<=count<=256:raise ValueError('GUI controller registry bounds changed')
        node=struct.unpack('<Q',read(head+8,8))[0];seen=set()
        for _ in range(32):
            if node in seen:raise ValueError('GUI controller registry cycle')
            seen.add(node);raw=read(node,0x38)
            if raw[0x19]:raise ValueError('Warehouse controller is absent')
            kind=struct.unpack_from('<I',raw,0x20)[0]
            if kind==0x16:break
            node=struct.unpack_from('<Q',raw,0 if kind>0x16 else 0x10)[0]
        else:raise ValueError('Warehouse controller lookup exceeded bounds')
        controller=struct.unpack_from('<Q',raw,0x28)[0]
        identity=read(controller,12)
        if struct.unpack_from('<Q',identity)[0]!=base+0x5cba68 or struct.unpack_from('<I',identity,8)[0]!=0x16:
            raise ValueError('Warehouse money controller identity changed')
        value=read(controller+0x48,12)
        if b'\0' not in value:raise ValueError('Warehouse amount is unterminated')
        amount=normalized_money_amount(value.split(b'\0')[0].decode('ascii'))
        if len(amount)>10 or (amount and not amount.isdecimal()) or silver>0x7fffffff:
            raise ValueError('Warehouse money bounds changed')
        if any(s.read_block(a,len(data))!=data for a,data in checks):
            raise ValueError('Warehouse money changed during observation')
        if self.gui.read('Warehouse')!=window or self.gui.read('Warehouse/ScrollingRegion_')!=grid:
            raise ValueError('Warehouse money controls moved')
        s.assert_identity()
        return WarehouseMoney(silver,amount,controller,window,grid)


def money_received(direction,amount,before_bag,before_bank,bag,bank):
    if direction not in ('deposit','withdraw') or type(amount) is not int or amount<=0:return False
    sign=1 if direction=='deposit' else -1
    return (bag.items==before_bag.items and bag.equipped_ammo==before_bag.equipped_ammo
            and before_bag.silver-bag.silver==sign*amount
            and bank.silver-before_bank.silver==sign*amount)
