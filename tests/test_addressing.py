import struct

import pytest

from conquest.addressing import PlayerLayout, resolve_player


class Session:
    expected_sha256 = "a" * 64
    modules = [{"name": "client.exe", "base": 0x100000, "size": 0x10000}]

    def __init__(self):
        self.memory = {0x100100: 0x200000, 0x200000: 0x300000, 0x300000: 0x100200}
        self.reads = 0
        self.change_on = None

    def assert_identity(self):
        pass

    def read(self, address, size):
        self.reads += 1
        if self.reads == self.change_on:
            self.memory[0x100100] = 0x400000
        return struct.pack("<Q", self.memory[address])


@pytest.fixture
def layout():
    return PlayerLayout(
        expected_sha256="a" * 64,
        module="client.exe",
        root_rva=0x100,
        pointer_offsets=(0, 0),
        vtable_rva=0x200,
        name_offset=0xA4,
        max_hp_offset=0x3E0,
        position_offset=0xE8,
    )


def test_resolves_from_current_module_base(layout):
    result = resolve_player(Session(), layout)
    assert result == {
        "object": 0x300000,
        "name": 0x3000A4,
        "max_hp": 0x3003E0,
        "position": 0x3000E8,
    }


def test_relocation_and_new_heap_allocations(layout):
    session = Session()
    session.modules = [{"name": "CLIENT.EXE", "base": 0x400000, "size": 0x10000}]
    session.memory = {0x400100: 0x500000, 0x500000: 0x600000, 0x600000: 0x400200}
    assert resolve_player(session, layout)["object"] == 0x600000


def test_pointer_changes_are_not_cached(layout):
    session = Session()
    assert resolve_player(session, layout)["object"] == 0x300000
    session.memory.update({0x200000: 0x700000, 0x700000: 0x100200})
    assert resolve_player(session, layout)["object"] == 0x700000


@pytest.mark.parametrize("value", [0, 5, 0xFFFF800000000000])
def test_bad_pointer_rejected(layout, value):
    session = Session()
    session.memory[0x100100] = value
    with pytest.raises(ValueError, match="Pointer"):
        resolve_player(session, layout)


def test_wrong_object_type_rejected(layout):
    session = Session()
    session.memory[0x300000] = 0x100300
    with pytest.raises(ValueError, match="object type"):
        resolve_player(session, layout)


def test_mid_resolution_pointer_change_rejected(layout):
    session = Session()
    session.change_on = 4
    with pytest.raises(ValueError, match="path changed"):
        resolve_player(session, layout)


def test_fingerprint_rejection_before_read(layout):
    session = Session()
    session.expected_sha256 = "b" * 64
    with pytest.raises(ValueError, match="fingerprint"):
        resolve_player(session, layout)
    assert session.reads == 0


def test_out_of_module_rva_rejected(layout):
    with pytest.raises(ValueError, match="RVA"):
        resolve_player(Session(), layout.model_copy(update={"root_rva": 0xFFFF}))
