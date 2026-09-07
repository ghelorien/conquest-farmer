from pathlib import Path

import pytest
import yaml

from conquest.memory_entities import EntityLayout, MemoryEntityReader


class Memory:
    expected_sha256 = "c2b53437ef68d687a1ef0f70c74bcf2df6027bf82b558e93330c839eb5e1c396"
    modules = [{"name": "ImConquer.exe", "base": 0x140000000, "size": 0x3000000}]
    identity = {"pid": 123, "creation_time_100ns": 456}

    def __init__(self, layout):
        p = layout
        self.calls, self.identity_checks = 0, 0
        self.mutate, self.exited = None, False
        self.read_counts = {}
        self.values = {
            0x140000000 + p.root_rva: 0x200000, 0x200018: 0x300000,
            0x300008: 0x400000, 0x400000: 0x140000000 + p.collection_vtable_rva,
            0x400000 + p.begin_offset: 0x500000, 0x400000 + p.end_offset: 0x500030,
            0x400000 + p.capacity_offset: 0x500100,
        }
        for index, (address, kind, name) in enumerate([
            (0x600000, 2, "Turtledove"), (0x700000, 0, "OtherPlayer"), (0x800000, 1, "Guard")
        ]):
            self.values[0x500008 + index * 16] = address
            self.values.update({
                address: 0x140000000 + p.monster_vtable_rva,
                address + p.id_offset: 450000 + index,
                address + p.kind_offset: kind, address + p.name_offset: name,
                address + p.position_offset: (680, 570),
                address + p.draw_position_offset: -60,
                address + p.draw_position_offset + 4: 800,
                address + p.max_hp_offset: 81, address + p.level_offset: 7,
            })

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
    path = Path(__file__).parents[1] / "profiles/classic-1074-entities-candidate.yaml"
    layout = EntityLayout.model_validate(yaml.safe_load(path.read_text()))
    memory = Memory(layout)
    return memory, layout, MemoryEntityReader(memory, layout)


def test_memory_ids_positions_exclude_players_and_do_not_infer_alive(setup):
    _, _, reader = setup
    result = reader.read()
    assert len(result.monsters) == 1
    monster = result.monsters[0]
    assert (monster.entity_id, monster.name, monster.position) == (450000, "Turtledove", (680, 570))
    assert monster.draw_position == (-60, 800)  # Offscreen actors are still observable.
    assert monster.current_hp is None and monster.alive is None
    assert reader.report()["autonomous_actions_enabled"] is False


@pytest.mark.parametrize("value", [0x4ffff0, 0x500009, 0x600000])
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
