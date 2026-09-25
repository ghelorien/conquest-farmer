from types import SimpleNamespace as NS
from dataclasses import replace
from conquest.loot_ownership import LootOwnership
from conquest.memory_build_layout import CLIENT_SHA256_1078
from conquest.memory_ground import GroundItem


def test_rejected_ground_instance_survives_reload_but_not_new_spawn_or_process(
    tmp_path,
):
    # Persistence and keying never read game memory; the session only has to
    # carry a qualified fingerprint and the client module.
    s = NS(
        expected_sha256=CLIENT_SHA256_1078,
        modules=[{"name": "ImConquer.exe", "base": 0x140000000}],
        identity={"pid": 123, "creation_time": 99},
    )
    path = tmp_path / "denied.json"
    guard = LootOwnership(s, path)
    drop = GroundItem(42, 1000, 1090020, (10, 11), spawn_tick=90)
    guard.reject(drop, 1011)
    assert LootOwnership(s, path).blocked(drop, 1011)
    assert not guard.blocked(replace(drop, spawn_tick=91), 1011)
    assert not guard.blocked(drop, 1002)
    s.identity = {"pid": 123, "creation_time": 100}
    assert not LootOwnership(s, path).blocked(drop, 1011)
