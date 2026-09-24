import importlib.util
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location(
    "worker_launcher", Path(__file__).parents[1] / "scripts/start_memory_worker.py"
)
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


def test_live_worker_is_reused_without_discovery_or_serve(monkeypatch):
    monkeypatch.setattr(
        launcher,
        "request",
        lambda *args: {
            "protocol_version": 2,
            "expected_sha256": "c2b53437ef68d687a1ef0f70c74bcf2df6027bf82b558e93330c839eb5e1c396",
            "read_only": True,
            "memory_read_revision": 1,
        },
    )

    def unexpected(*args, **kwargs):
        pytest.fail("An existing worker must not be replaced")

    monkeypatch.setattr(launcher, "diagnose", unexpected)
    monkeypatch.setattr(launcher, "serve", unexpected)
    monkeypatch.setattr("sys.argv", ["start_memory_worker.py"])
    assert launcher.main() == 0


@pytest.mark.parametrize(
    "change",
    [
        {"expected_sha256": "wrong"},
        {"read_only": False},
        {"memory_read_revision": 0},
        {"protocol_version": 1},
    ],
)
def test_incompatible_worker_is_not_reused(monkeypatch, tmp_path, change):
    reply = {
        "protocol_version": 2,
        "expected_sha256": "expected",
        "read_only": True,
        "memory_read_revision": 1,
        **change,
    }
    monkeypatch.setattr(launcher, "request", lambda *args: reply)
    assert not launcher.reusable_worker(tmp_path, "expected")


def test_missing_worker_check_does_not_start_one(monkeypatch):
    def absent(*args):
        raise FileNotFoundError()

    monkeypatch.setattr(launcher, "request", absent)
    monkeypatch.setattr("sys.argv", ["start_memory_worker.py", "--check"])
    assert launcher.main() == 1
