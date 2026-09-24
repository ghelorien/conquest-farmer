"""Read calibrated inventory records without screen capture or memory writes."""

from dataclasses import asdict, dataclass
import time

from pydantic import BaseModel, ConfigDict, Field

from conquest.addressing import Offset, checked_address, resolve_object, resolve_player


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
    item_plus: Offset | None = None
    owner_root_rva: Offset | None = None
    owner_pointer_offsets: tuple[Offset, ...] | None = None
    owner_vtable_rva: Offset | None = None
    owner_name_offset: Offset | None = None
    capacity: int = Field(default=40, ge=1, le=100)


@dataclass(frozen=True)
class Item:
    uid: int
    type_id: int
    amount: int
    limit: int
    slot: int | None
    plus: int | None = None


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
    if not hasattr(session, "request"):
        reader = getattr(session, "read_block", None) or session.read
        sizes = {"u64": 8, "u32": 4, "u16": 2}
        values = []
        for address, kind in fields:
            if kind not in sizes:
                raise ValueError("Unsupported direct inventory sample kind")
            values.append(
                int.from_bytes(reader(checked_address(address), sizes[kind]), "little")
            )
        return values
    result = []
    for start in range(0, len(fields), 64):
        batch = fields[start : start + 64]
        response = session.request(
            "sample",
            {
                "fields": [
                    {
                        "name": str(index),
                        "address": hex(checked_address(address)),
                        "kind": kind,
                    }
                    for index, (address, kind) in enumerate(batch)
                ]
            },
        )
        if len(response["fields"]) != len(batch):
            raise ValueError("Incomplete inventory sample")
        result.extend(field["value"][0] for field in response["fields"])
    return result


def read_inventory(session, player, layout, module_base, clock=time.monotonic):
    started = clock()
    header_fields = [
        (player + getattr(layout, name), "u64")
        for name in (
            "deque_map",
            "deque_map_size",
            "deque_start",
            "deque_count",
            "lookup_count",
        )
    ]
    header = sample(session, header_fields)
    table, table_size, first, count, lookup_count = header
    if not 0 <= table_size <= 256 or table_size & (table_size - 1):
        raise ValueError("Inventory map size is invalid")
    if not 0 <= count <= layout.capacity or count != lookup_count or count > table_size:
        raise ValueError("Inventory count is inconsistent")
    # The deque keeps a logical offset, which can exceed the map length after
    # removals. The pointer slots wrap modulo table_size; all raw header fields
    # and item identities are still rechecked below before accepting the read.
    if not 0 <= first <= 0xFFFFFFFFFFFFFFFF or (table_size == 0 and first != 0):
        raise ValueError("Inventory start is outside the calibrated map")
    block_fields = [
        (table + ((first + index) % table_size) * 8, "u64") for index in range(count)
    ]
    blocks = sample(session, block_fields)
    pointer_fields = [(checked_address(block), "u64") for block in blocks]
    pointer_fields.append((player + layout.equipped_ammo, "u64"))
    pointers = sample(session, pointer_fields)
    if len(set(pointers[:count])) != count or 0 in pointers[:count]:
        raise ValueError("Inventory contains null or duplicate item pointers")
    fields = []
    active = [(slot, ptr) for slot, ptr in enumerate(pointers) if ptr]
    stride = 6 if layout.item_plus is not None else 5
    for slot, ptr in active:
        fields.extend(
            [
                (ptr, "u64"),
                (ptr + layout.item_uid, "u32"),
                (ptr + layout.item_type, "u32"),
                (ptr + layout.item_amount, "u16"),
                (ptr + layout.item_limit, "u16"),
            ]
        )
        if layout.item_plus is not None:
            fields.append((ptr + layout.item_plus, "u16"))
    values = sample(session, fields)
    items, ammo = [], None
    for index, (slot, ptr) in enumerate(active):
        vtable, uid, type_id, amount, limit = values[
            index * stride : index * stride + 5
        ]
        if vtable != module_base + layout.item_vtable_rva or not uid or not type_id:
            raise ValueError("Inventory item identity is invalid")
        if amount > limit or limit == 0:
            raise ValueError("Inventory item amount is invalid")
        plus = (values[index * stride + 5] & 0xFF) if stride == 6 else None
        item = Item(uid, type_id, amount, limit, slot if slot < count else None, plus)
        if slot < count:
            items.append(item)
        else:
            ammo = item
    if len({item.uid for item in items}) != len(items) or (
        ammo and ammo.uid in {item.uid for item in items}
    ):
        raise ValueError("Inventory item identities are duplicated")
    silver = sample(session, [(player + layout.silver, "u32")])[0]
    # Recheck topology and records: a purchase, pickup, use, or equip operation
    # concurrent with reading invalidates the entire observation.
    final_values = sample(session, fields)
    # The equipped stack naturally decreases while a bow attack is running.
    # Accept its newer amount only if every identity/topology field is stable.
    if ammo is not None:
        ammo_index = next(
            index for index, (slot, ptr) in enumerate(active) if slot == count
        )
        amount_index = ammo_index * stride + 3
        if 0 <= final_values[amount_index] <= values[amount_index]:
            values[amount_index] = final_values[amount_index]
            ammo = Item(
                ammo.uid,
                ammo.type_id,
                final_values[amount_index],
                ammo.limit,
                None,
                ammo.plus,
            )
    if (
        sample(session, header_fields) != header
        or sample(session, block_fields) != blocks
        or sample(session, pointer_fields) != pointers
        or final_values != values
        or sample(session, [(player + layout.silver, "u32")])[0] != silver
    ):
        raise ValueError("Inventory changed during observation")
    session.assert_identity()
    return InventorySnapshot(
        started, clock(), tuple(items), ammo, silver, layout.capacity
    )


class MemoryInventoryReader:
    def __init__(self, session, player_layout, inventory_layout):
        if inventory_layout.expected_sha256 != player_layout.expected_sha256:
            raise ValueError(
                "Inventory profile fingerprint differs from player profile"
            )
        self.session = session
        self.player_layout = player_layout
        self.layout = inventory_layout

    @classmethod
    def for_session(cls, session):
        from conquest.memory_build_layout import inventory_reader_layouts

        player, inventory = inventory_reader_layouts(session)
        return cls(session, player, inventory)

    def read(self):
        addresses = resolve_player(self.session, self.player_layout)
        module = next(
            m
            for m in self.session.modules
            if m["name"].casefold() == self.player_layout.module.casefold()
        )
        owner = addresses["object"]
        layout = self.layout
        owner_fields = (
            layout.owner_root_rva,
            layout.owner_pointer_offsets,
            layout.owner_vtable_rva,
            layout.owner_name_offset,
        )
        reader = None
        actual_name = None
        owner_name = None
        if any(value is not None for value in owner_fields):
            if any(value is None for value in owner_fields):
                raise ValueError("Inventory owner layout is incomplete")
            owner = resolve_object(
                self.session,
                expected_sha256=layout.expected_sha256,
                module=self.player_layout.module,
                root_rva=layout.owner_root_rva,
                pointer_offsets=layout.owner_pointer_offsets,
                vtable_rva=layout.owner_vtable_rva,
            )
            reader = getattr(self.session, "read_block", None) or self.session.read
            actual_name = reader(addresses["name"], 64)
            owner_name = reader(owner + layout.owner_name_offset, 64)
            if (
                not actual_name.split(b"\0", 1)[0]
                or actual_name.split(b"\0", 1)[0] != owner_name.split(b"\0", 1)[0]
            ):
                raise ValueError("Inventory owner identity differs from player")
        result = read_inventory(self.session, owner, self.layout, module["base"])
        if resolve_player(self.session, self.player_layout) != addresses:
            raise ValueError("Player object changed during inventory observation")
        if (
            owner != addresses["object"]
            and resolve_object(
                self.session,
                expected_sha256=layout.expected_sha256,
                module=self.player_layout.module,
                root_rva=layout.owner_root_rva,
                pointer_offsets=layout.owner_pointer_offsets,
                vtable_rva=layout.owner_vtable_rva,
            )
            != owner
        ):
            raise ValueError("Inventory wrapper changed during observation")
        if reader is not None and (
            reader(addresses["name"], 64) != actual_name
            or reader(owner + layout.owner_name_offset, 64) != owner_name
        ):
            raise ValueError("Inventory owner identity changed during observation")
        return result

    def carried_pointer(self, snapshot, uid):
        """Resolve an exact carried UID through the qualified inventory owner.

        In 1078 the carried deque belongs to the inventory wrapper, not the
        actual player object.  A pointer is usable only while the same complete
        inventory and owner remain observable on both sides of the lookup.
        """
        from conquest.addressing import resolve_object

        layout = self.layout
        matches = [item for item in snapshot.items if item.uid == uid]
        if len(matches) != 1 or matches[0].slot is None:
            raise ValueError("Equipment UID not carried in one inventory slot")
        item = matches[0]

        def owner_address():
            if layout.owner_root_rva is None:
                return resolve_player(self.session, self.player_layout)["object"]
            return resolve_object(
                self.session,
                expected_sha256=layout.expected_sha256,
                module=self.player_layout.module,
                root_rva=layout.owner_root_rva,
                pointer_offsets=layout.owner_pointer_offsets,
                vtable_rva=layout.owner_vtable_rva,
            )

        def same_inventory():
            current = self.read()
            return (
                current.items == snapshot.items
                and current.silver == snapshot.silver
                and current.equipped_ammo == snapshot.equipped_ammo
                and current.capacity == snapshot.capacity
            )

        if not same_inventory():
            raise ValueError("Inventory changed before equipment lookup")
        owner = owner_address()
        header_fields = [
            (owner + getattr(layout, name), "u64")
            for name in (
                "deque_map",
                "deque_map_size",
                "deque_start",
                "deque_count",
                "lookup_count",
            )
        ]
        table, table_size, first, count, lookup_count = sample(
            self.session, header_fields
        )
        header = (table, table_size, first, count, lookup_count)
        if (
            not 0 < table_size <= 256
            or table_size & (table_size - 1)
            or count != lookup_count
            or count != len(snapshot.items)
            or not 0 <= item.slot < count
        ):
            raise ValueError("Invalid inventory equipment lookup")
        slot_address = checked_address(table + ((first + item.slot) % table_size) * 8)
        block = sample(self.session, [(slot_address, "u64")])[0]
        pointer = sample(self.session, [(checked_address(block), "u64")])[0]
        if not pointer or sample(
            self.session,
            [(pointer + layout.item_uid, "u32"), (pointer + layout.item_type, "u32")],
        ) != [item.uid, item.type_id]:
            raise ValueError("Equipment pointer differs from carried item")
        if (
            owner_address() != owner
            or tuple(sample(self.session, header_fields)) != header
            or sample(self.session, [(slot_address, "u64")])[0] != block
            or sample(self.session, [(checked_address(block), "u64")])[0] != pointer
            or not same_inventory()
        ):
            raise ValueError("Inventory changed during equipment lookup")
        self.session.assert_identity()
        return pointer

    def report(self):
        return {
            "stage": "inventory_candidate_sample",
            "qualified": False,
            "restart_qualified": False,
            "snapshot": asdict(self.read()),
        }
