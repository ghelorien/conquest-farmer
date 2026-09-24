"""Exact-build, read-only observations for the unselected 1078 client.

This module is deliberately not imported by the normal merchant runtime.  It
has no controller, bridge, focus, or input API.  Its small surface is useful
for qualifying a manual handoff reader without making the 1074 automation
paths interpret 1078 memory.
"""

from dataclasses import asdict, dataclass
import struct
import time
from types import MappingProxyType

from conquest.addressing import checked_address
from conquest.memory_health import decode_attribute


CLIENT_SHA256_1078 = "be9dd723cad8eb9068da792b5cb8ceec0d330f08aacb8c948e6f412d1520c4e0"
MODULE_NAME = "imconquer.exe"


@dataclass(frozen=True)
class StockItem1078:
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


class ObservationUnavailable1078(ValueError):
    """A read-only sample is incomplete or changed while it was read."""


class TradeObservationReader1078:
    """Pinned manual-only reader for the fields proven on build 1078.

    It returns a canonical-shaped snapshot only after every available ownership
    collection is stable. It has no normal-runtime or input capability.
    """

    root_rva = 0x6BCEF0
    actual_vtable_rva = 0x5EA748
    wrapper_vtable_rva = 0x5EA728
    item_vtable_rva = 0x5EA9F8
    registry_rva = 0x6B8E48
    server_rva = 0x6B7FC0
    trade_vtable_rva = 0x5E6A80
    confirm_vtable_rva = 0x5E0148
    booth_vtable_rva = 0x5DD9C0
    map_rva = 0x6B9D44

    # These are intentionally separate from normal runtime feature flags.
    capabilities = MappingProxyType(
        {
            "read_trade_request": True,
            "read_inventory": True,
            "read_health_candidate": True,
            "manual_ownership": True,
            "automatic_input": False,
            "focus": False,
        }
    )

    def __init__(self, session, character: str):
        if getattr(session, "expected_sha256", None) != CLIENT_SHA256_1078:
            raise ObservationUnavailable1078(
                "1078 reader requires its exact executable fingerprint"
            )
        if not character or len(character.encode("utf-8")) > 63:
            raise ValueError("A selected character name is required")
        modules = [m for m in session.modules if m["name"].casefold() == MODULE_NAME]
        if len(modules) != 1:
            raise ObservationUnavailable1078("Expected exactly one ImConquer module")
        self.session, self.character, self.module = session, character, modules[0]
        self.base = self.module["base"]
        for rva in (
            self.root_rva,
            self.registry_rva,
            self.server_rva,
            self.actual_vtable_rva,
            self.wrapper_vtable_rva,
            self.item_vtable_rva,
            self.trade_vtable_rva,
            self.confirm_vtable_rva,
            self.booth_vtable_rva,
            self.map_rva,
        ):
            if not 0 <= rva < self.module["size"]:
                raise ObservationUnavailable1078(
                    "1078 candidate RVA is outside the loaded module"
                )

    def _read(self, address, size):
        address = checked_address(address, size)
        reader = getattr(self.session, "read_block", None) or self.session.read
        return reader(address, size)

    def _u8(self, address):
        return self._read(address, 1)[0]

    def _u32(self, address):
        return struct.unpack("<I", self._read(address, 4))[0]

    def _u64(self, address):
        return struct.unpack("<Q", self._read(address, 8))[0]

    def _string(self, address, maximum=128):
        raw = self._read(address, 32)
        length, capacity = struct.unpack_from("<QQ", raw, 16)
        if not 0 <= length <= maximum or not max(length, 15) <= capacity <= 4096:
            raise ObservationUnavailable1078("Invalid 1078 client string")
        value = (
            raw[:length]
            if capacity <= 15
            else self._read(struct.unpack_from("<Q", raw)[0], length)
            if length
            else b""
        )
        if self._read(address, 32) != raw:
            raise ObservationUnavailable1078("Client string changed during observation")
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ObservationUnavailable1078("Invalid UTF-8 client string") from error

    def _actual_and_wrapper(self):
        # The module pointer owns both the actual actor (+0) and the wrapper
        # (+8); each path is read again before returning an observation.
        holder = self._u64(self.base + self.root_rva)
        actual = self._u64(holder)
        wrapper = self._u64(holder + 8)
        if self._u64(actual) != self.base + self.actual_vtable_rva:
            raise ObservationUnavailable1078("1078 actual actor type changed")
        if self._u64(wrapper) != self.base + self.wrapper_vtable_rva:
            raise ObservationUnavailable1078("1078 inventory wrapper type changed")
        if (
            self._read(actual + 0x94, 64).split(b"\0", 1)[0].decode("utf-8")
            != self.character
        ):
            raise ObservationUnavailable1078(
                "Selected character differs from 1078 actor"
            )
        if (
            self._read(wrapper + 0xA4, 64).split(b"\0", 1)[0].decode("utf-8")
            != self.character
        ):
            raise ObservationUnavailable1078(
                "Selected character differs from 1078 wrapper"
            )
        return holder, actual, wrapper

    def _assert_actor_stable(self, holder, actual, wrapper):
        if (
            self._u64(self.base + self.root_rva) != holder
            or self._u64(holder) != actual
            or self._u64(holder + 8) != wrapper
            or self._u64(actual) != self.base + self.actual_vtable_rva
            or self._u64(wrapper) != self.base + self.wrapper_vtable_rva
        ):
            raise ObservationUnavailable1078(
                "1078 actor identity changed during observation"
            )

    def _model(self, key, expected_vtable):
        header = self._read(self.base + self.registry_rva, 16)
        head, count = struct.unpack("<QQ", header)
        if not 1 <= count <= 128:
            raise ObservationUnavailable1078("Invalid 1078 model registry")
        node, visited = self._u64(head + 8), set()
        while node != head:
            if node in visited or len(visited) >= count:
                raise ObservationUnavailable1078("Invalid 1078 model tree")
            visited.add(node)
            raw = self._read(node, 56)
            found = struct.unpack_from("<I", raw, 32)[0]
            if found == key:
                model = struct.unpack_from("<Q", raw, 40)[0]
                if (
                    self._u64(model) != self.base + expected_vtable
                    or self._read(node, 56) != raw
                    or self._read(self.base + self.registry_rva, 16) != header
                ):
                    raise ObservationUnavailable1078(
                        "1078 model changed during observation"
                    )
                return model
            node = struct.unpack_from("<Q", raw, 0 if key < found else 16)[0]
        raise ObservationUnavailable1078("Expected 1078 model is absent")

    def _deque(self, address, limit):
        header = self._read(address, 32)
        table, capacity, start, count = struct.unpack("<4Q", header)
        if (
            not 0 <= count <= limit
            or not 0 <= capacity <= 256
            or capacity & (capacity - 1)
            or count > capacity
        ):
            raise ObservationUnavailable1078("Invalid 1078 item deque")
        if not capacity:
            if count or start:
                raise ObservationUnavailable1078("Invalid empty 1078 item deque")
            return [], header
        entries = self._read(table, capacity * 8)
        pointers, references = [], []
        for index in range(count):
            slot = struct.unpack_from("<Q", entries, ((start + index) % capacity) * 8)[
                0
            ]
            shared = self._read(slot, 16)
            pointers.append(struct.unpack_from("<Q", shared)[0])
            references.append((slot, shared))
        if (
            len(set(pointers)) != len(pointers)
            or self._read(address, 32) != header
            or self._read(table, capacity * 8) != entries
            or any(self._read(slot, 16) != raw for slot, raw in references)
        ):
            raise ObservationUnavailable1078(
                "1078 item deque changed during observation"
            )
        return pointers, header

    def _item(self, pointer, slot, *, booth=False):
        raw = self._read(pointer, 0xA0)
        if struct.unpack_from("<Q", raw)[0] != self.base + self.item_vtable_rva:
            raise ObservationUnavailable1078("1078 item type changed")
        uid, type_id = (
            struct.unpack_from("<I", raw, 8)[0],
            struct.unpack_from("<I", raw, 0x10)[0],
        )
        length, capacity = struct.unpack_from("<QQ", raw, 0x28)
        if (
            not uid
            or not 0 < type_id < 100000000
            or not 1 <= length <= 127
            or not length <= capacity <= 1024
        ):
            raise ObservationUnavailable1078("Invalid 1078 item identity")
        name_bytes = (
            raw[0x18:0x28]
            if capacity <= 15
            else self._read(struct.unpack_from("<Q", raw, 0x18)[0], length)
        )
        try:
            name = name_bytes[:length].decode("utf-8")
        except UnicodeDecodeError as error:
            raise ObservationUnavailable1078("Invalid 1078 item name") from error
        equipment = 100000 <= type_id < 600000
        quantity = 1 if equipment else struct.unpack_from("<H", raw, 0x62)[0]
        plus = raw[0x6B]
        if quantity <= 0 or plus > 12 or self._read(pointer, 0xA0) != raw:
            raise ObservationUnavailable1078("1078 item changed during observation")
        price = struct.unpack_from("<I", raw, 0x9C)[0] if booth else None
        if booth and not 1 <= price <= 2_147_483_647:
            raise ObservationUnavailable1078("Invalid 1078 booth price")
        return StockItem1078(
            uid,
            type_id,
            name,
            plus,
            raw[0x67],
            raw[0x68],
            bool(raw[0x44] & 1),
            quantity,
            slot,
            price,
        )

    def _inventory(self, wrapper):
        pointers, header = self._deque(wrapper + 0xBB0, 40)
        independent_count = self._u64(wrapper + 0xBE0)
        if independent_count != len(pointers):
            raise ObservationUnavailable1078(
                "1078 inventory count disagrees with its deque"
            )
        stock = [self._item(pointer, slot) for slot, pointer in enumerate(pointers)]
        silver = self._u32(wrapper + 0xAB8)
        if (
            self._read(wrapper + 0xBB0, 32) != header
            or self._u64(wrapper + 0xBE0) != independent_count
        ):
            raise ObservationUnavailable1078(
                "1078 inventory changed during observation"
            )
        return stock, silver

    def _booth(self, actual):
        """Read only the fields proven by the manual 1078 listing probe."""
        model = self._model(25, self.booth_vtable_rva)
        raw = self._read(model, 0x50)
        owner = self._u32(actual + 0x32A0)
        pointers, header = self._deque(actual + 0x34B0, 32)
        open_ = bool(raw[12])
        if open_ and (not owner or struct.unpack_from("<I", raw, 0x4C)[0] != owner):
            raise ObservationUnavailable1078(
                "1078 displayed booth is not the actor's booth"
            )
        if not owner and pointers:
            raise ObservationUnavailable1078("1078 booth items have no owned booth")
        items = [
            self._item(pointer, slot, booth=True)
            for slot, pointer in enumerate(pointers)
        ]
        if (
            self._read(actual + 0x34B0, 32) != header
            or self._read(model, 0x50) != raw
            or self._u32(actual + 0x32A0) != owner
        ):
            raise ObservationUnavailable1078("1078 booth changed during observation")
        return items, owner, open_

    def _health(self, actual):
        maximum = self._u32(actual + 0x3E0)
        attributes = self._u64(actual + 0x978)
        header = self._read(attributes, 24)
        mode, count = struct.unpack_from("<II", header, 8)
        if (
            not 1 <= maximum <= 1000000
            or mode not in (0, 1, 2, 3)
            or not 1 <= count <= 1024
        ):
            raise ObservationUnavailable1078("Invalid 1078 health candidate")
        table_address = struct.unpack_from("<Q", header, 16)[0]
        table = self._read(table_address, count * 4)
        current = decode_attribute(table, mode, count, 1)
        if (
            not 0 <= current <= maximum
            or self._u64(actual + 0x978) != attributes
            or self._read(attributes, 24) != header
            or self._read(table_address, count * 4) != table
            or self._u32(actual + 0x3E0) != maximum
        ):
            raise ObservationUnavailable1078("1078 health changed during observation")
        return {"current_hp_candidate": current, "max_hp_candidate": maximum}

    def _trade(self, actual):
        model = self._model(14, self.trade_vtable_rva)
        raw = self._read(model, 0x9A)
        if not raw[12]:
            return None
        participant = self._string(actual + 0xFB0, 63)
        participant_uid = self._u32(actual + 0xFAC)
        if not participant or not participant_uid:
            raise ObservationUnavailable1078("Invalid 1078 trade participant")
        own, own_header = self._deque(actual + 0xF50, 20)
        other, other_header = self._deque(actual + 0xF78, 20)
        value = {
            "participant": participant,
            "participant_uid": participant_uid,
            "own_items": [
                asdict(self._item(ptr, slot)) for slot, ptr in enumerate(own)
            ],
            "items": [asdict(self._item(ptr, slot)) for slot, ptr in enumerate(other)],
            # These strings and the local acceptance flag are retained as
            # raw UI evidence only. Ownership settlement needs the modal and
            # exact item collections, not unqualified counterpart semantics.
            "own_silver_text": self._string(model + 0x78, 32),
            "other_silver_text": self._string(model + 0x58, 32),
            "accepted": bool(raw[0x98]),
        }
        if (
            self._read(actual + 0xF50, 32) != own_header
            or self._read(actual + 0xF78, 32) != other_header
            or self._read(model, 0x9A) != raw
        ):
            raise ObservationUnavailable1078("1078 trade changed during observation")
        return value

    def _request(self, actual):
        model = self._model(15, self.confirm_vtable_rva)
        raw = self._read(model, 0xC0)
        if not raw[12]:
            return None
        title, participant = (
            self._string(model + 0x48, 63),
            self._string(actual + 0x1000, 63),
        )
        message, participant_uid = (
            self._string(model + 0x68, 256),
            self._u32(actual + 0xFF8),
        )
        if (
            title != "Trade###Confirm"
            or not participant
            or not participant_uid
            or message != f"{participant} wishes to trade with you."
        ):
            raise ObservationUnavailable1078(
                "1078 incoming trade request disagrees with confirmation"
            )
        if self._read(model, 0xC0) != raw:
            raise ObservationUnavailable1078(
                "1078 incoming request changed during observation"
            )
        return {
            "participant": participant,
            "participant_uid": participant_uid,
            "message": message,
        }

    def read_manual_ownership(self):
        """Return a stable manual-only ownership snapshot; it never enables input."""
        started = time.monotonic()
        self.session.assert_identity()
        server = self._read(self.base + self.server_rva, 64)
        if server.split(b"\0", 1)[0] != b"Classic_US":
            raise ObservationUnavailable1078(
                "1078 server field is not the qualified America value"
            )
        holder, actual, wrapper = self._actual_and_wrapper()
        inventory, silver = self._inventory(wrapper)
        booth, own_booth_uid, booth_open = self._booth(actual)
        character_uid = self._u32(actual + 0x68)
        map_id = self._u32(self.base + self.map_rva)
        position = list(struct.unpack("<2I", self._read(actual + 0xD8, 8)))
        health = self._health(actual)
        if not character_uid:
            raise ObservationUnavailable1078("1078 character identity is unavailable")
        if len({item.uid for item in inventory + booth}) != len(inventory) + len(booth):
            raise ObservationUnavailable1078("1078 item appears in inventory and booth")
        result = {
            "reader_build": "1078-read-only-candidate",
            "character": self.character,
            "character_uid": character_uid,
            "identity": dict(self.session.identity),
            "server": "America",
            "inventory": [asdict(item) for item in inventory],
            "booth": [asdict(item) for item in booth],
            "capacity": 40,
            "silver": silver,
            "map_id": map_id,
            "position": position,
            "hp": health["current_hp_candidate"],
            "health": health,
            "own_booth_uid": own_booth_uid,
            "booth_open": booth_open,
            "trade": self._trade(actual),
            "request": self._request(actual),
            "canonical_manual_ownership": False,
            "capabilities": dict(self.capabilities),
            "timestamp": time.time(),
        }
        from conquest.merchants.manual_sessions import canonical_ownership

        canonical_ownership(result, require_closed=False)
        result["canonical_manual_ownership"] = (
            result["trade"] is None and result["request"] is None
        )
        # A closed modal is evidence too. Reject a request/trade that opened
        # after its inactive-model observation, and re-read owned collections.
        if bool(self._u8(self._model(14, self.trade_vtable_rva) + 12)) != (
            result["trade"] is not None
        ):
            raise ObservationUnavailable1078(
                "1078 trade state changed during observation"
            )
        if bool(self._u8(self._model(15, self.confirm_vtable_rva) + 12)) != (
            result["request"] is not None
        ):
            raise ObservationUnavailable1078(
                "1078 request state changed during observation"
            )
        latest_inventory, latest_silver = self._inventory(wrapper)
        latest_booth, latest_owner, latest_open = self._booth(actual)
        if (
            latest_inventory != inventory
            or latest_silver != silver
            or latest_booth != booth
            or (latest_owner, latest_open) != (own_booth_uid, booth_open)
        ):
            raise ObservationUnavailable1078(
                "1078 ownership changed during observation"
            )
        self._assert_actor_stable(holder, actual, wrapper)
        if (
            self._u32(actual + 0x68) != character_uid
            or self._u32(self.base + self.map_rva) != map_id
            or self._read(actual + 0xD8, 8) != struct.pack("<2I", *position)
            or self._read(actual + 0x94, 64).split(b"\0", 1)[0].decode("utf-8")
            != self.character
            or self._read(wrapper + 0xA4, 64).split(b"\0", 1)[0].decode("utf-8")
            != self.character
        ):
            raise ObservationUnavailable1078(
                "1078 actor evidence changed during observation"
            )
        if self._read(self.base + self.server_rva, 64) != server:
            raise ObservationUnavailable1078(
                "1078 server field changed during observation"
            )
        self.session.assert_identity()
        if time.monotonic() - started > 3:
            raise ObservationUnavailable1078("1078 observation expired")
        return result

    def read(self):
        """Compatibility alias for explicit callers; normal runtime never calls it."""
        return self.read_manual_ownership()


def open_read_only_1078(session, character):
    """Explicit factory; normal observer/controller code must not call this."""
    return TradeObservationReader1078(session, character)
