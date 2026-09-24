import hashlib
import json
import struct

import pytest

from conquest.cli import main
from conquest.diagnostics import DEFERRED, diagnose
from conquest.identity import fingerprint


@pytest.fixture
def executable(tmp_path):
    image = bytearray(256)
    image[:2] = b"MZ"
    struct.pack_into("<I", image, 0x3C, 0x80)
    image[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<H", image, 0x84, 0x8664)
    path = tmp_path / "ImConquer.exe"
    path.write_bytes(image)
    return path


class FakeBackend:
    def __init__(self, path, *, denied=False):
        self.path = str(path)
        self.denied = denied
        self.memory_calls = 0
        self.identity_calls = 0
        self.restart = False
        self.exit = False
        self.candidates = [{"pid": 123, "executable_name": "ImConquer.exe"}]

    def processes(self, executable):
        return self.candidates

    def windows(self, pid):
        return [{"hwnd": 100, "visible": True, "foreground": False, "minimized": False}]

    def identity(self, pid):
        self.identity_calls += 1
        if self.exit and self.identity_calls > 1:
            raise OSError("Process exited")
        return {
            "pid": pid,
            "path": self.path,
            "architecture": "x64",
            "creation_time_100ns": 101
            if self.restart and self.identity_calls > 1
            else 100,
        }

    def check_memory_access(self, pid):
        self.memory_calls += 1
        if self.denied:
            error = OSError("OpenProcess: Access is denied")
            error.winerror = 5
            raise error


def check(report, name):
    return next(item for item in report.checks if item.name == name)


def test_access_denied_blocks_all_game_actions(executable):
    report = diagnose(FakeBackend(executable, denied=True))
    assert report.gate == "blocked"
    assert not report.autonomous_actions_enabled
    assert check(report, "read_only_memory_access").winerror == 5
    assert all(check(report, name).status == "not_run" for name in DEFERRED)


def test_access_alone_does_not_qualify_farming(executable):
    report = diagnose(FakeBackend(executable))
    assert check(report, "read_only_memory_access").status == "passed"
    assert report.gate == "unqualified"
    assert not report.autonomous_actions_enabled
    assert check(report, "game_state_validation").status == "not_run"


def test_wrong_fingerprint_stops_before_memory_access(executable):
    backend = FakeBackend(executable)
    report = diagnose(backend, expected_sha256="0" * 64)
    assert report.gate == "blocked"
    assert check(report, "expected_fingerprint").status == "failed"
    assert backend.memory_calls == 0


def test_matching_fingerprint_is_not_automatic_qualification(executable):
    expected = hashlib.sha256(executable.read_bytes()).hexdigest()
    report = diagnose(FakeBackend(executable), expected_sha256=expected)
    assert check(report, "expected_fingerprint").status == "passed"
    assert report.gate == "unqualified"


@pytest.mark.parametrize("change", ["restart", "exit"])
def test_process_change_discards_session(executable, change):
    backend = FakeBackend(executable)
    setattr(backend, change, True)
    report = diagnose(backend)
    assert report.gate == "blocked"
    assert check(report, "process_identity_stable").status == "failed"


@pytest.mark.parametrize("candidates", [[], [123, 456]])
def test_missing_or_ambiguous_process_does_not_attach(executable, candidates):
    backend = FakeBackend(executable)
    backend.candidates = [
        {"pid": pid, "executable_name": "ImConquer.exe"} for pid in candidates
    ]
    report = diagnose(backend)
    assert check(report, "process_selection").status == "failed"
    assert backend.identity_calls == backend.memory_calls == 0


def test_explicit_pid_resolves_ambiguity(executable):
    backend = FakeBackend(executable)
    backend.candidates.append({"pid": 456, "executable_name": "ImConquer.exe"})
    report = diagnose(backend, pid=456)
    assert report.target["pid"] == 456
    assert check(report, "process_selection").status == "passed"


def test_wrong_image_name_prevents_attachment(executable):
    backend = FakeBackend(executable)
    backend.path = str(executable.with_name("Other.exe"))
    report = diagnose(backend)
    assert check(report, "process_identity").status == "failed"
    assert backend.memory_calls == 0


def test_missing_window_blocks_gate(executable):
    backend = FakeBackend(executable)
    backend.windows = lambda pid: []
    report = diagnose(backend)
    assert report.gate == "blocked"
    assert check(report, "window_discovery").status == "failed"


def test_fingerprint_detects_image_change(executable):
    first = fingerprint(executable)
    with executable.open("ab") as stream:
        stream.write(b"update")
    second = fingerprint(executable)
    assert first["architecture"] == "x64"
    assert first["sha256"] != second["sha256"]


@pytest.mark.parametrize("content", [b"", b"MZ", b"garbage", b"MZ" + b"\xff" * 100])
def test_malformed_executable_is_rejected(tmp_path, content):
    path = tmp_path / "bad.exe"
    path.write_bytes(content)
    with pytest.raises(ValueError):
        fingerprint(path)


def test_cli_saves_json_and_nonzero_gate_exit(
    executable, tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(
        "conquest.cli.WindowsBackend", lambda: FakeBackend(executable, denied=True)
    )
    output = tmp_path / "reports" / "diagnostics.json"
    assert main(["diagnose", "--output", str(output)]) == 2
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert json.loads(output.read_text()) == report
    assert report["gate"] == "blocked"
    events = [json.loads(line) for line in captured.err.splitlines()]
    assert [event["event"] for event in events] == [
        "diagnostic_started",
        "diagnostic_finished",
    ]


def test_cli_cannot_report_ready_from_access_check(executable, monkeypatch, capsys):
    monkeypatch.setattr("conquest.cli.WindowsBackend", lambda: FakeBackend(executable))
    assert main(["diagnose"]) == 3
    assert json.loads(capsys.readouterr().out)["autonomous_actions_enabled"] is False


@pytest.mark.parametrize(
    "arguments",
    [
        ["--pid", "0"],
        ["--pid", "-1"],
        ["--pid", "4294967296"],
        ["--expected-sha256", "invalid"],
    ],
)
def test_cli_rejects_invalid_parameters(arguments):
    with pytest.raises(SystemExit) as error:
        main(["diagnose", *arguments])
    assert error.value.code == 2
