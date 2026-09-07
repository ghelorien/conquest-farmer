"""Read calibrated inventory records without screen capture or memory writes."""
from dataclasses import asdict, dataclass
import time

from pydantic import BaseModel, ConfigDict, Field

from conquest.addressing import Offset, checked_address, resolve_player


class InventoryLayout(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    expected_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    deque_map: Offset
    deque_map_size: Offset
    deque_start: Offset
    deque_count: Offset
    lookup_count: Offset
    silver: Offset
    equipped_ammo: Offset
    item_vtable_rva: Offset
    item_uid: Offset
    item_type: Offset
    item_amount: Offset
    item_limit: Offset
    capacity: int = Field(default=40, ge=1, le=100)


@dataclass(frozen=True)
class Item:
    uid: int
    type_id: int
    amount: int
    limit: int
    slot: int | None


@dataclass(frozen=True)
class InventorySnapshot:
    started_at: float
    timestamp: float
    items: tuple[Item, ...]
    equipped_ammo: Item | None
    silver: int
    capacity: int

    def count(self, type_id):
        return sum(item.amount for item in self.items if item.type_id == type_id)


def sample(session, fields):
    """Bound each RPC and preserve caller ordering across batches."""
    result = []
    for start in range(0, len(fields), 64):
        batch = fields[start:start + 64]
        response = session.request("sample", {"fields": [
            {"name": str(index), "address": hex(checked_address(address)), "kind": kind}
            for index, (address, kind) in enumerate(batch)]})
        if len(response["fields"]) != len(batch):
            raise ValueError("Incomplete inventory sample")
        result.extend(field["value"][0] for field in response["fields"])
    return result


def read_inventory(session, player, layout, module_base, clock=time.monotonic):
    started = clock()
    header_fields = [(player + getattr(layout, name), "u64") for name in
                     ("deque_map", "deque_map_size", "deque_start", "deque_count", "lookup_count")]
    header = sample(session, header_fields)
    table, table_size, first, count, lookup_count = header
    if not 0 <= table_size <= 256 or table_size & (table_size - 1):
        raise ValueError("Inventory map size is invalid")
    if not 0 <= count <= layout.capacity or count != lookup_count or count > table_size:
        raise ValueError("Inventory count is inconsistent")
    if first >= table_size and (table_size != 0 or first != 0 or count != 0):
        raise ValueError("Inventory start is outside the calibrated map")
    block_fields = [(table + ((first + index) % table_size) * 8, "u64") for index in range(count)]
    blocks = sample(session, block_fields)
    pointer_fields = [(checked_address(block), "u64") for block in blocks]
    pointer_fields.append((player + layout.equipped_ammo, "u64"))
    pointers = sample(session, pointer_fields)
    if len(set(pointers[:count])) != count or 0 in pointers[:count]:
        raise ValueError("Inventory contains null or duplicate item pointers")
    fields = []
    active = [(slot, ptr) for slot, ptr in enumerate(pointers) if ptr]
    for slot, ptr in active:
        fields.extend([(ptr, "u64"), (ptr + layout.item_uid, "u32"),
                       (ptr + layout.item_type, "u32"), (ptr + layout.item_amount, "u16"),
                       (ptr + layout.item_limit, "u16")])
    values = sample(session, fields)
    items, ammo = [], None
    for index, (slot, ptr) in enumerate(active):
        vtable, uid, type_id, amount, limit = values[index * 5:index * 5 + 5]
        if vtable != module_base + layout.item_vtable_rva or not uid or not type_id:
            raise ValueError("Inventory item identity is invalid")
        if amount > limit or limit == 0:
            raise ValueError("Inventory item amount is invalid")
        item = Item(uid, type_id, amount, limit, slot if slot < count else None)
        if slot < count:
            items.append(item)
        else:
            ammo = item
    if len({item.uid for item in items}) != len(items) or (ammo and ammo.uid in {item.uid for item in items}):
        raise ValueError("Inventory item identities are duplicated")
    silver = sample(session, [(player + layout.silver, "u32")])[0]
    # Recheck topology and records: a purchase, pickup, use, or equip operation
    # concurrent with reading invalidates the entire observation.
    final_values = sample(session, fields)
    # The equipped stack naturally decreases while a bow attack is running.
    # Accept its newer amount only if every identity/topology field is stable.
    if ammo is not None:
        ammo_index = next(index for index,(slot,ptr) in enumerate(active) if slot == count)
        amount_index = ammo_index * 5 + 3
        if 0 <= final_values[amount_index] <= values[amount_index]:
            values[amount_index] = final_values[amount_index]
            ammo = Item(ammo.uid, ammo.type_id, final_values[amount_index], ammo.limit, None)
    if (sample(session, header_fields) != header or sample(session, block_fields) != blocks
            or sample(session, pointer_fields) != pointers or final_values != values
            or sample(session, [(player + layout.silver, "u32")])[0] != silver):
        raise ValueError("Inventory changed during observation")
    session.assert_identity()
    return InventorySnapshot(started, clock(), tuple(items), ammo, silver, layout.capacity)


class MemoryInventoryReader:
    def __init__(self, session, player_layout, inventory_layout):
        if inventory_layout.expected_sha256 != player_layout.expected_sha256:
            raise ValueError("Inventory profile fingerprint differs from player profile")
        self.session = session
        self.player_layout = player_layout
        self.layout = inventory_layout

    def read(self):
        addresses = resolve_player(self.session, self.player_layout)
        module = next(m for m in self.session.modules
                      if m["name"].casefold() == self.player_layout.module.casefold())
        result = read_inventory(self.session, addresses["object"], self.layout, module["base"])
        if resolve_player(self.session, self.player_layout) != addresses:
            raise ValueError("Player object changed during inventory observation")
        return result

    def report(self):
        return {"stage": "inventory_candidate_sample", "qualified": False,
                "restart_qualified": False, "snapshot": asdict(self.read())}
