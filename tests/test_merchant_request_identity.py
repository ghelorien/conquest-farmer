import struct
from types import SimpleNamespace as NS

import pytest

from conquest.memory_life import CLIENT_SHA256
from conquest.merchants import request_identity as identity
from conquest.character_context import trusted_delivery


@pytest.fixture
def scene(monkeypatch):
    base, collection, begin, actor = 0x10000000, 0x20000000, 0x30000000, 0x40000000
    raw = bytearray(0xC4)
    struct.pack_into("<Q", raw, 0, base + 0x5C5E20)
    struct.pack_into("<I", raw, 0x78, 1173490)
    raw[0xA4:0xAC] = b"Parasite"
    state = NS(
        raw=raw, duplicate=False, ambiguous=False, mutate=False, bad_code=False, reads=0
    )

    def read(address, size):
        if address == base + 0x11045E:
            return (
                b"\0" * size
                if state.bad_code
                else bytes.fromhex("e8cd160700488d98d80f0000")
            )
        if address == begin:
            if state.ambiguous:
                return struct.pack("<4Q", 0, actor, 0, actor + 0x1000)
            return struct.pack("<2Q", 0, actor) * (2 if state.duplicate else 1)
        assert address in (actor, actor + 0x1000)
        state.reads += 1
        if state.mutate and state.reads == 2:
            struct.pack_into("<I", raw, 0x78, 1173491)
        return bytes(raw[:size])

    layout = NS(
        begin_offset=0x58,
        end_offset=0x60,
        capacity_offset=0x68,
        entry_stride=16,
        entry_object_offset=8,
        max_objects=1000,
    )

    def sample(session, fields):
        if not fields:
            return []
        return [
            begin,
            begin + (32 if state.duplicate or state.ambiguous else 16),
            begin + 32,
        ]

    monkeypatch.setattr(identity, "sample_fields", sample)
    state.observer = NS(
        adapter=NS(
            read_block=read, assert_identity=lambda: None, expected_sha256=CLIENT_SHA256
        ),
        entities=NS(layout=layout, _resolve=lambda: (base, collection, [])),
    )
    return state


def test_named_prompt_supplies_actual_uid_without_weakening_trust(scene, monkeypatch):
    import conquest.character_context as context

    monkeypatch.setattr(context, "registry", lambda: None)
    assert not trusted_delivery("Spiritual", "Parasite", None)
    uid = identity.participant_uid(scene.observer, "Parasite")
    assert uid == 1173490
    assert trusted_delivery("Spiritual", "Parasite", uid)
    assert not trusted_delivery("Spiritual", "Stranger", uid)


@pytest.mark.parametrize(
    "fault", ["mutate", "duplicate", "bad_code", "build", "layout"]
)
def test_changed_or_unqualified_scene_never_authorizes_acceptance(scene, fault):
    if fault == "build":
        scene.observer.adapter.expected_sha256 = "different-build"
    elif fault == "layout":
        scene.observer.entities.layout.entry_stride = 8
    else:
        setattr(scene, fault, True)
    with pytest.raises(ValueError):
        identity.participant_uid(scene.observer, "Parasite")


@pytest.mark.parametrize("fault", ["absent", "zero_uid", "wrong_vtable", "ambiguous"])
def test_unknown_request_source_remains_untrusted(scene, fault):
    if fault == "ambiguous":
        scene.ambiguous = True
    if fault == "zero_uid":
        struct.pack_into("<I", scene.raw, 0x78, 0)
    if fault == "wrong_vtable":
        struct.pack_into("<Q", scene.raw, 0, 0x10000001)
    name = "Stranger" if fault == "absent" else "Parasite"
    assert identity.participant_uid(scene.observer, name) is None


@pytest.mark.parametrize(
    "change",
    [
        "unrelated_uid",
        "unrelated_name",
        "unrelated_vtable",
        "unrelated_all",
        "new_duplicate",
        "name_takeover",
        "target_uid",
        "target_name",
        "target_vtable",
        "new_matching_vtable",
        "collection",
        "entries",
        "trace",
        "process",
        "timeout",
    ],
)
def test_complete_second_scan_preserves_unique_target_while_unrelated_actors_change(
    monkeypatch, change
):
    base, collection, begin = 0x10000000, 0x20000000, 0x30000000
    actor, other = 0x40000000, 0x40001000

    def record(uid, name, vtable=None):
        raw = bytearray(0xC4)
        struct.pack_into("<Q", raw, 0, base + 0x5C5E20 if vtable is None else vtable)
        struct.pack_into("<I", raw, 0x78, uid)
        raw[0xA4 : 0xA4 + len(name)] = name.encode()
        return bytes(raw)

    initial = {actor: record(1173490, "Parasite"), other: record(9, "Unrelated")}
    fresh = dict(initial)
    if change in ("unrelated_uid", "unrelated_all"):
        fresh[other] = record(10, "Unrelated")
    if change in ("unrelated_name", "unrelated_all"):
        fresh[other] = record(10, "Another")
    if change in ("unrelated_vtable", "unrelated_all"):
        fresh[other] = record(10, "Another", base + 0x1234)
    if change == "new_duplicate":
        fresh[other] = record(1173490, "Parasite")
    if change == "name_takeover":
        fresh[actor] = record(9, "Unrelated")
        fresh[other] = record(1173490, "Parasite")
    if change == "target_uid":
        fresh[actor] = record(1173491, "Parasite")
    if change == "target_name":
        fresh[actor] = record(1173490, "Changed")
    if change == "target_vtable":
        fresh[actor] = record(1173490, "Parasite", base + 0x1234)
    if change == "new_matching_vtable":
        initial[other] = record(9, "Parasite", base + 0x1234)
        fresh[other] = record(9, "Parasite")
    entries = struct.pack("<4Q", 0, actor, 0, other)
    reads = {}
    identity_checks = []
    samples = []
    trace_address = 0x50000000

    def read(address, size):
        reads[address] = reads.get(address, 0) + 1
        if address == base + 0x11045E:
            return bytes.fromhex("e8cd160700488d98d80f0000")
        if address == begin:
            return (
                entries
                if change != "entries" or reads[address] == 1
                else struct.pack("<4Q", 0, other, 0, actor)
            )
        assert address in initial
        return initial[address] if reads[address] == 1 else fresh[address]

    def sample(session, fields):
        samples.append(fields)
        if fields == [(trace_address, "u64")]:
            return [100 if change != "trace" else 101]
        end = begin + 32
        if change == "collection" and len(samples) > 1:
            end += 16
        return [begin, end, begin + 64]

    def assert_identity():
        identity_checks.append(True)
        if change == "process" and len(identity_checks) > 1:
            raise ValueError("Game process changed")

    clock = iter([0, 3 if change == "timeout" else 0.1])
    monkeypatch.setattr(identity, "sample_fields", sample)
    monkeypatch.setattr(identity.time, "monotonic", lambda: next(clock))
    observer = NS(
        adapter=NS(
            read_block=read,
            assert_identity=assert_identity,
            expected_sha256=CLIENT_SHA256,
        ),
        entities=NS(
            _resolve=lambda: (base, collection, [(trace_address, 100)]),
            layout=NS(
                entry_stride=16,
                entry_object_offset=8,
                begin_offset=0x58,
                end_offset=0x60,
                capacity_offset=0x68,
                max_objects=1000,
            ),
        ),
    )
    if change.startswith("unrelated_"):
        assert identity.participant_uid(observer, "Parasite") == 1173490
        assert reads[actor] == reads[other] == 2
        assert len(identity_checks) == 2
    else:
        with pytest.raises(ValueError):
            identity.participant_uid(observer, "Parasite")
