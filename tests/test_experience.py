import struct
from types import SimpleNamespace
import pytest
from conquest.experience import read_experience, Experience, ExperienceRate


def test_experience_uses_decoded_level_requirements_and_coherent_sample():
    blocks = {
        100 + 0x6E8: struct.pack("<I", 2),
        100 + 0x708: struct.pack("<Q", 30),
        100 + 0x1120: struct.pack("<II", 100, 200),
    }
    session = SimpleNamespace(
        assert_identity=lambda: None, read=lambda address, size: blocks[address]
    )
    result = read_experience(session, 100)
    assert (result.level, result.percent, result.cumulative) == (2, 15, 130)
    calls = iter([struct.pack("<Q", 30), struct.pack("<Q", 31)])
    session.read = lambda address, size: (
        next(calls) if address == 100 + 0x708 else blocks[address]
    )
    with pytest.raises(ValueError, match="changed"):
        read_experience(session, 100)


def test_build_1078_reads_experience_through_its_player_layout():
    from pathlib import Path
    import yaml
    from conquest.experience import experience_offsets
    from conquest.memory_health import HealthLayout

    profile = Path(__file__).resolve().parents[1] / "profiles"
    layout = HealthLayout.model_validate(
        yaml.safe_load(
            (profile / "classic-1078-health-candidate.yaml").read_text(encoding="utf-8")
        )
    ).player
    # Live on Toxic 2026-09-27: level 21, 33892 of 70515 for the level.
    assert experience_offsets(layout) == (0x6F8, 0x718, 0x1148)
    table = struct.pack("<21I", *range(1, 21), 70515)
    blocks = {
        100 + 0x6F8: struct.pack("<I", 21),
        100 + 0x718: struct.pack("<Q", 33892),
        100 + 0x1148: table,
    }
    session = SimpleNamespace(
        assert_identity=lambda: None, read=lambda address, size: blocks[address]
    )
    result = read_experience(session, 100, layout)
    assert (result.level, result.current, result.required) == (21, 33892, 70515)
    assert result.cumulative == sum(range(1, 21)) + 33892
    # A layout without the XP fields keeps the legacy offsets.
    assert experience_offsets(SimpleNamespace(level_offset=0x6F8)) == (
        0x6E8,
        0x708,
        0x1120,
    )


def test_experience_rate_counts_level_up_travel_and_death_loss():
    rate = ExperienceRate()
    assert rate.add(Experience(1, 90, 100, 90, 0)) is None
    assert rate.add(Experience(2, 10, 200, 110, 60)) == 1200
    assert rate.add(Experience(2, 5, 200, 105, 120)) == 450
    assert rate.add(Experience(2, 0, 200, 80, 180)) == -200
