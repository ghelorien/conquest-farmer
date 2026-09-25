import struct
from types import SimpleNamespace as NS
import pytest
from conquest import combat_ranges
from conquest.memory_build_layout import CLIENT_SHA256_1078, READ_LAYOUTS

LAYOUT = READ_LAYOUTS[CLIENT_SHA256_1078]
SKILLS = LAYOUT.learned_skills_offset


def fixture(monkeypatch):
    base = 0x140000000
    actor = 0x100000
    bow_address = 0x200000
    array = 0x300000
    skill_address = 0x400000
    bow = bytearray(0x74)
    struct.pack_into("<Q", bow, 0, base + LAYOUT.item_vtable_rva)
    struct.pack_into("<II", bow, 8, 123, 0)
    struct.pack_into("<I", bow, 0x10, 500035)
    struct.pack_into("<H", bow, 0x70, 12)
    skill = bytearray(0x68)
    struct.pack_into("<Q", skill, 0, base + LAYOUT.skill_vtable_rva)
    struct.pack_into("<I", skill, 0x10, 8001)
    skill[0x18:0x20] = b"Scatter\0"
    struct.pack_into("<QQ", skill, 0x28, 7, 15)
    struct.pack_into("<II", skill, 0x60, 8, 15)
    blobs = {
        actor + LAYOUT.bow_offset: struct.pack("<Q", bow_address),
        bow_address: bow,
        actor + SKILLS: struct.pack("<3Q", array, array + 16, array + 16),
        array: struct.pack("<2Q", skill_address, skill_address - 16),
        skill_address: skill,
    }
    session = NS(
        expected_sha256=CLIENT_SHA256_1078,
        modules=[{"name": "ImConquer.exe", "base": base}],
        read_block=lambda address, size: bytes(blobs[address][:size]),
        assert_identity=lambda: None,
    )
    life = NS(object_address=actor, dead_candidate=False)
    # A 1078 observer supplies its own exact-build life read.
    observer = NS(adapter=session, character="Parasite", read_life=lambda: life)
    return observer, blobs, skill


def test_range_and_aim_distance_are_separate_learned_memory_fields(monkeypatch):
    observer, _, _ = fixture(monkeypatch)
    result = combat_ranges.read_combat_ranges(observer)
    assert result["bow"]["range"] == 12
    assert result["scatter"] == {
        "type_id": 8001,
        "level": 0,
        "range": 8,
        "distance": 15,
    }


def test_high_level_bow_range_is_read_and_clamped_to_route_limit(monkeypatch):
    observer, blobs, _ = fixture(monkeypatch)
    struct.pack_into("<H", blobs[0x200000], 0x70, 21)
    ranges = combat_ranges.read_combat_ranges(observer)
    assert ranges["bow"]["range"] == 21
    settings = combat_ranges.route_combat_settings(
        NS(attack_range_tiles=20, jump_scatter=True), ranges
    )
    assert settings["single_attack_range_tiles"] == 20


def test_out_of_bounds_bow_range_is_rejected(monkeypatch):
    observer, blobs, _ = fixture(monkeypatch)
    struct.pack_into("<H", blobs[0x200000], 0x70, 33)
    with pytest.raises(ValueError, match="bow range is invalid"):
        combat_ranges.read_combat_ranges(observer)


@pytest.mark.parametrize("header", [(0, 0, 0), (0x300000, 0x300000, 0x300010)])
def test_empty_learned_skills_allow_only_single_bow_attacks(monkeypatch, header):
    observer, blobs, _ = fixture(monkeypatch)
    blobs[0x100000 + SKILLS] = struct.pack("<3Q", *header)
    ranges = combat_ranges.read_combat_ranges(observer, require_scatter=False)
    assert ranges["scatter"] is None and ranges["bow"]["range"] == 12
    with pytest.raises(ValueError, match="learned Scatter"):
        combat_ranges.read_combat_ranges(observer)
    settings = combat_ranges.route_combat_settings(
        NS(attack_range_tiles=15, jump_scatter=True), ranges
    )
    assert (
        settings["attack_button"] == "left"
        and not settings["adaptive_scatter"]
        and not settings["jump_scatter"]
    )
    assert settings["attack_range_tiles"] == settings["single_attack_range_tiles"] == 12


def test_existing_scatter_route_settings_are_preserved(monkeypatch):
    observer, _, _ = fixture(monkeypatch)
    ranges = combat_ranges.read_combat_ranges(observer, require_scatter=False)
    settings = combat_ranges.route_combat_settings(
        NS(attack_range_tiles=15, jump_scatter=True), ranges
    )
    assert (
        settings["attack_button"] == "right"
        and settings["adaptive_scatter"]
        and settings["jump_scatter"]
    )
    assert (
        settings["attack_range_tiles"] == 8
        and settings["single_attack_range_tiles"] == 12
    )


@pytest.mark.parametrize(
    "header",
    [(0, 0, 16), (0x300000, 0x2FFFF0, 0x300000), (0x300000, 0x300000, 0x300001)],
)
def test_bad_empty_vector_is_not_treated_as_an_unlearned_skill(monkeypatch, header):
    observer, blobs, _ = fixture(monkeypatch)
    blobs[0x100000 + SKILLS] = struct.pack("<3Q", *header)
    with pytest.raises(ValueError, match="vector is invalid"):
        combat_ranges.read_combat_ranges(observer, require_scatter=False)


def test_skill_learned_during_empty_observation_requires_retry(monkeypatch):
    observer, blobs, _ = fixture(monkeypatch)
    header = 0x100000 + SKILLS
    blobs[header] = bytes(24)
    read = observer.adapter.read_block
    calls = [0]

    def changing(address, size):
        if address == header:
            calls[0] += 1
            if calls[0] > 1:
                blobs[header] = struct.pack("<3Q", 0x300000, 0x300010, 0x300010)
        return read(address, size)

    observer.adapter.read_block = changing
    with pytest.raises(ValueError, match="identity changed"):
        combat_ranges.read_combat_ranges(observer, require_scatter=False)


@pytest.mark.parametrize("level", [0, 4, 5, 6, 10, 255, 65535, 0xFFFFFFFF])
def test_scatter_rank_never_rejects_an_otherwise_valid_learned_skill(
    monkeypatch, level
):
    observer, _, skill = fixture(monkeypatch)
    struct.pack_into("<I", skill, 0x48, level)
    struct.pack_into("<II", skill, 0x60, 12, 20)
    result = combat_ranges.read_combat_ranges(observer)
    assert result["scatter"] == {
        "type_id": 8001,
        "level": level,
        "range": 12,
        "distance": 20,
    }


@pytest.mark.parametrize(
    "offset,value", [(0x10, 8002), (0x60, 0), (0x60, 16), (0x64, 99)]
)
def test_unlearned_changed_or_invalid_skill_does_not_authorize_a_range(
    monkeypatch, offset, value
):
    observer, _, skill = fixture(monkeypatch)
    struct.pack_into("<I", skill, offset, value)
    with pytest.raises(ValueError):
        combat_ranges.read_combat_ranges(observer)


def test_swapped_bow_during_read_is_rejected(monkeypatch):
    observer, blobs, _ = fixture(monkeypatch)
    read = observer.adapter.read_block
    calls = [0]

    def changing(address, size):
        if address == 0x200000:
            calls[0] += 1
            if calls[0] > 1:
                struct.pack_into("<I", blobs[address], 8, 456)
        return read(address, size)

    observer.adapter.read_block = changing
    with pytest.raises(ValueError, match="identity changed"):
        combat_ranges.read_combat_ranges(observer)


def test_rank_up_mid_read_requires_a_fresh_observation(monkeypatch):
    observer, _, skill = fixture(monkeypatch)
    read = observer.adapter.read_block
    calls = [0]

    def changing(address, size):
        if address == 0x400000:
            calls[0] += 1
            if calls[0] > 1:
                struct.pack_into("<I", skill, 0x48, 6)
        return read(address, size)

    observer.adapter.read_block = changing
    with pytest.raises(ValueError, match="changed during range observation"):
        combat_ranges.read_combat_ranges(observer)
    assert combat_ranges.read_combat_ranges(observer)["scatter"]["level"] == 6
