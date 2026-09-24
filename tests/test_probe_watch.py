import struct

import pytest

from conquest.probe_watch import (
    read_probe_watch,
    require_observed_values,
    watch_changes,
)


class Session:
    identity = {"pid": 1, "creation_time_100ns": 2}
    expected_sha256 = "a" * 64
    hp = 51
    position = [430, 380]

    def read(self, address, size):
        return (
            struct.pack("<I", self.hp)
            if address == 0x10000
            else struct.pack("<II", *self.position)
        )

    def assert_identity(self):
        pass


def candidate_report():
    return {
        "process_identity": Session.identity,
        "expected_sha256": Session.expected_sha256,
        "candidates": {
            "hp_u32": {
                "kind": "u32",
                "value": 51,
                "addresses": ["0x10000"],
                "truncated": False,
            },
            "position_u32": {
                "kind": "xy_u32",
                "value": [430, 380],
                "addresses": ["0x20000"],
                "truncated": False,
            },
        },
    }


def test_alive_watch_is_still_unqualified():
    candidates = candidate_report()
    watch = read_probe_watch(Session(), candidates)
    require_observed_values(watch, candidates)
    assert not watch["qualified"]


def test_zero_health_blocks_even_if_reference_is_zero():
    session = Session()
    session.hp = 0
    candidates = candidate_report()
    candidates["candidates"]["hp_u32"]["value"] = 0
    with pytest.raises(ValueError, match="zero HP"):
        require_observed_values(read_probe_watch(session, candidates), candidates)


def test_movement_since_calibration_blocks_probe():
    session = Session()
    session.position = [431, 380]
    candidates = candidate_report()
    with pytest.raises(ValueError, match="no longer matches"):
        require_observed_values(read_probe_watch(session, candidates), candidates)


def test_detects_damage_and_movement():
    session = Session()
    candidates = candidate_report()
    before = read_probe_watch(session, candidates)
    session.hp = 48
    session.position = [431, 380]
    after = read_probe_watch(session, candidates)
    assert watch_changes(before, after) == ["hp_u32", "position_u32"]


def test_truncated_watch_is_rejected():
    candidates = candidate_report()
    candidates["candidates"]["hp_u32"]["truncated"] = True
    with pytest.raises(ValueError, match="untruncated"):
        read_probe_watch(Session(), candidates)
