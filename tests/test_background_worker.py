from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from conquest.worker import Operations


@pytest.fixture
def probe_setup(monkeypatch):
    profile = yaml.safe_load(
        (
            Path(__file__).parents[1] / "profiles/classic-1078-health-candidate.yaml"
        ).read_text()
    )
    session = SimpleNamespace(
        expected_sha256=profile["player"]["expected_sha256"],
        modules=[],
        assert_identity=lambda: None,
        read=lambda address, size: b"x" * size,
    )
    operations = Operations.__new__(Operations)
    operations.session, operations.target, operations.read_only = (
        session,
        object(),
        False,
    )
    reading = SimpleNamespace(current_hp=213, max_hp=213)

    def reader(adapter, layout, character):
        assert adapter.read_block(0x10000, 4) == b"xxxx"
        assert character == "Parasite"
        return SimpleNamespace(read=lambda: reading)

    monkeypatch.setattr("conquest.memory_health.MemoryHealthReader", reader)
    calls = []

    def click(target, x, y, size):
        calls.append((target, x, y, size))
        return {"qualified": False, "messages_queued": True}

    monkeypatch.setattr("conquest.worker.click_probe", click)
    return (
        operations,
        reading,
        calls,
        {
            "health_profile": profile,
            "character": "Parasite",
            "point": [100, 200],
            "expected_size": [800, 600],
        },
    )


def test_background_probe_uses_decoded_hp_and_remains_unqualified(probe_setup):
    operations, reading, calls, body = probe_setup
    result = operations.dispatch("background-click", body)
    assert len(calls) == 1
    assert result["hp_candidate_before"] == 213
    assert result["max_hp_candidate_before"] == 213
    assert result["qualified"] is False


@pytest.mark.parametrize("hp", [0, 1, 85])
def test_dead_or_low_candidate_health_prevents_probe(probe_setup, hp):
    operations, reading, calls, body = probe_setup
    reading.current_hp = hp
    with pytest.raises(ValueError, match="too low"):
        operations.dispatch("background-click", body)
    assert calls == []


def test_changed_fingerprint_prevents_probe(probe_setup):
    operations, reading, calls, body = probe_setup
    body["health_profile"]["player"]["expected_sha256"] = "b" * 64
    with pytest.raises(ValueError, match="fingerprint"):
        operations.dispatch("background-click", body)
    assert calls == []
