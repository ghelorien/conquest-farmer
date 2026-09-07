from pathlib import Path
from types import SimpleNamespace
import pytest
import yaml

from conquest.memory_inventory import InventoryLayout, MemoryInventoryReader, read_inventory

PLAYER, MODULE, TABLE = 0x100000, 0x140000000, 0x200000


class Memory:
    def __init__(self):
        self.values = {}
        self.calls = 0
        self.mutate = None
        self.exited = False
        self.read_counts = {}

    def request(self, operation, body):
        assert operation == "sample"
        assert len(body["fields"]) <= 64
        self.calls += 1
        for field in body["fields"]:
            address = int(field["address"], 0)
            self.read_counts[address] = self.read_counts.get(address, 0) + 1
        if self.mutate:
            self.mutate(self)
        return {"fields": [{"value": [self.values[int(f["address"], 0)]]} for f in body["fields"]]}

    def assert_identity(self):
        if self.exited:
            raise OSError("process exited")


@pytest.fixture
def populated():
    layout = InventoryLayout.model_validate(yaml.safe_load((Path(__file__).parents[1] / "profiles/classic-1074-inventory-candidate.yaml").read_text()))
    memory = Memory()
    for name, value in [("deque_map", TABLE), ("deque_map_size", 8), ("deque_start", 7),
                        ("deque_count", 2), ("lookup_count", 2), ("silver", 4420),
                        ("equipped_ammo", 0x500000)]:
        memory.values[PLAYER + getattr(layout, name)] = value
    for table_slot, block, ptr in [(7, 0x300000, 0x400000), (0, 0x300100, 0x400100)]:
        memory.values[TABLE + table_slot * 8] = block
        memory.values[block] = ptr
    for ptr, uid, type_id, amount, limit in [(0x400000, 101, 1000000, 1, 1),
                                           (0x400100, 102, 1050000, 200, 200),
                                           (0x500000, 103, 1050000, 170, 200)]:
        memory.values.update({ptr: MODULE + layout.item_vtable_rva,
                              ptr+layout.item_uid: uid, ptr+layout.item_type: type_id,
                              ptr+layout.item_amount: amount, ptr+layout.item_limit: limit})
    return memory, layout


def test_wrapped_inventory_order_and_equipped_ammo(populated):
    memory, layout = populated
    result = read_inventory(memory, PLAYER, layout, MODULE)
    assert [(i.slot, i.uid) for i in result.items] == [(0, 101), (1, 102)]
    assert result.count(1000000) == 1
    assert result.equipped_ammo.amount == 170
    assert result.silver == 4420


@pytest.mark.parametrize("offset,value,match", [
    ("deque_count", 41, "count"), ("lookup_count", 3, "count"),
    ("deque_map_size", 9, "map size"), ("deque_start", 8, "start")])
def test_invalid_container_rejected(populated, offset, value, match):
    memory, layout = populated
    memory.values[PLAYER + getattr(layout, offset)] = value
    with pytest.raises(ValueError, match=match):
        read_inventory(memory, PLAYER, layout, MODULE)


def test_item_replaced_during_observation_rejected(populated):
    memory, layout = populated
    uid = 0x400000 + layout.item_uid
    memory.mutate = lambda m: m.values.update({uid: 999}) if m.read_counts.get(uid) == 2 else None
    with pytest.raises(ValueError, match="changed"):
        read_inventory(memory, PLAYER, layout, MODULE)


def test_arrow_consumption_during_read_returns_latest_quantity(populated):
    memory, layout = populated
    amount = 0x500000 + layout.item_amount
    memory.mutate = lambda m: m.values.update({amount: 169}) if m.read_counts.get(amount) == 2 else None
    assert read_inventory(memory, PLAYER, layout, MODULE).equipped_ammo.amount == 169


def test_wrong_item_type_and_invalid_amount_rejected(populated):
    memory, layout = populated
    memory.values[0x400000] = MODULE
    with pytest.raises(ValueError, match="identity"):
        read_inventory(memory, PLAYER, layout, MODULE)
    memory.values[0x400000] = MODULE + layout.item_vtable_rva
    memory.values[0x400000 + layout.item_amount] = 2
    with pytest.raises(ValueError, match="amount"):
        read_inventory(memory, PLAYER, layout, MODULE)


def test_absent_equipped_ammo_is_observed(populated):
    memory, layout = populated
    memory.values[PLAYER + layout.equipped_ammo] = 0
    assert read_inventory(memory, PLAYER, layout, MODULE).equipped_ammo is None


def test_process_exit_and_fingerprint_change(populated):
    memory, layout = populated
    memory.exited = True
    with pytest.raises(OSError, match="exited"):
        read_inventory(memory, PLAYER, layout, MODULE)
    with pytest.raises(ValueError, match="fingerprint"):
        MemoryInventoryReader(memory, SimpleNamespace(expected_sha256="0"*64), layout)
