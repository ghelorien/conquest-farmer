import struct

import pytest

from conquest.addressing import PlayerLayout
from conquest.memory_health import HealthLayout, MemoryHealthReader, decode_attribute


@pytest.mark.parametrize(
    "mode,slot,encoded",
    [(0, 1, 426), (1, 14, 0x03540000), (2, 0, 213), (3, 8, 0xD5000000)],
)
def test_attribute_modes_decode_known_storage_words(mode, slot, encoded):
    table = bytearray(64)
    struct.pack_into("<I", table, slot * 4, encoded)
    assert decode_attribute(table, mode, 16, 1) == 213


@pytest.mark.parametrize(
    "mode,count,index", [(4, 16, 1), (0, 0, 1), (0, 1025, 1), (0, 16, 16), (0, 16, -1)]
)
def test_invalid_attribute_layout_is_rejected(mode, count, index):
    with pytest.raises(ValueError):
        decode_attribute(bytes(64), mode, count, index)


class Session:
    expected_sha256 = "a" * 64
    identity = {"pid": 1, "creation_time_100ns": 2}
    modules = [{"name": "client.exe", "base": 0x100000, "size": 0x10000}]

    def __init__(self):
        self.chunks = {}
        self.hook = lambda *_: None
        self.identity_checks = 0
        for address, value in [
            (0x100100, 0x200000),
            (0x200000, 0x300000),
            (0x300000, 0x100200),
            (0x300968, 0x400000),
        ]:
            self.chunks[address] = struct.pack("<Q", value)
        self.chunks[0x300094] = b"Parasite\0".ljust(64, b"\0")
        self.chunks[0x3003D0] = struct.pack("<I", 213)
        self.chunks[0x400000] = struct.pack("<QIIQ", 0x123456, 3, 16, 0x500000)
        self.set_hp(213)

    def set_hp(self, hp):
        table = bytearray(64)
        struct.pack_into("<I", table, 32, ((hp >> 8) | (hp << 24)) & 0xFFFFFFFF)
        self.chunks[0x500000] = bytes(table)

    def assert_identity(self):
        self.identity_checks += 1

    def read(self, address, size):
        self.hook(address, size)
        result = self.chunks[address]
        assert len(result) == size
        return result

    read_block = read


@pytest.fixture
def setup_reader():
    player = PlayerLayout(
        expected_sha256="a" * 64,
        module="client.exe",
        root_rva=0x100,
        pointer_offsets=(0, 0),
        vtable_rva=0x200,
        name_offset=0x94,
        max_hp_offset=0x3D0,
        position_offset=0xD8,
    )
    layout = HealthLayout(
        player=player, attribute_pointer_offset=0x968, hp_attribute_index=1
    )
    session = Session()
    return session, layout


def test_damage_and_zero_are_preserved_without_qualification(setup_reader):
    session, layout = setup_reader
    reader = MemoryHealthReader(session, layout, "Parasite")
    assert reader.read().current_hp == 213
    session.set_hp(150)
    assert reader.read().current_hp == 150
    session.set_hp(0)
    report = reader.report()
    assert report["snapshot"]["current_hp"] == 0
    assert report["snapshot"]["max_hp"] == 213
    assert not report["qualified"]
    assert not report["autonomous_actions_enabled"]


def test_hp_above_maximum_is_rejected(setup_reader):
    session, layout = setup_reader
    session.set_hp(214)
    with pytest.raises(ValueError, match="bounds"):
        MemoryHealthReader(session, layout, "Parasite").read()


def test_other_character_is_rejected(setup_reader):
    session, layout = setup_reader
    with pytest.raises(ValueError, match="Character name"):
        MemoryHealthReader(session, layout, "SomeoneElse").read()


def test_attribute_change_invalidates_entire_sample(setup_reader):
    session, layout = setup_reader
    calls = []

    def change(address, size):
        if address == 0x500000:
            calls.append(address)
            if len(calls) == 2:
                session.set_hp(150)

    session.hook = change
    with pytest.raises(ValueError, match="changed"):
        MemoryHealthReader(session, layout, "Parasite").read()


def test_pointer_change_invalidates_entire_sample(setup_reader):
    session, layout = setup_reader
    calls = []

    def change(address, size):
        if address == 0x300968:
            calls.append(address)
            if len(calls) == 2:
                session.chunks[address] = struct.pack("<Q", 0x600000)

    session.hook = change
    with pytest.raises(ValueError, match="changed"):
        MemoryHealthReader(session, layout, "Parasite").read()


def test_slow_sample_is_rejected(setup_reader):
    session, layout = setup_reader
    ticks = iter([1, 3])
    with pytest.raises(ValueError, match="expired"):
        MemoryHealthReader(
            session, layout, "Parasite", clock=lambda: next(ticks)
        ).read()


def test_restart_invalidates_sample(setup_reader):
    session, layout = setup_reader

    def check():
        if session.identity_checks == 4:
            raise ValueError("Process restarted")
        session.identity_checks += 1

    session.assert_identity = check
    with pytest.raises(ValueError, match="restarted"):
        MemoryHealthReader(session, layout, "Parasite").read()
