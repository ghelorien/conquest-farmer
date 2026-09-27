"""Read the experience HUD's candidate fields; measure complete route episodes."""

from dataclasses import dataclass
import struct
import time


@dataclass(frozen=True)
class Experience:
    level: int
    current: int
    required: int
    cumulative: int
    timestamp: float

    @property
    def percent(self):
        return 100 * self.current / self.required


# (level, current experience, per-level requirement table) on the retired
# legacy client. Build 1078 moved them (level +0x6F8, current +0x718, table
# +0x1148: 120, 180, 240, ... per level, live on Toxic 2026-09-27); its
# player layout carries them, and a layout without them keeps these.
LEGACY_OFFSETS = (0x6E8, 0x708, 0x1120)


def experience_offsets(layout=None):
    offsets = (
        getattr(layout, "level_offset", None),
        getattr(layout, "experience_offset", None),
        getattr(layout, "experience_table_offset", None),
    )
    return offsets if None not in offsets else LEGACY_OFFSETS


def read_experience(session, actual_player, layout=None):
    # Caller resolves the actual player through the pinned, read-only life reader.
    session.assert_identity()
    level_at, current_at, table_at = experience_offsets(layout)

    def fields():
        return (
            struct.unpack("<I", session.read(actual_player + level_at, 4))[0],
            struct.unpack("<Q", session.read(actual_player + current_at, 8))[0],
        )

    level, current = fields()
    if not 1 <= level < 130:
        raise ValueError("Experience HUD changes formula at level 130")
    table = session.read(actual_player + table_at, level * 4)
    requirements = struct.unpack("<" + "I" * level, table)
    if not all(requirements) or current >= requirements[-1]:
        raise ValueError("Experience fields are outside the current level bounds")
    if (
        fields() != (level, current)
        or session.read(actual_player + table_at, level * 4) != table
    ):
        raise ValueError("Experience changed during the sample")
    return Experience(
        level,
        current,
        requirements[-1],
        sum(requirements[:-1]) + current,
        time.monotonic(),
    )


class ExperienceRate:
    def __init__(self):
        self.first = None

    def add(self, reading):
        if (
            self.first is None
            or reading.level < self.first.level
            or reading.timestamp <= self.first.timestamp
        ):
            self.first = reading
            return None
        elapsed = reading.timestamp - self.first.timestamp
        # Net experience includes death losses and all automatic travel time.
        return (
            3600 * (reading.cumulative - self.first.cumulative) / elapsed
            if elapsed >= 10
            else None
        )
