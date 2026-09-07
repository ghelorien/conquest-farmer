"""Fail-closed preflight orchestration, separate from the Win32 adapter."""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from conquest.identity import fingerprint


class DiagnosticBackend(Protocol):
    def processes(self, executable: str) -> list[dict]: ...
    def windows(self, pid: int) -> list[dict]: ...
    def identity(self, pid: int) -> dict: ...
    def check_memory_access(self, pid: int) -> None: ...


@dataclass
class Check:
    name: str
    status: str
    detail: str
    winerror: int | None = None


@dataclass
class Report:
    schema_version: int = 1
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    stage: str = "read_only_preflight"
    gate: str = "blocked"
    autonomous_actions_enabled: bool = False
    target: dict = field(default_factory=dict)
    checks: list[Check] = field(default_factory=list)

    def add(self, name: str, status: str, detail: str, error: Exception | None = None):
        self.checks.append(Check(name, status, detail, getattr(error, "winerror", None)))

    def to_dict(self) -> dict:
        return asdict(self)


DEFERRED = (
    "game_state_validation",
    "background_movement",
    "background_attack",
    "background_potion",
    "background_pickup",
    "focus_and_cursor_isolation",
    "minimized_state_updates",
    "supervised_30_minute_farming",
)


def diagnose(
    backend: DiagnosticBackend,
    executable: str = "ImConquer.exe",
    pid: int | None = None,
    expected_sha256: str | None = None,
) -> Report:
    report = Report(target={"requested_executable": executable, "requested_pid": pid})
    try:
        candidates = backend.processes(executable)
        if pid is not None:
            candidates = [process for process in candidates if process["pid"] == pid]
        if len(candidates) != 1:
            report.target["candidates"] = candidates
            raise ValueError(
                "No matching process found" if not candidates
                else "Multiple matching processes found; select one with --pid"
            )
        selected = candidates[0]
        report.target.update(selected)
        selected_pid = selected["pid"]
        report.add("process_selection", "passed", "Exactly one matching process selected")
    except (OSError, ValueError) as error:
        report.add("process_selection", "failed", str(error), error)
        return finish(report)

    try:
        report.target["windows"] = backend.windows(selected_pid)
        visible = [window for window in report.target["windows"] if window["visible"]]
        report.add(
            "window_discovery", "passed" if visible else "failed",
            f"Found {len(visible)} visible top-level windows; no window was activated",
        )
    except OSError as error:
        report.add("window_discovery", "failed", str(error), error)

    identity = None
    try:
        identity = backend.identity(selected_pid)
        report.target["process_identity"] = identity
        if Path(identity["path"]).name.casefold() != executable.casefold():
            raise ValueError("Queried process image does not match the requested executable")
        report.add("process_identity", "passed", "Image path and creation time queried from process")
    except (OSError, ValueError) as error:
        report.add("process_identity", "failed", str(error), error)
        return finish(report)

    try:
        image = fingerprint(Path(identity["path"]))
        report.target["executable"] = image
        if image["architecture"] != identity["architecture"]:
            raise ValueError("On-disk architecture differs from the running process")
        report.add("executable_fingerprint", "passed", "SHA-256 and PE architecture read from disk")
        if expected_sha256 is not None:
            matches = image["sha256"] == expected_sha256.lower()
            report.add(
                "expected_fingerprint", "passed" if matches else "failed",
                "Executable matches expected SHA-256" if matches
                else "Executable fingerprint changed; existing integration profiles must be rejected",
            )
            if not matches:
                return finish(report)
    except (OSError, ValueError) as error:
        report.add("executable_fingerprint", "failed", str(error), error)
        return finish(report)

    try:
        backend.check_memory_access(selected_pid)
        report.add(
            "read_only_memory_access", "passed",
            "PROCESS_VM_READ handle opened and closed; game-state reads are not yet validated",
        )
    except OSError as error:
        report.add("read_only_memory_access", "failed", str(error), error)

    try:
        current = backend.identity(selected_pid)
        if current != identity:
            raise ValueError("Process identity changed during diagnostics; discard results and retry")
        report.add("process_identity_stable", "passed", "Process creation time and image identity remained stable")
    except (OSError, ValueError) as error:
        report.add("process_identity_stable", "failed", str(error), error)

    return finish(report)


def finish(report: Report) -> Report:
    """A successful access check is never proof of game-state or input compatibility."""
    failed = any(check.status == "failed" for check in report.checks)
    report.gate = "blocked" if failed else "unqualified"
    reason = (
        "Preflight failed; stopped at the feasibility gate without sending game input"
        if failed else
        "Preflight passed, but calibrated state and input validation are still required"
    )
    for name in DEFERRED:
        report.add(name, "not_run", reason)
    return report
