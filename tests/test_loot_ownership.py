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


def session():
    return NS(
        expected_sha256=CLIENT_SHA256_1078,
        modules=[{"name": "ImConquer.exe", "base": 0x140000000}],
        identity={"pid": 123, "creation_time": 99},
    )


def test_a_refused_drop_is_tried_again_after_its_protection_then_given_up(tmp_path, monkeypatch):
    # "You can`t pick up other player`s loot at the moment. Please wait.":
    # 2026-10-01 a Meteor (22:14:33) and 2026-10-02 an IronHelmet +1
    # (00:47:52) stayed on the Bandit field for good after one refusal each.
    from conquest import loot_ownership as lo

    clock = [1000.0]
    monkeypatch.setattr(lo.time, "time", lambda: clock[0])
    path = tmp_path / "denied.json"
    guard = LootOwnership(session(), path)
    drop = GroundItem(42, 1000, 1088001, (10, 11), spawn_tick=90)
    for _ in range(lo.OWNERSHIP_TRIES - 1):
        guard.reject(drop, 1011)
        assert guard.blocked(drop, 1011)
        clock[0] += lo.OWNERSHIP_RETRY_SECONDS
        assert not guard.blocked(drop, 1011)  # the protection may be over: try again
    guard.reject(drop, 1011)  # the last refusal: given up for good
    clock[0] += 3600
    assert guard.blocked(drop, 1011)
    assert LootOwnership(session(), path).blocked(drop, 1011)


def test_a_refusal_saved_as_a_bare_time_counts_once(tmp_path, monkeypatch):
    import json

    from conquest import loot_ownership as lo

    clock = [1000.0]
    monkeypatch.setattr(lo.time, "time", lambda: clock[0])
    drop = GroundItem(42, 1000, 1088001, (10, 11), spawn_tick=90)
    path = tmp_path / "denied.json"
    key = LootOwnership.key(drop, 1011)
    path.write_text(json.dumps({"identity": session().identity, "denied": {key: 995.0}}))
    guard = LootOwnership(session(), path)
    assert guard.blocked(drop, 1011)
    clock[0] += lo.OWNERSHIP_RETRY_SECONDS
    assert not guard.blocked(drop, 1011)
    guard.reject(drop, 1011)
    assert guard.denied[key][1] == 2
