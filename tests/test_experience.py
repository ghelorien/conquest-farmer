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


def test_experience_rate_counts_level_up_travel_and_death_loss():
    rate = ExperienceRate()
    assert rate.add(Experience(1, 90, 100, 90, 0)) is None
    assert rate.add(Experience(2, 10, 200, 110, 60)) == 1200
    assert rate.add(Experience(2, 5, 200, 105, 120)) == 450
    assert rate.add(Experience(2, 0, 200, 80, 180)) == -200
