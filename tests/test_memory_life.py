import struct
from types import SimpleNamespace

import pytest

import conquest.memory_life as life


@pytest.mark.parametrize(
    "status,appearance,expected",
    [
        (0x420, 98, True),
        (0x620, 98, True),
        (0x200, 0, False),
        (0x20, 98, False),
        (0x400, 98, False),
        (0x420, 99, False),
        (0x420, 0, False),
        (0, 98, False),
    ],
)
def test_ghost_candidate_requires_both_observed_indications(
    status, appearance, expected
):
    assert life.ghost_candidate(status, appearance) == expected


class Session:
    expected_sha256 = life.CLIENT_SHA256
    identity = {"pid": 1}

    def __init__(self):
        self.data = {
            0x10030: struct.pack("<Q", 0x420),
            0x100C0: struct.pack("<I", 98),
            0x100D8: struct.pack("<II", 435, 453),
            0x20000: struct.pack("<I", 1002),
            0x10AE8: struct.pack("<Q", 0),
        }
        self.reads = 0

    def read_block(self, address, size):
        self.reads += 1
        return self.data[address]

    def assert_identity(self):
        pass


def reader(monkeypatch):
    session = Session()
    hp = SimpleNamespace(player=SimpleNamespace(expected_sha256=life.CLIENT_SHA256))
    player = SimpleNamespace(expected_sha256=life.CLIENT_SHA256, map_rva=0x699564)
    monkeypatch.setattr(
        life,
        "resolve_player",
        lambda s, p: {"object": 0x10000, "position": 0x100D8, "map": 0x20000},
    )
    monkeypatch.setattr(
        life,
        "MemoryHealthReader",
        lambda *args, **kwargs: SimpleNamespace(
            read=lambda: SimpleNamespace(current_hp=3, max_hp=213)
        ),
    )
    return life.MemoryLifeReader(session, hp, player, "Parasite", clock=lambda: 0)


def test_positive_hp_does_not_override_ghost_indications(monkeypatch):
    result = reader(monkeypatch).read()
    assert result.current_hp == 3 and result.ghost_candidate
    assert result.revive_ready_candidate
    assert result.position == (435, 453) and result.map_id == 1002


def test_nonzero_button_gate_preserves_ghost_but_does_not_claim_ready(monkeypatch):
    candidate = reader(monkeypatch)
    candidate.session.data[0x10AE8] = struct.pack("<Q", 123)
    result = candidate.read()
    assert result.ghost_candidate and not result.revive_ready_candidate


def test_inconsistent_state_is_not_a_ghost_observation(monkeypatch):
    candidate = reader(monkeypatch)

    def changed():
        candidate.session.data[0x10030] = struct.pack("<Q", 0x200)
        return SimpleNamespace(current_hp=213, max_hp=213)

    candidate.health.read = changed
    with pytest.raises(ValueError, match="Life state changed"):
        candidate.read()


def test_recycled_player_pointer_rejects_observation(monkeypatch):
    candidate = reader(monkeypatch)
    calls = 0

    def resolve(*args):
        nonlocal calls
        calls += 1
        return {
            "object": 0x10000 if calls <= 2 else 0x30000,
            "position": 0x100D8,
            "map": 0x20000,
        }

    monkeypatch.setattr(life, "resolve_player", resolve)
    with pytest.raises(ValueError, match="pointer changed"):
        candidate.read()
