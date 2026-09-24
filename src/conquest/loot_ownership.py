"""Read system pickup rejection messages and remember rejected ground instances."""

from conquest.character_context import state_path
from dataclasses import dataclass
from pathlib import Path
import json
import struct
import time
from conquest.addressing import checked_address
from conquest.memory_build_layout import CLIENT_SHA256_1074, CLIENT_SHA256_1078

OWNERSHIP_MESSAGE = "You can`t pick up other player`s loot at the moment. Please wait."


@dataclass(frozen=True)
class SystemMessage:
    address: int
    tick: int
    text: str


def ownership_rejected(before, after):
    # Old visible messages, player chat, and unrelated errors are not rejection
    # of this pickup. The reader restricts both the queue and record to System.
    seen = set(before)
    latest = max((m.tick for m in before), default=0)
    return any(
        m not in seen and m.tick >= latest and m.text == OWNERSHIP_MESSAGE
        for m in after
    )


class SystemMessageReader:
    def __init__(self, session):
        # Both head RVAs are pinned to their exact executable fingerprints.
        # The 1078 getter at RVA 0x77c65 returns manager RVA 0x6b8e90;
        # its System-channel tree, deque and records retain the checked layout.
        heads = {CLIENT_SHA256_1074: 0x698740, CLIENT_SHA256_1078: 0x6B8ED0}
        try:
            self.head_rva = heads[session.expected_sha256]
        except KeyError as error:
            raise ValueError("Unqualified system-message client") from error
        self.session = session
        self.base = next(
            m["base"] for m in session.modules if m["name"].lower() == "imconquer.exe"
        )

    def read(self, *, max_age=0.5):
        s = self.session
        s.assert_identity()
        started = time.monotonic()
        checks = []

        def read(address, size):
            raw = s.read_block(checked_address(address, size), size)
            if len(raw) != size:
                raise ValueError("Incomplete system-message memory")
            checks.append((address, raw))
            return raw

        # Renderer ef7f8 calls 76590, returning module+698700. Channel lookup
        # 196e20 walks the tree at manager+40, key node+20, deque node+28.
        head = struct.unpack("<Q", read(self.base + self.head_rva, 8))[0]
        node = struct.unpack("<Q", read(head + 8, 8))[0]
        visited = set()
        found = None
        for _ in range(64):
            if node == head:
                break
            if node in visited:
                raise ValueError("System-channel tree cycle")
            visited.add(node)
            raw = read(node, 0x22)
            if raw[0x19]:
                break
            key = struct.unpack_from("<H", raw, 0x20)[0]
            if key == 2005:
                found = node
                break
            node = struct.unpack_from("<Q", raw, 0 if key > 2005 else 0x10)[0]
        else:
            raise ValueError("System-channel tree exceeds bounds")
        messages = []
        if found is not None:
            table, capacity, first, count = struct.unpack("<4Q", read(found + 0x30, 32))
            if (
                not 0 <= count <= 200
                or not max(count, 1) <= capacity <= 2048
                or capacity & (capacity - 1)
            ):
                raise ValueError("System-message deque is invalid")
            # Read a bounded tail. The client retains up to 200 messages per channel.
            for index in range(max(0, count - 16), count):
                block = struct.unpack(
                    "<Q", read(table + ((first + index) & (capacity - 1)) * 8, 8)
                )[0]
                address = struct.unpack("<Q", read(block, 8))[0]
                # Renderer efa95/efab9 consumes text +68/+78/+80, efb5e channel
                # +88; ef8f2 uses timestamp +a8. Ignore animation bytes +b0.
                raw = read(address + 0x68, 0x48)
                length, allocated = struct.unpack_from("<QQ", raw, 0x10)
                channel = struct.unpack_from("<H", raw, 0x20)[0]
                tick = struct.unpack_from("<Q", raw, 0x40)[0]
                if (
                    channel != 2005
                    or not 0 < length <= 2048
                    or not max(length, 15) <= allocated <= 4096
                ):
                    raise ValueError(
                        "System-message record differs from qualified layout"
                    )
                text = (
                    raw[:16]
                    if allocated <= 15
                    else read(struct.unpack_from("<Q", raw)[0], length)
                )[:length].decode("utf-8")
                messages.append(SystemMessage(address, tick, text))
        if any(s.read_block(address, len(raw)) != raw for address, raw in checks):
            raise ValueError("System messages changed during observation")
        s.assert_identity()
        if time.monotonic() - started > max_age:
            raise ValueError("System-message observation expired")
        return tuple(messages)


class LootOwnership:
    def __init__(self, session, path=Path(state_path(".runtime/loot-ownership.json"))):
        self.reader = SystemMessageReader(session)
        self.path = Path(path)
        self.identity = session.identity
        self.denied = {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if data.get("identity") == self.identity:
                self.denied = data["denied"]
        except (OSError, ValueError, KeyError):
            pass

    @staticmethod
    def key(drop, map_id):
        return json.dumps(
            [
                map_id,
                drop.uid,
                drop.object_address,
                drop.type_id,
                list(drop.position),
                drop.spawn_tick,
            ],
            separators=(",", ":"),
        )

    def blocked(self, drop, map_id):
        return self.key(drop, map_id) in self.denied

    def snapshot(self):
        return self.reader.read()

    def reject(self, drop, map_id):
        self.denied[self.key(drop, map_id)] = time.time()
        if len(self.denied) > 4096:
            self.denied = dict(
                sorted(self.denied.items(), key=lambda item: item[1])[-4096:]
            )
        from conquest.discord_notify import write_json

        write_json(self.path, {"identity": self.identity, "denied": self.denied})
