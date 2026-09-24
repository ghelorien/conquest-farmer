import struct
import pytest
from conquest.item_definitions import read_definition, item_hash, MANAGER_RVA
from conquest.memory_life import CLIENT_SHA256
from conquest.valuables import DRAGONBALL_NAMES


class Memory:
    expected_sha256 = CLIENT_SHA256
    modules = [{"name": "ImConquer.exe", "base": 0x140000000, "size": 0x800000}]

    def __init__(self, kind, name):
        self.blocks = {}
        self.checks = 0
        self.root = 0x140000000 + MANAGER_RVA
        self.bucket = 0x200000 + (item_hash(kind) & 15) * 16
        self.node = 0x300000
        self.blocks[self.root] = struct.pack(
            "<10Q", 0x1405CF590, 1, 0, 0x400000, 1, 0x200000, 0x200100, 0x200100, 15, 16
        )
        self.blocks[self.bucket] = struct.pack("<2Q", self.node, self.node)
        raw = bytearray(0x40)
        struct.pack_into("<I", raw, 0x10, kind)
        struct.pack_into("<I", raw, 0x18, kind)
        encoded = name.encode()
        if len(encoded) <= 15:
            raw[0x20 : 0x20 + len(encoded)] = encoded
        else:
            struct.pack_into("<Q", raw, 0x20, 0x500000)
            self.blocks[0x500000] = encoded
        struct.pack_into("<2Q", raw, 0x30, len(encoded), max(15, len(encoded)))
        self.blocks[self.node] = bytes(raw)

    def read_block(self, address, size):
        result = self.blocks[address]
        assert len(result) == size
        return result

    def assert_identity(self):
        self.checks += 1


@pytest.mark.parametrize("kind,name", DRAGONBALL_NAMES.items())
def test_reads_every_qualified_definition(kind, name):
    memory = Memory(kind, name)
    assert read_definition(memory, kind) == {
        "type_id": kind,
        "name": name,
        "address": memory.node + 0x18,
    }
    assert memory.checks == 2


def test_wrong_embedded_id_is_not_a_definition():
    m = Memory(2000031, "1-StarDragonBall")
    raw = bytearray(m.blocks[m.node])
    struct.pack_into("<I", raw, 0x18, 2000032)
    m.blocks[m.node] = bytes(raw)
    with pytest.raises(ValueError, match="identity"):
        read_definition(m, 2000031)


def test_changed_table_cannot_qualify():
    m = Memory(1088000, "DragonBall")
    read = m.read_block
    seen = []

    def unstable(address, size):
        value = read(address, size)
        if address == m.root:
            seen.append(1)
            if len(seen) > 1:
                return bytes(size)
        return value

    m.read_block = unstable
    with pytest.raises(ValueError, match="changed"):
        read_definition(m, 1088000)


def test_invalid_bucket_capacity_and_wrong_build():
    m = Memory(1088000, "DragonBall")
    raw = bytearray(m.blocks[m.root])
    struct.pack_into("<Q", raw, 0x48, 17)
    m.blocks[m.root] = bytes(raw)
    with pytest.raises(ValueError, match="table"):
        read_definition(m, 1088000)
    m.expected_sha256 = "other"
    with pytest.raises(ValueError, match="build"):
        read_definition(m, 1088000)
