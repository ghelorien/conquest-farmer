"""Discover the current client and start a bounded worker with input disabled."""

import json
from pathlib import Path

from conquest.diagnostics import diagnose
from conquest.win32 import WindowsBackend
from conquest.worker import request, serve


def reusable_worker(root, expected):
    """Probe the authenticated worker before requesting another elevation."""
    try:
        health = request(root / ".runtime/memory-worker.json", "health")
        return (health.get("protocol_version") == 2
                and health.get("expected_sha256") == expected
                and health.get("read_only") is True
                and health.get("memory_read_revision", 0) >= 1)
    except (OSError, ValueError, KeyError, RuntimeError):
        return False


def main():
    root = Path(__file__).resolve().parents[1]
    import yaml
    profile = yaml.safe_load((root / "profiles/classic-1074-player-candidate.yaml").read_text())
    expected = profile["expected_sha256"]
    import sys
    reusable = reusable_worker(root, expected)
    if "--check" in sys.argv:
        return 0 if reusable else 1
    if reusable:
        return 0
    report = diagnose(WindowsBackend(), expected_sha256=expected)
    reports = root / "reports"
    reports.mkdir(exist_ok=True)
    (reports / "memory-worker-preflight.json").write_text(
        json.dumps(report.to_dict(), indent=2), encoding="utf-8")
    if report.gate == "blocked":
        failed = [check.detail for check in report.checks if check.status == "failed"]
        raise RuntimeError("; ".join(failed))
    windows = [window for window in report.target["windows"] if window["visible"]]
    if len(windows) != 1:
        raise RuntimeError("Expected exactly one visible client window")
    serve(report.target["pid"], windows[0]["hwnd"], expected,
          root / ".runtime/memory-worker.json", lifetime=3600, read_only=True)


if __name__ == "__main__":
    import sys
    if "--check" in sys.argv:
        raise SystemExit(main())
    import contextlib
    import traceback
    reports = Path(__file__).resolve().parents[1] / "reports"
    reports.mkdir(exist_ok=True)
    with (reports / "memory-worker-startup.log").open("w", encoding="utf-8") as log:
        with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            try:
                main()
            except Exception:
                traceback.print_exc()
                raise SystemExit(2)
