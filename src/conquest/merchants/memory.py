"""Fingerprint-pinned booth/trade readers. All observations are read-only.

Layouts are derived from the c2b53437 client renderer and checked against live
merchant inventories/booths. Trade and input qualification is tracked separately.
"""

from conquest.character_context import farmer_name
from dataclasses import dataclass, asdict
import math
import struct
import time
import zlib
from pathlib import Path
import yaml
from conquest.addressing import PlayerLayout, resolve_player, checked_address
from conquest.memory_inventory import InventoryLayout, MemoryInventoryReader
from conquest.memory_life import CLIENT_SHA256
from conquest.merchants.transit_life import stable_life as read_life
from conquest.equipment import item_details
from conquest.merchants.pricing import ItemKey, quality, socket_name


def unpack(session, address, fmt):
    return struct.unpack(
        fmt, session.read_block(checked_address(address), struct.calcsize(fmt))
    )


def assert_booth_stable(session, actor, model, owned_uid, before, *, layout=None):
    # +0x54 begins the editable price buffer. Native queued typing may change
    # it during a read; price submission has its own exact-value guard. Keep
    # the model/header, open state, owner and selected item UID stable.
    after = session.read_block(model, 0x58)
    offset = layout.merchant_own_booth_offset if layout is not None else 0x3258
    if (
        unpack(session, actor + offset, "<I")[0] != owned_uid
        or after[:0x54] != before[:0x54]
    ):
        raise ValueError("Booth ownership or selected item changed during observation")


def character_uid(session, base, actor, *, layout=None):
    # Both pinned call sites resolve the self actor using RVA 0x181b30,
    # then compare actor+0x68 with a received/other actor identity.
    # actor+0x3258 instead belongs to the owned booth and can be zero.
    pins = (
        layout.merchant_uid_accessor_pins
        if layout is not None
        else (
            (0x8DC8, "e8638d17008b486841394f10"),
            (0x97BC, "e86f8317008b4868394e687520"),
        )
    )
    for rva, encoded in pins:
        expected = bytes.fromhex(encoded)
        if session.read_block(base + rva, len(expected)) != expected:
            raise ValueError(
                "Character identity accessor needs qualification for this client"
            )
    uid = unpack(session, actor + 0x68, "<I")[0]
    if not uid:
        raise ValueError("Character UID is unavailable")
    return uid


def string(session, address, maximum=128):
    raw = session.read_block(address, 32)
    length, capacity = struct.unpack_from("<QQ", raw, 16)
    if not 0 <= length <= maximum or not max(length, 15) <= capacity <= 4096:
        raise ValueError("Invalid client string")
    value = (
        raw[:length]
        if capacity <= 15
        else session.read_block(
            checked_address(struct.unpack_from("<Q", raw)[0]), length
        )
        if length
        else b""
    )
    if session.read_block(address, 32) != raw:
        raise ValueError("Client string changed during observation")
    return value.decode("utf-8")


def deque_items(session, address, limit):
    header = session.read_block(address, 32)
    table, capacity, start, count = struct.unpack("<4Q", header)
    if (
        not 0 <= count <= limit
        or not 0 <= capacity <= 256
        or capacity & (capacity - 1)
        or count > capacity
    ):
        raise ValueError("Invalid item deque")
    if not capacity:
        if count or start:
            raise ValueError("Invalid empty item deque")
        return [], header
    pointers = session.read_block(checked_address(table), capacity * 8)
    rows, references = [], []
    for i in range(count):
        slot = struct.unpack_from("<Q", pointers, ((start + i) % capacity) * 8)[0]
        shared = session.read_block(checked_address(slot), 16)
        ptr = checked_address(struct.unpack_from("<Q", shared)[0])
        rows.append(ptr)
        references.append((slot, shared))
    if (
        len(set(rows)) != len(rows)
        or session.read_block(address, 32) != header
        or session.read_block(table, capacity * 8) != pointers
        or any(session.read_block(p, 16) != raw for p, raw in references)
    ):
        raise ValueError("Item collection changed during observation")
    return rows, header


class GuiObservationChanged(ValueError):
    """The renderer changed a previously valid GUI sample while reading it."""


class TransitObservationChanged(ValueError):
    """A moving actor or town transition needs another read, never another click."""


class HoverNotReady(ValueError):
    """The rendered hover ID does not yet identify the requested control."""


class GuiReader:
    def __init__(self, session, *, layout=None):
        if layout is None and session.expected_sha256 != CLIENT_SHA256:
            raise ValueError("Unqualified merchant client fingerprint")
        if layout is not None and session.expected_sha256 != layout.expected_sha256:
            raise ValueError("Merchant GUI layout differs from client")
        self.session = session
        self.layout = layout
        modules = [
            m for m in session.modules if m["name"].casefold() == "imconquer.exe"
        ]
        if len(modules) != 1:
            raise ValueError("Ambiguous client module")
        self.base = modules[0]["base"]
        self.context_rva = layout.gui_context_rva if layout is not None else 0x6966F0
        self.registry_rva = layout.gui_registry_rva if layout is not None else 0x6986C0

    @classmethod
    def for_session(cls, session):
        """Explicit read-only GUI evidence for one exact selected build."""
        if not hasattr(session, "read_block"):
            from types import SimpleNamespace

            session = SimpleNamespace(
                expected_sha256=session.expected_sha256,
                modules=session.modules,
                identity=session.identity,
                read=session.read,
                read_block=session.read,
                assert_identity=session.assert_identity,
                viewport_size=getattr(session, "viewport_size", None),
            )
        from conquest.memory_build_layout import read_build_layout

        return cls(session, layout=read_build_layout(session))

    def viewport_size(self):
        s = self.session
        s.assert_identity()
        context = unpack(s, self.base + self.context_rva, "<Q")[0]
        array = unpack(s, context + 0x40C0, "<Q")[0]
        viewport = unpack(s, array, "<Q")[0]
        size = unpack(s, viewport + 0xC, "<2f")
        if not all(math.isfinite(v) and 320 <= v <= 8192 and int(v) == v for v in size):
            raise ValueError("Invalid merchant GUI viewport")
        if (
            unpack(s, self.base + self.context_rva, "<Q")[0] != context
            or unpack(s, context + 0x40C0, "<Q")[0] != array
            or unpack(s, array, "<Q")[0] != viewport
            or unpack(s, viewport + 0xC, "<2f") != size
        ):
            raise GuiObservationChanged("Merchant GUI viewport changed")
        s.assert_identity()
        return [int(v) for v in size]

    def assert_hovered(self, window, label, *, seeds=None):
        s = self.session
        s.assert_identity()
        context = unpack(s, self.base + self.context_rva, "<Q")[0]
        if seeds is None:
            seeds = [unpack(s, window["address"] + 8, "<I")[0]]
        expected = {zlib.crc32(label.encode("utf-8"), seed) for seed in seeds}
        hovered_window = unpack(s, context + 0x3EC0, "<Q")[0]
        hovered_id = unpack(s, context + 0x3EF0, "<I")[0]
        if hovered_window != window["address"] or hovered_id not in expected:
            raise HoverNotReady(
                "Pointer is not over the memory-identified merchant control"
            )
        if unpack(s, self.base + self.context_rva, "<Q")[0] != context:
            raise GuiObservationChanged(
                "Merchant GUI context changed during hover observation"
            )
        if (
            unpack(s, context + 0x3EC0, "<Q")[0] != hovered_window
            or unpack(s, context + 0x3EF0, "<I")[0] != hovered_id
        ):
            # This sample proves no control. Let the caller's existing bounded
            # hover wait reobserve all guards, exactly like an initial mismatch.
            raise HoverNotReady("Merchant hover changed during observation")
        s.assert_identity()

    def model(self, key, vtable):
        s = self.session
        s.assert_identity()
        header = s.read_block(self.base + self.registry_rva, 16)
        head, count = struct.unpack("<QQ", header)
        if not 1 <= count <= 128:
            raise ValueError("Invalid client window registry")
        node = unpack(s, head + 8, "<Q")[0]
        visited = set()
        while node != head:
            if node in visited or len(visited) >= count:
                raise ValueError("Invalid client window tree")
            visited.add(node)
            raw = s.read_block(checked_address(node), 56)
            found = struct.unpack_from("<I", raw, 32)[0]
            if found == key:
                ptr = checked_address(struct.unpack_from("<Q", raw, 40)[0])
                if (
                    unpack(s, ptr, "<Q")[0] != self.base + vtable
                    or s.read_block(node, 56) != raw
                    or s.read_block(self.base + self.registry_rva, 16) != header
                ):
                    raise ValueError("Client window model changed")
                s.assert_identity()
                return ptr
            node = struct.unpack_from("<Q", raw, 0 if key < found else 16)[0]
        raise ValueError("Client window model absent")

    def windows(self):
        # Retry changed membership/geometry; draw-order-only changes are allowed
        # below. No retry here sends input.
        for attempt in range(3):
            try:
                return GuiReader._windows(self)
            except GuiObservationChanged:
                if attempt == 2:
                    raise
                # Immediate rereads can all fall inside one render mutation.
                # Yield before a complete fresh sample; callers still compare
                # the resulting structural revision before any input.
                time.sleep(0.01)

    def _windows(self):
        s = self.session
        context = unpack(s, self.base + self.context_rva, "<Q")[0]

        def registry_members():
            header = s.read_block(context + 0x3E58, 16)
            count, capacity, array = struct.unpack("<IIQ", header)
            if not 0 < count <= capacity <= 256:
                raise ValueError("Invalid GUI registry")
            entries = s.read_block(checked_address(array), count * 8)
            if (
                unpack(s, self.base + self.context_rva, "<Q")[0] != context
                or s.read_block(context + 0x3E58, 16) != header
            ):
                raise GuiObservationChanged("GUI registry changed during vector read")
            addresses = struct.unpack("<" + "Q" * count, entries)
            if len(set(addresses)) != count:
                raise ValueError("Duplicate GUI registry entries")
            return addresses

        addresses = registry_members()
        result = []
        for ptr in addresses:
            # Rendering continues while a diagnostic RPC is in flight. A
            # single frame read at the start rejects every later live window
            # as being "from the future" on high-FPS clients.
            frame_before = unpack(s, context + 0x3E38, "<I")[0]
            raw = s.read_block(checked_address(ptr), 0x250)
            frame_after = unpack(s, context + 0x3E38, "<I")[0]
            last_frame = struct.unpack_from("<I", raw, 0x248)[0]
            if frame_after < frame_before:
                raise ValueError("GUI frame counter reset during observation")
            if not raw[0x97] or not frame_before - 3 <= last_frame <= frame_after:
                continue
            name = (
                s.read_block(checked_address(struct.unpack_from("<Q", raw)[0]), 128)
                .split(b"\0")[0]
                .decode("utf-8")
            )
            geometry = struct.unpack_from("<4f", raw, 0x18)
            scroll = struct.unpack_from("<2f", raw, 0x64)
            if (
                not all(
                    math.isfinite(v) and -8192 <= v <= 8192
                    for v in (*geometry, *scroll)
                )
                or min(geometry[2:]) <= 0
            ):
                raise ValueError("Invalid GUI geometry")
            if s.read_block(ptr, 12) != raw[:12]:
                raise GuiObservationChanged("GUI window identity changed")
            if (
                s.read_block(ptr + 0x18, 16) != raw[0x18:0x28]
                or s.read_block(ptr + 0x64, 8) != raw[0x64:0x6C]
            ):
                raise GuiObservationChanged("GUI geometry changed")
            result.append(
                {"name": name, "address": ptr, "geometry": geometry, "scroll": scroll}
            )
        latest = registry_members()
        # Native 1078 swaps two differently sized backing buffers each frame.
        # Each vector read must be coherent, but pointer/capacity/order between
        # complete reads are storage details. Exact window membership, identity
        # and live geometry remain required; callers also fence layout revisions.
        if len(latest) != len(addresses) or set(latest) != set(addresses):
            raise GuiObservationChanged("GUI registry membership changed")
        s.assert_identity()
        return result

    def table(self, window, label):
        for attempt in range(3):
            try:
                return GuiReader._table(self, window, label)
            except GuiObservationChanged:
                if attempt == 2:
                    raise

    def _table(self, window, label):
        """Read the rendered table layout, including its scroll/clip bounds.

        This client uses 536-byte ImGuiTable records and 104-byte columns.
        The window-seeded table ID and both owning-window pointers must agree;
        an unrelated or old table cannot supply merchant input coordinates.
        """
        s = self.session
        context = unpack(s, self.base + self.context_rva, "<Q")[0]
        window_id = unpack(s, window["address"] + 8, "<I")[0]
        expected_id = zlib.crc32(label.encode("utf-8"), window_id)
        header = s.read_block(context + 0x4338, 16)
        count, capacity, array = struct.unpack("<IIQ", header)
        if not 0 < count <= capacity <= 256:
            raise ValueError("Invalid GUI table pool")
        frame_before = unpack(s, context + 0x3E38, "<I")[0]
        records = s.read_block(checked_address(array), count * 536)
        matches = [
            i
            for i in range(count)
            if struct.unpack_from("<I", records, i * 536)[0] == expected_id
        ]
        if len(matches) != 1:
            raise ValueError("Expected one window-owned GUI table")
        address = array + matches[0] * 536
        raw = records[matches[0] * 536 : (matches[0] + 1) * 536]
        frame_after = unpack(s, context + 0x3E38, "<I")[0]
        last_frame, columns = struct.unpack_from("<II", raw, 0x70)
        if (
            frame_after < frame_before
            or not frame_before - 3 <= last_frame <= frame_after
        ):
            raise ValueError("GUI table is not currently rendered")
        if not 1 <= columns <= 64 or struct.unpack_from("<2Q", raw, 0x180) != (
            window["address"],
            window["address"],
        ):
            raise ValueError("GUI table ownership or column count changed")
        pointer = checked_address(struct.unpack_from("<Q", raw, 0x18)[0])
        column_data = s.read_block(pointer, columns * 104)
        outer = struct.unpack_from("<4f", raw, 0xF0)
        clip = struct.unpack_from("<4f", raw, 0x120)
        row_height = struct.unpack_from("<f", raw, 0x1AC)[0]
        cells = [
            {
                "minimum": struct.unpack_from("<f", column_data, i * 104 + 8)[0],
                "maximum": struct.unpack_from("<f", column_data, i * 104 + 12)[0],
                "content_x": struct.unpack_from("<f", column_data, i * 104 + 52)[0],
            }
            for i in range(columns)
        ]
        values = (
            *outer,
            *clip,
            row_height,
            *(v for cell in cells for v in cell.values()),
        )
        if (
            not all(math.isfinite(v) and -8192 <= v <= 8192 for v in values)
            or not 4 <= row_height <= 512
            or outer[2] <= outer[0]
            or outer[3] <= outer[1]
            or clip[2] <= clip[0]
            or clip[3] <= clip[1]
            or any(not c["minimum"] <= c["content_x"] < c["maximum"] for c in cells)
        ):
            raise GuiObservationChanged("Invalid GUI table geometry")
        after = s.read_block(address, 536)
        stable = ((0, 4), (0x18, 8), (0x74, 4), (0xF0, 64), (0x180, 16), (0x1AC, 4))
        if (
            s.read_block(context + 0x4338, 16) != header
            or any(after[o : o + n] != raw[o : o + n] for o, n in stable)
            or s.read_block(pointer, columns * 104) != column_data
        ):
            raise GuiObservationChanged("GUI table changed during observation")
        s.assert_identity()
        return {
            "id": expected_id,
            "address": address,
            "columns": cells,
            "outer": outer,
            "clip": clip,
            "row_height": row_height,
        }


@dataclass(frozen=True)
class StockItem:
    uid: int
    type_id: int
    name: str
    plus: int
    gem1: int
    gem2: int
    bound: bool
    quantity: int
    slot: int
    price: int | None = None
    category: str | None = None

    def key(self):
        if not self.category:
            raise ValueError("Item category has not been matched to the market")
        return ItemKey(
            self.category,
            quality(self.type_id) if 100000 <= self.type_id < 600000 else "—",
            self.plus,
            (socket_name(self.gem1), socket_name(self.gem2)),
        )


def trade_silver(text, *, accepted=False, locked=False):
    # The pinned Accept Trade renderer (10fa50..10fa6d) writes this exact
    # zero/checkmark string when it locks an untouched own gold field.
    if text == "0 \u2714" and accepted and locked:
        return 0
    if text and text.isascii() and text.isdecimal():
        return int(text)
    raise ValueError("Unverified trade silver fields")


class MerchantMemory:
    def __init__(self, observer, *, definitions=None):
        self.observer = observer
        self.s = observer.adapter
        self.gui = GuiReader(self.s)
        self.base = self.gui.base
        self.player = PlayerLayout.model_validate(
            yaml.safe_load(
                Path("profiles/classic-1074-player-candidate.yaml").read_text()
            )
        )
        self.inventory = MemoryInventoryReader(
            self.s,
            self.player,
            InventoryLayout.model_validate(
                yaml.safe_load(
                    Path("profiles/classic-1074-inventory-candidate.yaml").read_text()
                )
            ),
        )
        self.definitions = definitions or {}

    @classmethod
    def for_session(cls, session, character, *, definitions=None):
        """Explicit 1078 closed-modal stock observation with no input surface."""
        from types import SimpleNamespace
        from conquest.memory_build_layout import read_build_layout
        from conquest.memory_inventory import MemoryInventoryReader

        if not hasattr(session, "read_block"):
            session = SimpleNamespace(
                expected_sha256=session.expected_sha256,
                modules=session.modules,
                identity=session.identity,
                read=session.read,
                read_block=session.read,
                assert_identity=session.assert_identity,
                viewport_size=getattr(session, "viewport_size", None),
            )
        layout = read_build_layout(session)
        if layout.expected_sha256 == CLIENT_SHA256:
            raise ValueError("Use the default MerchantMemory constructor for 1074")
        self = cls.__new__(cls)
        self.observer = SimpleNamespace(character=character, adapter=session)
        self.s = session
        self.layout = layout
        self.gui = GuiReader.for_session(session)
        self.base = self.gui.base
        self.player = None
        self.inventory = MemoryInventoryReader.for_session(session)
        self.definitions = definitions or {}
        self._closed_modal_only = True
        return self

    @classmethod
    def for_observer(cls, observer, *, definitions=None):
        from conquest.memory_build_layout import read_build_layout

        layout = read_build_layout(observer.adapter)
        if layout.expected_sha256 == CLIENT_SHA256:
            return cls(observer, definitions=definitions)
        return cls.for_session(
            observer.adapter, observer.character, definitions=definitions
        )

    def item(self, ptr, slot, booth=False):
        raw = self.s.read_block(ptr, 0xA0)
        d = item_details(self.s, ptr, self.base)
        equipment = 100000 <= d["type_id"] < 600000
        # Equipment +0x62 is DURABILITY, not a transferable item count.
        quantity = 1 if equipment else struct.unpack_from("<H", raw, 0x62)[0]
        if quantity <= 0 or d["plus"] is None:
            raise ValueError("Invalid merchant item count or plus")
        price = struct.unpack_from("<I", raw, 0x9C)[0] if booth else None
        if booth and not 1 <= price <= 2_147_483_647:
            raise ValueError("Invalid booth price")
        if self.s.read_block(ptr, 0xA0) != raw:
            raise ValueError("Merchant item changed")
        return StockItem(
            d["uid"],
            d["type_id"],
            d["name"],
            d["plus"],
            d["gem1"],
            d["gem2"],
            bool(raw[0x44] & 1),
            quantity,
            slot,
            price,
            self.definitions.get(d["type_id"]),
        )

    def booth_pointer(self, slot, uid):
        if getattr(self, "_closed_modal_only", False):
            raise ValueError("1078 booth input targeting is not qualified")
        life = read_life(self.s, self.observer.health_layout, self.observer.character)
        pointers, _ = deque_items(self.s, life.object_address + 0x3468, 32)
        if (
            not 0 <= slot < len(pointers)
            or self.item(pointers[slot], slot, True).uid != uid
        ):
            raise ValueError("Booth item identity changed before checking its control")
        return pointers[slot]

    def read_travel(self, *, max_seconds=3):
        if getattr(self, "_closed_modal_only", False):
            raise ValueError("1078 merchant travel observation is not qualified")
        """Transit evidence only; never a stock, capacity or trade snapshot."""
        started = time.monotonic()
        s = self.s
        server = s.read_block(self.base + 0x697860, 64)
        if server.split(b"\0")[0] != b"Classic_US":
            raise ValueError("Merchant is not on the verified America server")
        life = read_life(s, self.observer.health_layout, self.observer.character)
        if (
            life.dead_candidate
            or life.current_hp <= 0
            or life.map_id not in (1002, 1036)
        ):
            raise ValueError(
                "Market travel requires a living merchant in a supported town"
            )
        wrapper = resolve_player(s, self.player)
        silver_address = wrapper["object"] + self.inventory.layout.silver
        silver = unpack(s, silver_address, "<I")[0]
        models = [self.gui.model(14, 0x5CB328), self.gui.model(15, 0x5C4F30)]
        flags = [unpack(s, p + 12, "<B")[0] for p in models]
        windows = self.gui.windows()
        fresh = read_life(s, self.observer.health_layout, self.observer.character)
        if (
            fresh.object_address != life.object_address
            or fresh.dead_candidate
            or fresh.current_hp <= 0
            or resolve_player(s, self.player) != wrapper
            or s.read_block(self.base + 0x697860, 64) != server
        ):
            raise ValueError("Merchant transit identity or life changed")
        if (
            fresh.map_id != life.map_id
            or fresh.position != life.position
            or unpack(s, silver_address, "<I")[0] != silver
            or [unpack(s, p + 12, "<B")[0] for p in models] != flags
        ):
            raise TransitObservationChanged("Merchant transit observation changed")
        s.assert_identity()
        if time.monotonic() - started > max_seconds:
            raise ValueError("Merchant transit observation expired")
        return {
            "character": self.observer.character,
            "identity": s.identity,
            "timestamp": time.time(),
            "observation": "travel_only",
            "map_id": fresh.map_id,
            "position": list(fresh.position),
            "hp": fresh.current_hp,
            "silver": silver,
            "trade": bool(flags[0]),
            "request": bool(flags[1]),
            "windows": windows,
        }

    def _read_closed_1078(self, *, max_seconds):
        """Reuse the normal stock decoder, but never decode an open trade."""
        from conquest.memory_life import MemoryLifeReader
        from conquest.addressing import resolve_object

        started = time.monotonic()
        s = self.s
        layout = self.layout
        s.assert_identity()
        life_reader = MemoryLifeReader.for_session(s, self.observer.character)
        life = life_reader.read()
        actor = life.object_address
        if life.dead_candidate or life.current_hp <= 0 or life.map_id != 1036:
            raise ValueError(
                "Merchant must be alive in Market for closed stock observation"
            )
        server = s.read_block(self.base + layout.merchant_server_rva, 64)
        if server.split(b"\0", 1)[0] != b"Classic_US":
            raise ValueError("Merchant is not on the verified America server")
        wrapper = resolve_object(
            s,
            expected_sha256=layout.expected_sha256,
            module="imconquer.exe",
            root_rva=self.inventory.layout.owner_root_rva,
            pointer_offsets=self.inventory.layout.owner_pointer_offsets,
            vtable_rva=self.inventory.layout.owner_vtable_rva,
        )
        inv = self.inventory.read()
        inv_ptrs, inv_header = deque_items(
            s, wrapper + self.inventory.layout.deque_map, 40
        )
        stock = [self.item(pointer, index) for index, pointer in enumerate(inv_ptrs)]
        if [item.uid for item in stock] != [item.uid for item in inv.items]:
            raise ValueError("Inventory identities changed")
        trade_model = self.gui.model(14, layout.merchant_trade_vtable_rva)
        request_model = self.gui.model(15, layout.merchant_confirm_vtable_rva)
        # No acceptance/silver semantics are read while a modal is live.
        if (
            s.read_block(trade_model + 12, 1) != b"\0"
            or s.read_block(request_model + 12, 1) != b"\0"
        ):
            raise ValueError("Merchant trade or request must be closed")
        booth_ptrs, booth_header = deque_items(
            s, actor + layout.merchant_booth_offset, 32
        )
        booth = [
            self.item(pointer, index, True) for index, pointer in enumerate(booth_ptrs)
        ]
        model = self.gui.model(25, layout.merchant_booth_vtable_rva)
        model_raw = s.read_block(model, 0x58)
        own_uid = character_uid(s, self.base, actor, layout=layout)
        own_booth_uid = unpack(s, actor + layout.merchant_own_booth_offset, "<I")[0]
        booth_open = bool(model_raw[12])
        if booth_open and (
            not own_booth_uid
            or struct.unpack_from("<I", model_raw, 0x4C)[0] != own_booth_uid
        ):
            raise ValueError("Displayed booth is not this merchant’s booth")
        if not own_booth_uid and booth_ptrs:
            raise ValueError("Booth items have no owned booth")
        final_inventory = self.inventory.read()
        final_booth_ptrs, final_booth_header = deque_items(
            s, actor + layout.merchant_booth_offset, 32
        )
        final_booth = [
            self.item(pointer, index, True)
            for index, pointer in enumerate(final_booth_ptrs)
        ]
        final_stock = [
            self.item(pointer, index) for index, pointer in enumerate(inv_ptrs)
        ]
        if (
            s.read_block(wrapper + self.inventory.layout.deque_map, 32) != inv_header
            or final_booth_header != booth_header
            or final_booth_ptrs != booth_ptrs
            or final_booth != booth
            or final_stock != stock
            or final_inventory.items != inv.items
            or final_inventory.silver != inv.silver
            or final_inventory.capacity != inv.capacity
        ):
            raise ValueError("Merchant owned stock changed during observation")
        if len({item.uid for item in stock + booth}) != len(stock) + len(booth):
            raise ValueError("Item appears in both booth and inventory")
        if len(stock) + len(booth) > inv.capacity:
            raise ValueError("Merchant owned stock exceeds qualified combined capacity")
        windows = self.gui.windows()
        if (
            self.gui.model(14, layout.merchant_trade_vtable_rva) != trade_model
            or self.gui.model(15, layout.merchant_confirm_vtable_rva) != request_model
            or s.read_block(trade_model + 12, 1) != b"\0"
            or s.read_block(request_model + 12, 1) != b"\0"
            or s.read_block(self.base + layout.merchant_server_rva, 64) != server
        ):
            raise ValueError("Merchant modal or server changed during observation")
        assert_booth_stable(s, actor, model, own_booth_uid, model_raw, layout=layout)
        fresh = MemoryLifeReader.for_session(s, self.observer.character).read()
        final_wrapper = resolve_object(
            s,
            expected_sha256=layout.expected_sha256,
            module="imconquer.exe",
            root_rva=self.inventory.layout.owner_root_rva,
            pointer_offsets=self.inventory.layout.owner_pointer_offsets,
            vtable_rva=self.inventory.layout.owner_vtable_rva,
        )
        if (
            fresh.object_address != actor
            or fresh.map_id != life.map_id
            or fresh.position != life.position
            or fresh.dead_candidate
            or fresh.current_hp <= 0
            or character_uid(s, self.base, actor, layout=layout) != own_uid
            or final_wrapper != wrapper
            or time.monotonic() - started > max_seconds
        ):
            raise ValueError("Merchant observation expired or identity changed")
        s.assert_identity()
        return {
            "character": self.observer.character,
            "character_uid": own_uid,
            "identity": s.identity,
            "timestamp": time.time(),
            "server": "America",
            "map_id": life.map_id,
            "position": list(life.position),
            "hp": fresh.current_hp,
            "capacity": inv.capacity,
            "silver": inv.silver,
            "owned_free_slots": inv.capacity - len(stock) - len(booth),
            "inventory": [asdict(item) for item in stock],
            "booth": [asdict(item) for item in booth],
            "own_booth_uid": own_booth_uid,
            "booth_open": booth_open,
            "trade": None,
            "request": None,
            "windows": windows,
            "source": "read_only_memory",
            "client_sha256": layout.expected_sha256,
            "observation_only": True,
            "capabilities": {
                "read_inventory": True,
                "read_owned_booth": True,
                "read_capacity": True,
                "automatic_input": False,
                "refill_input": False,
            },
        }

    def read(self, *, max_seconds=3, recovery=False, farmer_preflight=False):
        if getattr(self, "_closed_modal_only", False):
            if recovery or farmer_preflight:
                raise ValueError("1078 merchant recovery/preflight is not qualified")
            return self._read_closed_1078(max_seconds=max_seconds)
        started = time.monotonic()
        s = self.s
        server_raw = s.read_block(self.base + 0x697860, 64)
        if server_raw.split(b"\0")[0] != b"Classic_US":
            raise ValueError("Merchant is not on the verified America server")
        life = read_life(s, self.observer.health_layout, self.observer.character)
        allowed_maps = (1002, 1036) if recovery else (1036,)
        if farmer_preflight:
            if self.observer.character != farmer_name():
                raise ValueError("Town delivery preflight is restricted to the farmer")
            allowed_maps = (1002, 1011, 1036)
        if (
            life.dead_candidate
            or life.current_hp <= 0
            or life.map_id not in allowed_maps
        ):
            raise ValueError("Merchant must be alive on the Market map")
        actor = life.object_address
        inv = self.inventory.read()
        wrapper = resolve_player(s, self.player)["object"]
        inv_ptrs, inv_header = deque_items(s, wrapper + 0xB88, 40)
        stock = [self.item(p, i) for i, p in enumerate(inv_ptrs)]
        if [i.uid for i in stock] != [i.uid for i in inv.items]:
            raise ValueError("Inventory identities changed")
        booth_ptrs, booth_header = deque_items(s, actor + 0x3468, 32)
        booth = [self.item(p, i, True) for i, p in enumerate(booth_ptrs)]
        model = self.gui.model(25, 0x5C27F8)
        own_uid = character_uid(s, self.base, actor)
        own_booth_uid = unpack(s, actor + 0x3258, "<I")[0]
        model_raw = s.read_block(model, 0x58)
        booth_open = bool(model_raw[12])
        if booth_open and (
            not own_booth_uid
            or struct.unpack_from("<I", model_raw, 0x4C)[0] != own_booth_uid
        ):
            raise ValueError("Displayed booth is not this merchant’s booth")
        trade_model = self.gui.model(14, 0x5CB328)
        trade_raw = s.read_block(trade_model, 0x9A)
        confirmation_model = self.gui.model(15, 0x5C4F30)
        request_active = bool(unpack(s, confirmation_model + 12, "<B")[0])
        trade = None
        if trade_raw[12]:
            own_ptrs, own_header = deque_items(s, actor + 0xF28, 20)
            other_ptrs, other_header = deque_items(s, actor + 0xF50, 20)
            own = [self.item(p, i) for i, p in enumerate(own_ptrs)]
            other = [self.item(p, i) for i, p in enumerate(other_ptrs)]
            participant = string(s, actor + 0xF88, 63)
            participant_uid = unpack(s, actor + 0xF84, "<I")[0]
            # The renderer's editable silver string is authoritative; never
            # infer zero just because no gold has left inventory yet.
            own_silver_text = string(s, trade_model + 0x78, 32)
            other_silver_text = string(s, trade_model + 0x58, 32)
            own_silver = trade_silver(
                own_silver_text,
                accepted=bool(trade_raw[0x98]),
                locked=bool(trade_raw[0x54]),
            )
            other_silver = trade_silver(other_silver_text)
            trade = {
                "participant": participant,
                "participant_uid": participant_uid,
                "own_items": [asdict(i) for i in own],
                "items": [asdict(i) for i in other],
                "own_silver": own_silver,
                "other_silver": other_silver,
                "accepted": bool(trade_raw[0x98]),
                "other_accepted": bool(trade_raw[0x99]),
            }
            if (
                s.read_block(actor + 0xF28, 32) != own_header
                or s.read_block(actor + 0xF50, 32) != other_header
                or s.read_block(trade_model, 0x9A) != trade_raw
            ):
                raise ValueError("Trade changed during observation")
        request = None
        if request_active:
            title = string(s, confirmation_model + 0x48)
            if title == "Trade###Confirm":
                request = {
                    "participant": string(s, actor + 0xFD8, 63),
                    "message": string(s, confirmation_model + 0x68, 256),
                }
                if (
                    request["message"]
                    != f"{request['participant']} wishes to trade with you."
                ):
                    raise ValueError(
                        "Incoming trade participant and confirmation disagree"
                    )
                from conquest.merchants.request_identity import participant_uid

                request["participant_uid"] = participant_uid(
                    self.observer, request["participant"]
                )
                if (
                    not unpack(s, confirmation_model + 12, "<B")[0]
                    or string(s, confirmation_model + 0x48) != title
                    or string(s, actor + 0xFD8, 63) != request["participant"]
                    or string(s, confirmation_model + 0x68, 256) != request["message"]
                ):
                    raise ValueError(
                        "Incoming trade request changed during identity resolution"
                    )
        final_inventory = self.inventory.read()
        if (
            s.read_block(wrapper + 0xB88, 32) != inv_header
            or s.read_block(actor + 0x3468, 32) != booth_header
            or final_inventory.items != inv.items
            or final_inventory.silver != inv.silver
        ):
            raise ValueError("Merchant inventory changed during observation")
        uids = [i.uid for i in stock + booth]
        if len(set(uids)) != len(uids):
            raise ValueError("Item appears in both booth and inventory")
        windows = self.gui.windows()
        s.assert_identity()
        if time.monotonic() - started > max_seconds:
            raise ValueError("Merchant observation expired during GUI sampling")
        if character_uid(s, self.base, actor) != own_uid:
            raise ValueError("Character UID changed during observation")
        assert_booth_stable(s, actor, model, own_booth_uid, model_raw)
        # Input guards must not combine stock/GUI sampled at one position with
        # a later position reached while the snapshot was being assembled.
        fresh = read_life(s, self.observer.health_layout, self.observer.character)
        if (
            fresh.object_address != actor
            or fresh.map_id != life.map_id
            or fresh.dead_candidate
            or fresh.current_hp <= 0
            or s.read_block(self.base + 0x697860, 64) != server_raw
            or time.monotonic() - started > max_seconds
        ):
            raise ValueError("Merchant observation expired or identity changed")
        if fresh.position != life.position:
            raise TransitObservationChanged(
                "Merchant position changed during observation"
            )
        snapshot = {
            "character": self.observer.character,
            "character_uid": own_uid,
            "identity": s.identity,
            "timestamp": time.time(),
            "server": "America",
            "map_id": life.map_id,
            "position": list(fresh.position),
            "hp": fresh.current_hp,
            "capacity": inv.capacity,
            "silver": inv.silver,
            "inventory": [asdict(i) for i in stock],
            "booth": [asdict(i) for i in booth],
            "own_booth_uid": own_booth_uid,
            "booth_open": booth_open,
            "trade": trade,
            "request": request,
            "windows": windows,
        }
        from conquest.character_context import merchant_context, current, registry

        # Market trades and town preflight use the same verified farmer identity.
        context = (
            current()
            if self.observer.character == farmer_name()
            else merchant_context(self.observer.character)
        )
        if context:
            context.verify(snapshot)
            if context.profile.character_uid is None:
                registry().bind(
                    context.profile.id,
                    snapshot["character"],
                    snapshot["server"],
                    own_uid,
                )
        return snapshot
