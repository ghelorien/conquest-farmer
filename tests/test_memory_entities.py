from pathlib import Path

import pytest
import yaml

from conquest.memory_entities import EntityLayout, MemoryEntityReader


class Memory:
    expected_sha256 = "be9dd723cad8eb9068da792b5cb8ceec0d330f08aacb8c948e6f412d1520c4e0"
    modules = [{"name": "ImConquer.exe", "base": 0x140000000, "size": 0x3000000}]
    identity = {"pid": 123, "creation_time_100ns": 456}

    def __init__(self, layout):
        p = layout
        self.calls, self.identity_checks = 0, 0
        self.mutate, self.exited = None, False
        self.read_counts = {}
        self.values = {
            0x140000000 + p.root_rva: 0x200000,
            0x200018: 0x300000,
            0x300008: 0x400000,
            0x400000: 0x140000000 + p.collection_vtable_rva,
            0x400000 + p.begin_offset: 0x500000,
            0x400000 + p.end_offset: 0x500030,
            0x400000 + p.capacity_offset: 0x500100,
        }
        for index, (address, kind, name) in enumerate(
            [
                (0x600000, 2, "Turtledove"),
                (0x700000, 0, "OtherPlayer"),
                (0x800000, 900, "Guard"),
            ]
        ):
            self.values[0x500008 + index * 16] = address
            self.values.update(
                {
                    address: 0x140000000 + p.monster_vtable_rva,
                    address + p.id_offset: 450000 + index,
                    address + p.kind_offset: kind,
                    address + p.name_offset: name,
                    address + p.position_offset: (680, 570),
                    address + p.draw_position_offset: -60,
                    address + p.draw_position_offset + 4: 800,
                    address + p.max_hp_offset: 81,
                    address + p.level_offset: 7,
                }
            )

    def request(self, operation, body):
        assert operation == "sample"
        assert 1 <= len(body["fields"]) <= 64
        self.calls += 1
        for f in body["fields"]:
            a = int(f["address"], 0)
            self.read_counts[a] = self.read_counts.get(a, 0) + 1
        if self.mutate:
            self.mutate(self)
        result = []
        for f in body["fields"]:
            value = self.values[int(f["address"], 0)]
            if f["kind"] != "utf8":
                value = list(value) if isinstance(value, tuple) else [value]
            result.append({**f, "value": value})
        return {"fields": result}

    def assert_identity(self):
        self.identity_checks += 1
        if self.exited:
            raise OSError("process exited")


@pytest.fixture
def setup():
    path = Path(__file__).parents[1] / "profiles/classic-1078-entities-candidate.yaml"
    layout = EntityLayout.model_validate(yaml.safe_load(path.read_text()))
    memory = Memory(layout)
    return memory, layout, MemoryEntityReader(memory, layout)


def test_memory_ids_positions_exclude_players_and_do_not_infer_alive(setup):
    _, _, reader = setup
    result = reader.read()
    assert len(result.monsters) == 1
    monster = result.monsters[0]
    assert (monster.entity_id, monster.name, monster.position) == (
        450000,
        "Turtledove",
        (680, 570),
    )
    assert monster.draw_position == (-60, 800)  # Offscreen actors are still observable.
    assert monster.current_hp is None and monster.alive is None
    assert reader.report()["autonomous_actions_enabled"] is False


def test_type_field_is_species_id_so_pheasants_are_included(setup):
    memory, layout, reader = setup
    memory.values[0x800000 + layout.kind_offset] = 1
    memory.values[0x800000 + layout.name_offset] = "Pheasant"
    result = reader.read()
    assert [(m.entity_id, m.type_id, m.name) for m in result.monsters] == [
        (450000, 2, "Turtledove"),
        (450002, 1, "Pheasant"),
    ]


@pytest.mark.parametrize("value", [0x4FFFF0, 0x500009, 0x600000])
def test_invalid_bounds_fail_before_object_reads(setup, value):
    memory, layout, reader = setup
    memory.values[0x400000 + layout.end_offset] = value
    with pytest.raises(ValueError, match="bounds"):
        reader.read()
    assert 0x600000 not in memory.read_counts


def test_other_client_rejected_before_read(setup):
    memory, layout, _ = setup
    memory.expected_sha256 = "b" * 64
    with pytest.raises(ValueError, match="fingerprint"):
        MemoryEntityReader(memory, layout)
    assert memory.calls == 0


@pytest.mark.parametrize("offset", ["id_offset", "position_offset"])
def test_entity_reuse_or_movement_during_sample_rejected(setup, offset):
    memory, layout, reader = setup
    address = 0x600000 + getattr(layout, offset)

    def mutate(m):
        if m.read_counts.get(address) == 2:
            m.values[address] = 999 if offset == "id_offset" else (681, 570)

    memory.mutate = mutate
    with pytest.raises(ValueError, match="Monster changed"):
        reader.read()


def test_root_changes_during_sample_rejected(setup):
    memory, layout, reader = setup
    root = 0x140000000 + layout.root_rva

    def mutate(m):
        if m.read_counts.get(root) == 3:
            m.values[root] = 0x900000

    memory.mutate = mutate
    with pytest.raises(ValueError, match="collection changed"):
        reader.read()


def test_process_exit_and_slow_samples_fail_closed(setup):
    memory, layout, _ = setup
    memory.exited = True
    with pytest.raises(OSError, match="exited"):
        MemoryEntityReader(memory, layout).read()
    memory.exited = False
    times = iter([0, 4])
    with pytest.raises(ValueError, match="expired"):
        MemoryEntityReader(memory, layout, clock=lambda: next(times)).read()


def test_duplicate_objects_and_ids_rejected(setup):
    memory, layout, reader = setup
    memory.values[0x500018] = 0x600000
    with pytest.raises(ValueError, match="Duplicate scene"):
        reader.read()
    memory.values[0x500018] = 0x700000
    memory.values[0x700000 + layout.kind_offset] = 2
    memory.values[0x700000 + layout.id_offset] = 450000
    with pytest.raises(ValueError, match="Duplicate monster"):
        reader.read()


def test_restart_resolves_new_scene_addresses(setup):
    memory, layout, reader = setup
    assert reader.read().object_count == 3
    # Same module, new heap allocation; no scene address may be cached.
    memory.values[0x300008] = 0x900000
    for offset in (0, layout.begin_offset, layout.end_offset, layout.capacity_offset):
        memory.values[0x900000 + offset] = memory.values[0x400000 + offset]
    del memory.values[0x400000]
    assert reader.read().monsters[0].entity_id == 450000


@pytest.mark.parametrize("changed", ["none", "uid", "missing", "moving", "membership"])
def test_selected_refresh_only_reads_target_but_preserves_identity_guards(
    setup, changed
):
    memory, layout, reader = setup
    if changed == "uid":
        memory.values[0x600000 + layout.id_offset] += 1
    if changed == "missing":
        memory.values[0x500008] = 0x700000
    if changed in ("moving", "membership"):

        def mutate(m):
            if m.read_counts.get(0x600000 + layout.position_offset, 0) >= 2:
                if changed == "moving":
                    m.values[0x600000 + layout.position_offset] = (681, 570)
                else:
                    m.values[0x500008] = 0x700000

        memory.mutate = mutate
    if changed == "none":
        snapshot = reader.read(selected=(450000, 0x600000))
        assert len(snapshot.monsters) == 1 and snapshot.monsters[0].entity_id == 450000
        assert 0x800000 + layout.position_offset not in memory.read_counts
        assert 0x600000 + layout.position_offset in memory.read_counts
    else:
        with pytest.raises(ValueError):
            reader.read(selected=(450000, 0x600000))


@pytest.mark.parametrize(
    "change",
    [
        None,
        "id",
        "position",
        "draw",
        "name",
        "hp",
        "level",
        "membership",
        "short",
        "exit",
    ],
)
def test_packed_records_preserve_stability_identity_and_membership_guards(
    setup, change
):
    import struct

    memory, layout, reader = setup
    expected = reader.read()
    calls = []

    def block(address, size):
        data = bytearray(size)
        for location, value in memory.values.items():
            if not address <= location < address + size:
                continue
            if isinstance(value, str):
                encoded = value.encode() + b"\0"
            elif isinstance(value, tuple):
                encoded = struct.pack("<II", *value)
            else:
                encoded = int(value).to_bytes(4, "little", signed=value < 0)
            offset = location - address
            data[offset : offset + len(encoded)] = encoded
        calls.append((address, size))
        if len(calls) == 1:
            offset = {
                "id": layout.id_offset,
                "position": layout.position_offset,
                "draw": layout.draw_position_offset,
                "name": layout.name_offset,
                "hp": layout.max_hp_offset,
                "level": layout.level_offset,
            }.get(change)
            if offset is not None:
                value = memory.values[0x600000 + offset]
                memory.values[0x600000 + offset] = (
                    (681, 570)
                    if change == "position"
                    else "Changed"
                    if change == "name"
                    else value + 1
                )
            elif change == "membership":
                memory.values[0x500008] = 0x800000
            elif change == "exit":
                memory.exited = True
        return bytes(data[:-1] if change == "short" else data)

    memory.read_block = block
    if change is None:
        actual = reader.read(packed=True)
        assert actual.monsters == expected.monsters
        assert len(calls) == 2 and all(size <= 4096 for _, size in calls)
    else:
        with pytest.raises((ValueError, OSError)):
            reader.read(packed=True)


def test_packed_record_span_is_bounded():
    from types import SimpleNamespace
    from conquest.memory_entities import record_values

    def forbidden(*args):
        pytest.fail("Oversized records must not be read")

    with pytest.raises(ValueError, match="bounded"):
        record_values(
            SimpleNamespace(read_block=forbidden),
            [0x600000],
            [(0, "u32"), (4096, "u32")],
            packed=True,
        )
