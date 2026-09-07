import copy
import ctypes
import json
import os
import sqlite3
import struct
from pathlib import Path

import pytest

from conquest.cli import main
from conquest.observation import CandidateReader
from conquest.recording import Recorder, record


class Clock:
    now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class Session:
    identity = {"pid": 1, "creation_time_100ns": 2}
    expected_sha256 = "a" * 64
    hp = 51
    position = (430, 380)
    checks = 0
    restart_on = None
    short = False

    def assert_identity(self):
        self.checks += 1
        if self.checks == self.restart_on:
            raise ValueError("Process restarted")

    def read(self, address, size):
        if self.short:
            return b"\0"
        return struct.pack("<I", self.hp) if address == 0x10000 else struct.pack("<II", *self.position)


@pytest.fixture
def report():
    return {"schema_version": 1, "process_identity": copy.deepcopy(Session.identity),
            "expected_sha256": Session.expected_sha256,
            "candidates": {"hp_u32": {"kind": "u32", "addresses": ["0x10000"], "truncated": False},
                           "position_u32": {"kind": "xy_u32", "addresses": ["0x20000"], "truncated": False}}}


def test_values_read_from_memory_remain_candidates(report):
    sample = CandidateReader(Session(), report).read()
    assert sample.read_ok and not sample.qualified and not sample.atomic
    assert sample.fields[0].value == [51]
    assert sample.fields[1].value == [430, 380]
    assert sample.finished_monotonic >= sample.started_monotonic


@pytest.mark.parametrize("field,value", [("expected_sha256", "b" * 64), ("process_identity", {"pid": 1})])
def test_changed_client_or_restart_rejected_before_read(report, field, value):
    report[field] = value
    with pytest.raises(ValueError):
        CandidateReader(Session(), report)


def test_restart_mid_sample_discards_all_values(report):
    session = Session()
    session.restart_on = 2
    sample = CandidateReader(session, report).read()
    assert not sample.read_ok
    assert all(field.value is None and not field.read_ok for field in sample.fields)
    assert "restarted" in sample.error


def test_short_read_never_reuses_previous_value(report):
    session = Session()
    reader = CandidateReader(session, report)
    assert reader.read().read_ok
    session.short = True
    sample = reader.read()
    assert not sample.read_ok
    assert all(field.value is None for field in sample.fields)


@pytest.mark.parametrize("addresses", [["0x10"], [], ["0x10000"] * 65, ["0x10000", "0x10000"]])
def test_invalid_candidate_addresses_rejected(report, addresses):
    report["candidates"]["hp_u32"]["addresses"] = addresses
    with pytest.raises(ValueError):
        CandidateReader(Session(), report)


def test_nonfinite_float_is_invalid(report):
    report["candidates"]["hp_u32"]["kind"] = "f32"
    session = Session()
    session.hp = 0x7FC00000  # IEEE NaN bit pattern
    sample = CandidateReader(session, report).read()
    assert not sample.read_ok and sample.fields[0].value is None


def test_recording_retains_changes_and_outcome(report, tmp_path):
    clock, session = Clock(), Session()
    recorder = Recorder(tmp_path / "trace.sqlite")
    def next_state(_):
        session.hp -= 1
        session.position = (session.position[0] + 1, session.position[1])
    try:
        result = record(CandidateReader(session, report, clock=clock), recorder, {},
                        seconds=1, interval=0.25, clock=clock, sleep=clock.sleep, on_sample=next_state)
        assert result["samples"] == 4
        assert result["candidate_change_counts"] == {"hp_u32@0x10000": 3, "position_u32@0x20000": 3}
        assert result["stop_reason"] == "duration_elapsed" and not result["qualified"]
        saved = recorder.db.execute("SELECT sample_json FROM observation_samples ORDER BY sequence").fetchall()
        assert json.loads(saved[-1][0])["fields"][0]["value"] == [48]
        assert recorder.db.execute("SELECT stop_reason FROM observation_sessions").fetchone()[0] == "duration_elapsed"
    finally:
        recorder.close()


def test_invalid_read_is_saved_then_stops(report, tmp_path):
    clock, session = Clock(), Session()
    session.short = True
    recorder = Recorder(tmp_path / "trace.sqlite")
    try:
        result = record(CandidateReader(session, report, clock=clock), recorder, {},
                        clock=clock, sleep=clock.sleep)
        assert result["samples"] == 1 and result["stop_reason"] == "invalid_observation"
        assert clock.now == 0
    finally:
        recorder.close()


def test_emergency_stop_responsive_during_long_interval(report, tmp_path):
    clock = Clock()
    recorder = Recorder(tmp_path / "trace.sqlite")
    try:
        result = record(CandidateReader(Session(), report), recorder, {}, interval=10,
                        clock=clock, sleep=clock.sleep, should_stop=lambda: clock.now >= 0.1)
        assert result["samples"] == 1 and result["stop_reason"] == "emergency_stop"
        assert clock.now <= 0.15
    finally:
        recorder.close()


def test_keyboard_interrupt_finalizes_recording(report, tmp_path):
    recorder = Recorder(tmp_path / "trace.sqlite")
    def interrupt(_):
        raise KeyboardInterrupt
    try:
        result = record(CandidateReader(Session(), report), recorder, {}, on_sample=interrupt)
        assert result["stop_reason"] == "interrupted"
    finally:
        recorder.close()


def test_cli_rejects_bad_timing_without_opening_process(tmp_path, capsys):
    assert main(["observe", "--pid", "1", "--watch", "absent.json", "--database", str(tmp_path / "trace.sqlite"),
                 "--seconds", "nan"]) == 2
    report = json.loads(capsys.readouterr().out)
    assert "--seconds" in report["error"]
    assert not (tmp_path / "trace.sqlite").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows memory integration")
def test_real_readonly_memory_recording_of_test_process(tmp_path):
    from conquest.identity import fingerprint
    from conquest.memory import MemorySession
    from conquest.win32 import WindowsBackend

    hp = ctypes.c_uint32(51)
    position = (ctypes.c_uint32 * 2)(430, 380)
    image_path = Path(WindowsBackend().identity(os.getpid())["path"])
    digest = fingerprint(image_path)["sha256"]
    with MemorySession(os.getpid(), digest, executable=image_path.name) as session:
        report = {"process_identity": session.identity, "expected_sha256": digest, "candidates": {
            "hp_u32": {"kind": "u32", "addresses": [hex(ctypes.addressof(hp))], "truncated": False},
            "position_u32": {"kind": "xy_u32", "addresses": [hex(ctypes.addressof(position))], "truncated": False}}}
        reader = CandidateReader(session, report)
        assert reader.read().fields[0].value == [51]
        hp.value, position[0] = 49, 431
        recorder = Recorder(tmp_path / "real.sqlite")
        try:
            result = record(reader, recorder, {"process_identity": session.identity}, seconds=0.1, interval=0.05)
            assert result["stop_reason"] == "duration_elapsed"
        finally:
            recorder.close()
    with sqlite3.connect(tmp_path / "real.sqlite") as db:
        saved = json.loads(db.execute("SELECT sample_json FROM observation_samples ORDER BY sequence LIMIT 1").fetchone()[0])
        assert saved["fields"][0]["value"] == [49]
        assert saved["fields"][1]["value"] == [431, 380]
