import json
import struct
from types import SimpleNamespace as NS
from dataclasses import replace
import pytest
from conquest.loot_ownership import (
    SystemMessageReader,
    SystemMessage,
    LootOwnership,
    ownership_rejected,
    OWNERSHIP_MESSAGE,
)
from conquest.memory_life import CLIENT_SHA256
from conquest.memory_ground import GroundItem


def fixture():
    base = 0x140000000
    head = 0x100000
    node = 0x200000
    table = 0x300000
    block = 0x400000
    obj = 0x500000
    chars = 0x600000
    raw = bytearray(0x22)
    struct.pack_into("<3Q", raw, 0, head, head, head)
    struct.pack_into("<H", raw, 0x20, 2005)
    text = OWNERSHIP_MESSAGE.encode()
    record = bytearray(0x48)
    struct.pack_into("<Q", record, 0, chars)
    struct.pack_into("<QQ", record, 0x10, len(text), len(text))
    struct.pack_into("<H", record, 0x20, 2005)
    struct.pack_into("<Q", record, 0x40, 12345)
    data = {
        base + 0x698740: struct.pack("<Q", head),
        head + 8: struct.pack("<Q", node),
        node: bytes(raw),
        node + 0x30: struct.pack("<4Q", table, 8, 7, 1),
        table + 56: struct.pack("<Q", block),
        block: struct.pack("<Q", obj),
        obj + 0x68: bytes(record),
        chars: text,
    }
    session = NS(
        expected_sha256=CLIENT_SHA256,
        modules=[{"name": "ImConquer.exe", "base": base}],
        identity={"pid": 123, "creation_time": 99},
        read_block=lambda a, n: data[a][:n],
        assert_identity=lambda: None,
    )
    return session, data


def test_system_channel_reader_and_old_message_does_not_reject_new_drop():
    s, _ = fixture()
    rows = SystemMessageReader(s).read()
    assert rows == (SystemMessage(0x500000, 12345, OWNERSHIP_MESSAGE),)
    assert not ownership_rejected(rows, rows)
    assert ownership_rejected(rows, rows + (replace(rows[0], tick=12346),))
    assert not ownership_rejected(
        rows, rows + (replace(rows[0], tick=12344, address=0x700000),)
    )
    assert not ownership_rejected(
        rows, rows + (replace(rows[0], tick=12346, text="Inventory is full."),)
    )
    assert not ownership_rejected(
        rows,
        rows + (replace(rows[0], tick=12346, text="Player: " + OWNERSHIP_MESSAGE),),
    )


@pytest.mark.parametrize("change", ["channel", "capacity", "torn"])
def test_unqualified_or_changing_message_record_is_rejected(change):
    s, data = fixture()
    if change == "channel":
        raw = bytearray(data[0x500068])
        struct.pack_into("<H", raw, 0x20, 2000)
        data[0x500068] = bytes(raw)
    if change == "capacity":
        data[0x200030] = struct.pack("<4Q", 0x300000, 7, 0, 1)
    if change == "torn":

        def read(a, n):
            result = data[a][:n]
            if a == 0x500068:
                raw = bytearray(data[a])
                struct.pack_into("<Q", raw, 0x40, 12346)
                data[a] = bytes(raw)
            return result

        s.read_block = read
    with pytest.raises(ValueError):
        SystemMessageReader(s).read()


def test_rejected_ground_instance_survives_reload_but_not_new_spawn_or_process(
    tmp_path,
):
    s, _ = fixture()
    path = tmp_path / "denied.json"
    guard = LootOwnership(s, path)
    drop = GroundItem(42, 1000, 1090020, (10, 11), spawn_tick=90)
    guard.reject(drop, 1011)
    assert LootOwnership(s, path).blocked(drop, 1011)
    assert not guard.blocked(replace(drop, spawn_tick=91), 1011)
    assert not guard.blocked(drop, 1002)
    s.identity = {"pid": 123, "creation_time": 100}
    assert not LootOwnership(s, path).blocked(drop, 1011)
