import struct
from dataclasses import replace
from types import SimpleNamespace

import pytest
import yaml

from conquest.memory_entities import EntityLayout, MonsterObservation
from conquest.monster_health import read_monster_health


def fixture(hp):
    layout = EntityLayout.model_validate(
        yaml.safe_load(open("profiles/classic-1078-entities-candidate.yaml"))
    )
    obj, header, table = 0x100000, 0x200000, 0x300000
    record = bytearray(max(0x988, layout.attribute_pointer_offset + 8))
    struct.pack_into("<Q", record, 0, 0x400000 + layout.monster_vtable_rva)
    struct.pack_into("<I", record, layout.id_offset, 25)
    struct.pack_into("<I", record, layout.kind_offset, 2)
    struct.pack_into("<II", record, layout.position_offset, 680, 570)
    struct.pack_into("<ii", record, layout.draw_position_offset, 650, 400)
    struct.pack_into("<I", record, layout.max_hp_offset, 81)
    struct.pack_into("<Q", record, layout.attribute_pointer_offset, header)
    h = bytearray(24)
    struct.pack_into("<IIQ", h, 8, 0, 16, table)
    t = bytearray(64)
    struct.pack_into("<I", t, 4, hp << 1)
    blocks = {obj: record, header: h, table: t}

    def read(address, size):
        for base, data in blocks.items():
            if base <= address and address + size <= base + len(data):
                return bytes(data[address - base : address - base + size])
        raise ValueError("Unmapped")

    session = SimpleNamespace(
        assert_identity=lambda: None,
        read_block=read,
        modules=[{"name": layout.module, "base": 0x400000}],
    )
    monster = MonsterObservation(
        obj, 25, "Turtledove", (680, 570), (650, 400), 81, 7, type_id=2
    )
    return session, layout, monster, blocks


@pytest.mark.parametrize("hp", [0, 30, 81])
def test_reads_zero_damaged_and_full_attribute_values(hp):
    session, layout, monster, _ = fixture(hp)
    assert read_monster_health(session, layout, monster) == hp


def test_recycled_id_and_out_of_bounds_health_are_rejected():
    session, layout, monster, _ = fixture(81)
    with pytest.raises(ValueError, match="changed"):
        read_monster_health(session, layout, replace(monster, entity_id=26))
    session, layout, monster, _ = fixture(82)
    with pytest.raises(ValueError, match="bounds"):
        read_monster_health(session, layout, monster)


def test_pointer_swap_during_read_cannot_reuse_old_health():
    session, layout, monster, blocks = fixture(81)
    original = session.read_block

    def read(address, size):
        value = original(address, size)
        if address == 0x300000:
            struct.pack_into(
                "<Q", blocks[0x100000], layout.attribute_pointer_offset, 0x400000
            )
        return value

    session.read_block = read
    with pytest.raises(ValueError, match="changed"):
        read_monster_health(session, layout, monster)


def test_camera_scroll_does_not_invalidate_actor_health():
    session, layout, monster, blocks = fixture(30)
    # The scene's draw coordinates may already lag the camera at the first read.
    struct.pack_into(
        "<ii", blocks[monster.object_address], layout.draw_position_offset, 750, 450
    )
    original = session.read_block

    def read(address, size):
        value = original(address, size)
        if address == 0x300000:
            struct.pack_into(
                "<ii",
                blocks[monster.object_address],
                layout.draw_position_offset,
                850,
                500,
            )
        return value

    session.read_block = read
    assert read_monster_health(session, layout, monster) == 30


@pytest.mark.parametrize("field", ["id_offset", "kind_offset", "position_offset"])
def test_actor_identity_or_world_movement_still_invalidates_health(field):
    session, layout, monster, blocks = fixture(30)
    original = session.read_block

    def read(address, size):
        value = original(address, size)
        if address == 0x300000:
            struct.pack_into(
                "<I", blocks[monster.object_address], getattr(layout, field), 999
            )
        return value

    session.read_block = read
    with pytest.raises(ValueError, match="changed"):
        read_monster_health(session, layout, monster)
