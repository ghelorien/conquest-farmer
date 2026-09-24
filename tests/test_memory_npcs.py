import base64
import json
from pathlib import Path
import struct

import pytest
import yaml

from conquest.memory_entities import EntityLayout, MemoryEntityReader
from conquest.memory_npcs import MemoryNpcReader


@pytest.mark.parametrize(
    "map_id,name,position,expected_y",
    [
        (1036, "Warehouseman", (182, 180), 249),
        (1011, "Warehouseman", (227, 246), 281),
        (1036, "MillionaireLee", (242, 242), 281),
    ],
)
def test_warehouse_interaction_point_preserves_other_npcs(
    map_id, name, position, expected_y
):
    from conquest.memory_npcs import NpcObservation, interaction_point

    npc = NpcObservation(123, 456, 0, name, map_id, position, (638, 313))
    assert interaction_point(npc) == (638, expected_y)


def test_market_warehouse_reachability_uses_actual_click_point():
    from conquest.memory_npcs import NpcObservation
    from conquest.town_trade import TownTrade

    trade = object.__new__(TownTrade)
    trade.observer = None
    # Old -32 offset reports reachable at the failed approach (186,188).
    trade.vendor = lambda kind: NpcObservation(
        123, 456, 0, "Warehouseman", 1036, (182, 180), (638, 185)
    )
    result = trade.execute({"action": "vendor-status", "vendor_type": 0})
    assert result["point"] == (638, 121)
    assert result["reachable"] is False


@pytest.mark.parametrize(
    "size,draw,reachable",
    [
        ((2056, 1236), (1092, 490), True),
        ((1036, 793), (1092, 490), False),
        ((2056, 1236), (2020, 490), False),
        ((2056, 1236), (1028, 1150), False),
    ],
)
def test_vendor_reachability_uses_current_viewport_and_hud(size, draw, reachable):
    from types import SimpleNamespace as NS
    from conquest.memory_npcs import NpcObservation
    from conquest.town_trade import TownTrade

    trade = object.__new__(TownTrade)
    trade.observer = NS(adapter=NS(viewport_size=lambda: size))
    trade.vendor = lambda kind: NpcObservation(
        123, 456, 0, "Warehouseman", 1011, (227, 246), draw
    )
    assert (
        trade.execute({"action": "vendor-status", "vendor_type": 0})["reachable"]
        == reachable
    )


class Replay:
    def __init__(self):
        self.layout = EntityLayout.model_validate(
            yaml.safe_load(
                Path("profiles/classic-1074-entities-candidate.yaml").read_text()
            )
        )
        self.expected_sha256 = self.layout.expected_sha256
        self.modules = [
            {"name": "ImConquer.exe", "base": 0x140000000, "size": 0x3000000}
        ]
        rows = json.loads(Path("tests/fixtures/town-vendor-memory.json").read_text())[
            "records"
        ]
        self.blocks = {
            r["address"]: bytearray(base64.b64decode(r["data_base64"])) for r in rows
        }
        self.pointers = {
            0x140000000 + self.layout.root_rva: 0x200000,
            0x200018: 0x300000,
            0x300008: 0x400000,
            0x400000: 0x140000000 + self.layout.collection_vtable_rva,
            0x400058: 0x500000,
            0x400060: 0x500020,
            0x400068: 0x500020,
            **{0x500008 + i * 16: obj for i, obj in enumerate(self.blocks)},
        }
        self.read_counts = {}
        self.mutate = lambda: None
        self.exited = False

    def assert_identity(self):
        if self.exited:
            raise OSError("process exited")

    def request(self, operation, body):
        assert operation == "sample"
        assert 0 < len(body["fields"]) <= 64
        results = []
        for f in body["fields"]:
            address = int(f["address"], 0)
            self.read_counts[address] = self.read_counts.get(address, 0) + 1
            self.mutate()
            if address in self.pointers:
                value = [self.pointers[address]]
            else:
                obj = next(
                    obj
                    for obj, b in self.blocks.items()
                    if obj <= address < obj + len(b)
                )
                data, offset = self.blocks[obj], address - obj
                if f["kind"] == "utf8":
                    value = (
                        bytes(data[offset : offset + 64])
                        .split(b"\0")[0]
                        .decode("utf-8")
                    )
                else:
                    fmt = {"u64": "<Q", "u32": "<I", "i32": "<i", "xy_u32": "<II"}[
                        f["kind"]
                    ]
                    value = list(struct.unpack_from(fmt, data, offset))
            results.append({**f, "value": value})
        return {"fields": results}


@pytest.fixture
def replay():
    memory = Replay()
    return memory, MemoryNpcReader(MemoryEntityReader(memory, memory.layout))


def test_recorded_town_vendors_have_ids_types_and_positions(replay):
    _, reader = replay
    rows = {npc.entity_id: npc for npc in reader.read(1002).npcs}
    assert (rows[100102].name, rows[100102].type_id, rows[100102].position) == (
        "Pharmacist",
        3,
        (466, 327),
    )
    assert (rows[100104].name, rows[100104].type_id, rows[100104].position) == (
        "Blacksmith",
        5,
        (452, 330),
    )
    assert rows[100102].draw_position == (774, 300)


def test_player_with_vendor_name_is_not_a_vendor(replay):
    memory, reader = replay
    for record in memory.blocks.values():
        struct.pack_into("<I", record, 0x78, 1166684)
        struct.pack_into("<I", record, 0x7C, 0)
    assert reader.read(1002).npcs == ()


@pytest.mark.parametrize("offset,value", [(0x80, 2), (0x84, 53805059), (0xE8, 4096)])
def test_mismatched_vendor_type_model_or_world_coordinate_rejected(
    replay, offset, value
):
    memory, reader = replay
    struct.pack_into("<I", next(iter(memory.blocks.values())), offset, value)
    with pytest.raises(ValueError, match="identity or position"):
        reader.read(1002)


def test_recycled_vendor_during_sample_is_rejected(replay):
    memory, reader = replay
    obj = next(iter(memory.blocks))

    def mutate():
        if memory.read_counts.get(obj + 0xE8) == 2:
            struct.pack_into("<I", memory.blocks[obj], 0xE8, 500)

    memory.mutate = mutate
    with pytest.raises(ValueError, match="NPC changed"):
        reader.read(1002)


def test_removed_scene_record_is_rejected(replay):
    memory, reader = replay

    def mutate():
        if memory.read_counts.get(0x400060) == 2:
            memory.pointers[0x400060] = 0x500000

    memory.mutate = mutate
    with pytest.raises(ValueError, match="scene changed"):
        reader.read(1002)


def test_wrong_map_never_reuses_town_ids(replay):
    memory, reader = replay
    assert reader.read(1015).npcs == ()
    assert not memory.read_counts
    with pytest.raises(ValueError, match="not present"):
        reader.require(1015, 100102)


def test_expired_snapshot_is_not_usable(replay):
    _, reader = replay
    reader.clock = iter([0.0, 0.6]).__next__
    with pytest.raises(ValueError, match="expired"):
        reader.read(1002)


def test_new_session_vendor_ids_are_discovered_and_old_ids_rejected(replay):
    memory, reader = replay
    for record in memory.blocks.values():
        uid = struct.unpack_from("<I", record, 0x78)[0]
        struct.pack_into("<I", record, 0x78, uid + 21)
    assert reader.require(1002, 100123).type_id == 3
    assert reader.require(1002, 100125).type_id == 5
    with pytest.raises(ValueError, match="not present"):
        reader.require(1002, 100102)


def test_warehouse_type_zero_requires_matching_model_name_and_tile(replay):
    memory, reader = replay
    record = next(iter(memory.blocks.values()))
    struct.pack_into("<I", record, 0x7C, 0)
    struct.pack_into("<I", record, 0x84, 80)
    struct.pack_into("<II", record, 0xE8, 409, 351)
    record[0xA4:0xC4] = b"Warehouseman" + bytes(20)
    assert any(
        n.name == "Warehouseman" and n.position == (409, 351)
        for n in reader.read(1002).npcs
    )
    record[0xA4:0xC4] = b"Player" + bytes(26)
    with pytest.raises(ValueError, match="identity or position"):
        reader.read(1002)


@pytest.mark.parametrize("stable,accepted", [(False, False), (True, True)])
def test_market_identity_only_sampling_excludes_animated_draw_coordinates(
    replay, stable, accepted
):
    from conquest.market_services import discover

    memory, reader = replay
    obj = next(iter(memory.blocks))
    record = memory.blocks[obj]
    struct.pack_into("<I", record, 0x7C, 0)
    struct.pack_into("<I", record, 0x84, 87)
    struct.pack_into("<II", record, 0xE8, 182, 180)
    record[0xA4:0xC4] = b"Warehouseman" + bytes(20)
    draw = memory.layout.draw_position_offset

    def mutate():
        if memory.read_counts.get(obj + draw) == 2:
            struct.pack_into("<ii", record, draw, 800, 320)

    memory.mutate = mutate
    if accepted:
        identity, npc = discover(
            reader.entities, 1036, "Warehouseman", stable_identity_only=stable
        )
        assert npc.draw_position == (800, 320) and npc.position == (182, 180)
    else:
        with pytest.raises(ValueError, match="changed during observation"):
            discover(reader.entities, 1036, "Warehouseman", stable_identity_only=stable)


@pytest.mark.parametrize("offset,value", [(0x78, 999), (0xE8, 183), (0x7C, 1)])
def test_market_stable_identity_sampling_still_rejects_uid_world_tile_and_type_drift(
    replay, offset, value
):
    from conquest.market_services import discover

    memory, reader = replay
    obj = next(iter(memory.blocks))
    record = memory.blocks[obj]
    struct.pack_into("<I", record, 0x7C, 0)
    struct.pack_into("<I", record, 0x84, 87)
    struct.pack_into("<II", record, 0xE8, 182, 180)
    record[0xA4:0xC4] = b"Warehouseman" + bytes(20)

    def mutate():
        if memory.read_counts.get(obj + offset) == 2:
            struct.pack_into("<I", record, offset, value)

    memory.mutate = mutate
    with pytest.raises(ValueError, match="changed during observation"):
        discover(reader.entities, 1036, "Warehouseman", stable_identity_only=True)


def test_market_withdrawal_vendor_uses_selected_identity_without_whole_scene_resample(
    monkeypatch,
):
    from types import SimpleNamespace as NS
    from conquest.memory_npcs import NpcObservation
    from conquest.town_trade import TownTrade
    from conquest import market_services, town_trade

    trade = TownTrade.__new__(TownTrade)
    trade.observer = NS(entities="scene")
    trade.life = lambda **kw: NS(map_id=1036, position=(183, 180))
    npc = NpcObservation(123, 456, 0, "Warehouseman", 1036, (182, 180), (400, 300))
    calls = []
    monkeypatch.setattr(
        market_services,
        "discover",
        lambda *a, **kw: calls.append((a, kw)) or (None, npc),
    )
    monkeypatch.setattr(
        town_trade,
        "MemoryNpcReader",
        lambda *a, **kw: pytest.fail("unrelated NPC scene resample"),
    )
    assert trade.vendor(0, stable_identity_only=True) == npc
    assert calls == [(("scene", 1036, "Warehouseman"), {"stable_identity_only": True})]


def test_phoenix_service_roles_resolve_city_specific_memory_types():
    from conquest.memory_npcs import vendor_identity
    from conquest.town_trade import TownTrade
    from types import SimpleNamespace

    expected = vendor_identity(1011, 3)
    assert (expected.type_id, expected.model, expected.position) == (
        10014,
        230,
        (189, 252),
    )
    assert vendor_identity(1002, 3).type_id == 3
    with pytest.raises(ValueError):
        vendor_identity(1011, 1)
    trade = TownTrade.__new__(TownTrade)
    trade.life = lambda: SimpleNamespace(map_id=1011, position=(191, 250))
    trade.npcs = SimpleNamespace(read=lambda map_id: SimpleNamespace(npcs=[expected]))
    assert trade.vendor(3) == expected
