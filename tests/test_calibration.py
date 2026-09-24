import struct
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from conquest.calibration import (
    Observation,
    ObservationSet,
    find_matches,
    refine,
    scan,
    scan_near,
)
from conquest.memory import Region


def observations(value=439, kind="u32"):
    return ObservationSet(
        source="test fixture",
        observed_at=datetime.now(timezone.utc),
        expected_sha256="a" * 64,
        observations=[Observation(name="position", kind=kind, value=value)],
    )


class FakeSession:
    identity = {"pid": 1, "creation_time_100ns": 2}
    modules = []

    def __init__(self, content):
        self.content = content
        self.fail = False

    def regions(self):
        yield Region(0x10000, len(self.content), 0x10000, 0x20000, 4)

    def assert_identity(self):
        pass

    def read(self, address, size):
        if self.fail:
            raise OSError("page unreadable")
        offset = address - 0x10000
        return self.content[offset : offset + size]


def test_cross_chunk_and_unaligned_matches_are_not_lost_or_duplicated():
    data = bytearray(600)
    data[254:258] = struct.pack("<I", 439)
    data[301:305] = struct.pack("<I", 439)
    report = scan(FakeSession(data), observations(), chunk_size=256)
    assert report["candidates"]["position"]["addresses"] == [
        hex(0x10000 + 254),
        hex(0x10000 + 301),
    ]
    assert report["coverage"]["eligible_regions_complete"]
    assert not report["qualified"]


def test_candidate_limit_is_explicit():
    report = scan(
        FakeSession(struct.pack("<I", 439) * 100), observations(), max_candidates=3
    )
    result = report["candidates"]["position"]
    assert len(result["addresses"]) == 3
    assert result["truncated"]


def test_failed_reads_cannot_be_reported_as_complete():
    session = FakeSession(b"\0" * 600)
    session.fail = True
    report = scan(session, observations())
    assert report["coverage"]["failed_bytes"] == 600
    assert not report["coverage"]["eligible_regions_complete"]


def test_byte_budget_is_enforced():
    report = scan(
        FakeSession(b"\0" * 2048), observations(), max_bytes=512, chunk_size=256
    )
    assert report["coverage"]["attempted_bytes"] <= 512
    assert report["coverage"]["stop_reason"] == "byte_budget"


def test_elapsed_budget_prevents_reads():
    ticks = iter([0, 2, 2, 2])
    report = scan(
        FakeSession(b"\0" * 256),
        observations(),
        max_seconds=1,
        clock=lambda: next(ticks),
    )
    assert report["coverage"]["attempted_bytes"] == 0
    assert report["coverage"]["stop_reason"] == "time_budget"


def test_refinement_requires_changed_values_without_granting_qualification():
    session = FakeSession(struct.pack("<II", 439, 439))
    initial = scan(session, observations())
    session.content = struct.pack("<II", 440, 439)
    refined = refine(session, observations(440), initial)
    candidate = refined["candidates"]["position"]
    assert candidate["addresses"] == ["0x10000"]
    assert candidate["value_changed"]
    assert not refined["qualified"]


def test_refinement_rejects_restarted_process():
    session = FakeSession(struct.pack("<I", 439))
    initial = scan(session, observations())
    session.identity = {"pid": 1, "creation_time_100ns": 3}
    with pytest.raises(ValueError, match="different process session"):
        refine(session, observations(), initial)


def test_overlapping_match_search():
    assert list(find_matches(b"aaaa", b"aa", 10)) == [0, 1, 2]


@pytest.mark.parametrize(
    "kind,value",
    [("u16", -1), ("u16", 65536), ("xy_u32", [1]), ("f32", float("nan")), ("utf8", "")],
)
def test_bad_observations_rejected(kind, value):
    with pytest.raises(ValidationError):
        Observation(name="invalid", kind=kind, value=value)


def test_duplicate_observations_rejected():
    first = observations()
    with pytest.raises(ValidationError):
        ObservationSet(**{**first.model_dump(), "observations": first.observations * 2})


def test_xy_types_encode_as_explicit_little_endian():
    value = Observation(name="position", kind="xy_u16", value=[439, 372])
    assert value.encoded() == struct.pack("<HH", 439, 372)


def test_partial_final_chunk_does_not_claim_complete_coverage():
    report = scan(FakeSession(b"\0" * 200), observations(), max_bytes=50)
    assert report["coverage"]["attempted_bytes"] == 50
    assert not report["coverage"]["eligible_regions_complete"]


def test_neighborhood_discovery_excludes_distant_matches():
    data = bytearray(4096)
    data[100:104] = struct.pack("<I", 439)
    data[140:144] = struct.pack("<I", 51)
    data[3000:3004] = struct.pack("<I", 51)
    session = FakeSession(data)
    anchors = scan(session, observations())
    report = scan_near(session, observations(51), anchors, "position", radius=64)
    assert report["candidates"]["position"]["addresses"] == [hex(0x10000 + 140)]
    assert report["scope"]["matching_anchors"] == 1


def test_changed_anchor_is_rejected():
    session = FakeSession(struct.pack("<I", 439))
    anchors = scan(session, observations())
    session.content = struct.pack("<I", 440)
    with pytest.raises(ValueError, match="None of the previous anchors"):
        scan_near(session, observations(51), anchors, "position")
