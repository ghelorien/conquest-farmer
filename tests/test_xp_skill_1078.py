"""Fly activation on the live 1078 client.

Written before the fix (AGENTS.md testing rule). Live 2026-09-25: fly_point
read the XP-skill vector at actor+0x1998 and matched entries against
base+0x5CFF78, both 1074 values; on 1078 (0x19C0 / 0x5EB7B8 in the build
layout) every attempt raised and XpSkill.step dropped the error silently.

Failure modes:
F1 A 1078 session with a ready Fly entry must yield its popup button point.
F2 A 1078 session must never read the 1074 vector offset (0x1998).
F3 The skills popup must be read with the session's own build layout.
F4 An entry carrying the 1074 skill vtable on a 1078 client is rejected.
F5 An unqualified client build fails before any memory read.
F6 The pre-click recheck uses the same 1078 vector; a change is rejected.
"""

import struct
from types import SimpleNamespace as NS

import pytest

from conquest import xp_skill as xp
from conquest.memory_build_layout import CLIENT_SHA256_1078, READ_LAYOUTS

LAYOUT = READ_LAYOUTS[CLIENT_SHA256_1078]
ACTOR, ARRAY, POINTER, BASE = 0x100000, 0x200000, 0x300000, 0x140000000


def rig(monkeypatch, *, vtable=None, sha=CLIENT_SHA256_1078):
    skill = bytearray(0x68)
    struct.pack_into("<Q", skill, 0, BASE + (vtable or LAYOUT.skill_vtable_rva))
    struct.pack_into("<I", skill, 8, 1)
    struct.pack_into("<I", skill, 0x10, 8002)
    skill[0x18:0x1C] = b"Fly\0"
    struct.pack_into("<QQ", skill, 0x28, 3, 15)
    struct.pack_into("<I", skill, 0x44, 2)
    blobs = {
        ACTOR + LAYOUT.xp_charge_offset: struct.pack("<I", 100),
        ACTOR + LAYOUT.life_status_offset: struct.pack("<Q", 0x10),
        ACTOR + LAYOUT.xp_skills_offset: struct.pack(
            "<3Q", ARRAY, ARRAY + 16, ARRAY + 16
        ),
        ARRAY: struct.pack("<2Q", POINTER, 0),
        POINTER: bytes(skill),
    }
    reads = []

    def read_block(address, size):
        reads.append(address)
        return bytes(blobs[address][:size])

    session = NS(
        expected_sha256=sha,
        modules=[{"name": "ImConquer.exe", "base": BASE}],
        read_block=read_block,
        assert_identity=lambda: None,
    )
    life = NS(object_address=ACTOR, dead_candidate=False)
    window = NS(position=(490.0, 613.0), size=(56.0, 56.0), scroll=(0.0, 0.0))
    guis = []

    def gui(s, layout=None):
        guis.append(layout)
        return NS(read=lambda name: window)

    monkeypatch.setattr(xp, "MemoryGui", gui)
    observer = NS(adapter=session, read_life=lambda: life, character="Parasite")
    return observer, blobs, reads, guis


def test_f1_ready_fly_on_1078_returns_its_button(monkeypatch):
    observer, *_ = rig(monkeypatch)
    state = xp.read_xp(observer)
    assert xp.fly_point(observer, state) == (518, 641)


def test_f2_1078_never_reads_the_1074_vector(monkeypatch):
    observer, _, reads, _ = rig(monkeypatch)
    xp.fly_point(observer, xp.read_xp(observer))
    assert ACTOR + 0x1998 not in reads
    assert reads.count(ACTOR + LAYOUT.xp_skills_offset) == 2


def test_f3_popup_uses_the_session_build_layout(monkeypatch):
    observer, _, _, guis = rig(monkeypatch)
    xp.fly_point(observer, xp.read_xp(observer))
    assert guis and all(g is LAYOUT for g in guis)


def test_f4_1074_vtable_on_1078_client_is_rejected(monkeypatch):
    observer, *_ = rig(monkeypatch, vtable=0x5CFF78)
    with pytest.raises(ValueError, match="identity changed"):
        xp.fly_point(observer, xp.read_xp(observer))


def test_f5_unqualified_build_fails_before_memory(monkeypatch):
    observer, _, reads, _ = rig(monkeypatch, sha="0" * 64)
    state = {"charge": 100, "ready": True, "flying": False, "actor": ACTOR}
    with pytest.raises(ValueError, match="No qualified read layout"):
        xp.fly_point(observer, state)
    assert reads == []


def test_f6_recheck_uses_the_1078_vector(monkeypatch):
    observer, blobs, reads, _ = rig(monkeypatch)
    state = xp.read_xp(observer)
    header = ACTOR + LAYOUT.xp_skills_offset
    original = blobs[header]
    count = []

    def changing(address, size):
        reads.append(address)
        if address == header:
            count.append(1)
            if len(count) == 2:
                return struct.pack("<3Q", ARRAY, ARRAY + 16, ARRAY + 32)
        return bytes(blobs[address][:size])

    observer.adapter.read_block = changing
    with pytest.raises(ValueError, match="changed before activation"):
        xp.fly_point(observer, state)
    assert original == blobs[header]
