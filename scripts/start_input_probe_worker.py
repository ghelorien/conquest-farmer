"""Start a bounded four-hour worker for supervised input compatibility tests."""
import json
import sys
from pathlib import Path

import yaml

from conquest.diagnostics import diagnose
from conquest.win32 import WindowsBackend
from conquest.worker import request, serve


def main():
    root = Path(__file__).resolve().parents[1]
    profile = yaml.safe_load((root / 'profiles/classic-1074-player-candidate.yaml').read_text())
    expected = profile['expected_sha256']
    info = root / '.runtime/input-probe-worker.json'
    if info.exists():
        health = request(info, 'health')
        if (health.get('expected_sha256') == expected and health.get('read_only') is False
                and health.get('input_revision', 0) >= 7):
            return 0
        raise ValueError('Existing input worker is incompatible; inspect it before replacement')
    if '--check' in sys.argv:
        return 1
    report = diagnose(WindowsBackend(), expected_sha256=expected)
    if report.gate == 'blocked':
        raise ValueError('; '.join(c.detail for c in report.checks if c.status == 'failed'))
    windows = [window for window in report.target['windows'] if window['visible']]
    if len(windows) != 1:
        raise ValueError('Expected exactly one visible game window')
    serve(report.target['pid'], windows[0]['hwnd'], expected, info, lifetime=14400)


if __name__ == '__main__':
    if '--check' in sys.argv:
        try:
            raise SystemExit(main())
        except (ValueError, OSError, KeyError, TypeError):
            raise SystemExit(2)
    import contextlib
    import traceback
    logs = Path(__file__).resolve().parents[1] / 'reports'
    logs.mkdir(exist_ok=True)
    with (logs / 'input-probe-worker-startup.log').open('w') as log:
        with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            try:
                main()
            except Exception:
                traceback.print_exc()
                raise SystemExit(2)
