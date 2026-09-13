import struct
from types import SimpleNamespace as NS

import pytest

from conquest.memory_life import CLIENT_SHA256
from conquest.merchants import request_identity as identity
from conquest.character_context import trusted_delivery


@pytest.fixture
def scene(monkeypatch):
    base, collection, begin, actor = 0x10000000, 0x20000000, 0x30000000, 0x40000000
    raw = bytearray(0xc4)
    struct.pack_into('<Q', raw, 0, base + 0x5c5e20)
    struct.pack_into('<I', raw, 0x78, 1173490)
    raw[0xa4:0xac] = b'Parasite'
    state = NS(raw=raw, duplicate=False, ambiguous=False, mutate=False, bad_code=False, reads=0)
    def read(address, size):
        if address == base + 0x11045e:
            return b'\0' * size if state.bad_code else bytes.fromhex('e8cd160700488d98d80f0000')
        if address == begin:
            if state.ambiguous:
                return struct.pack('<4Q', 0, actor, 0, actor + 0x1000)
            return struct.pack('<2Q', 0, actor) * (2 if state.duplicate else 1)
        assert address in (actor, actor + 0x1000)
        state.reads += 1
        if state.mutate and state.reads == 2:
            struct.pack_into('<I', raw, 0x78, 1173491)
        return bytes(raw[:size])
    layout = NS(begin_offset=0x58, end_offset=0x60, capacity_offset=0x68,
                entry_stride=16, entry_object_offset=8, max_objects=1000)
    def sample(session, fields):
        if not fields:
            return []
        return [begin, begin + (32 if state.duplicate or state.ambiguous else 16), begin + 32]
    monkeypatch.setattr(identity, 'sample_fields', sample)
    state.observer = NS(adapter=NS(read_block=read, assert_identity=lambda:None,
                                 expected_sha256=CLIENT_SHA256),
                        entities=NS(layout=layout, _resolve=lambda:(base, collection, [])))
    return state


def test_named_prompt_supplies_actual_uid_without_weakening_trust(scene, monkeypatch):
    import conquest.character_context as context
    monkeypatch.setattr(context, 'registry', lambda:None)
    assert not trusted_delivery('Spiritual', 'Parasite', None)
    uid = identity.participant_uid(scene.observer, 'Parasite')
    assert uid == 1173490
    assert trusted_delivery('Spiritual', 'Parasite', uid)
    assert not trusted_delivery('Spiritual', 'Stranger', uid)


@pytest.mark.parametrize('fault', ['mutate', 'duplicate', 'bad_code', 'build', 'layout'])
def test_changed_or_unqualified_scene_never_authorizes_acceptance(scene, fault):
    if fault == 'build':
        scene.observer.adapter.expected_sha256 = 'different-build'
    elif fault == 'layout':
        scene.observer.entities.layout.entry_stride = 8
    else:
        setattr(scene, fault, True)
    with pytest.raises(ValueError):
        identity.participant_uid(scene.observer, 'Parasite')


@pytest.mark.parametrize('fault', ['absent', 'zero_uid', 'wrong_vtable', 'ambiguous'])
def test_unknown_request_source_remains_untrusted(scene, fault):
    if fault == 'ambiguous':
        scene.ambiguous = True
    if fault == 'zero_uid':
        struct.pack_into('<I', scene.raw, 0x78, 0)
    if fault == 'wrong_vtable':
        struct.pack_into('<Q', scene.raw, 0, 0x10000001)
    name = 'Stranger' if fault == 'absent' else 'Parasite'
    assert identity.participant_uid(scene.observer, name) is None
