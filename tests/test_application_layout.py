from pathlib import Path
from types import SimpleNamespace
import importlib.util
import os
import subprocess
import sys

import pytest

from conquest import application_layout as layout
from conquest import release, route_controller


def app(root):
    for name in (
        "scripts",
        "src/conquest",
        "profiles/routes",
        ".venv/Scripts",
        ".venv/bin",
    ):
        (root / name).mkdir(parents=True, exist_ok=True)
    for name in (
        "pyproject.toml",
        "scripts/start_desktop_app.py",
        "scripts/run_overnight.py",
        ".venv/Scripts/python.exe",
        ".venv/Scripts/pythonw.exe",
        ".venv/bin/python",
    ):
        (root / name).touch()
    return root


@pytest.fixture(autouse=True)
def no_authority(monkeypatch):
    monkeypatch.delenv(layout.APP_ROOT, raising=False)
    monkeypatch.delenv(layout.MANIFEST_PIN, raising=False)


def test_installed_package_never_guesses_from_cwd_or_site_packages(
    tmp_path, monkeypatch
):
    root = app(tmp_path / "release")
    monkeypatch.setattr(
        layout,
        "__file__",
        str(tmp_path / ".venv/Lib/site-packages/conquest/application_layout.py"),
    )
    monkeypatch.chdir(root)
    with pytest.raises(ValueError, match="explicit CONQUEST_APP_ROOT"):
        layout.application_root()
    assert layout.application_root(root) == root
    monkeypatch.setenv(layout.APP_ROOT, str(root))
    assert layout.application_root() == root
    with pytest.raises(ValueError, match="differs"):
        layout.application_root(tmp_path / "other")


def test_immutable_controller_uses_pinned_script_interpreter_and_external_state(
    tmp_path, monkeypatch
):
    root = app(tmp_path / "release")
    state = tmp_path / "state"
    state.mkdir()
    release.write_manifest(root)
    digest = release.verify_release(root)["manifest_sha256"]
    before = (root / release.MANIFEST).read_bytes()
    monkeypatch.setenv(layout.APP_ROOT, str(root))
    monkeypatch.setenv(layout.MANIFEST_PIN, digest)
    monkeypatch.setenv("CONQUEST_DATA_ROOT", str(state))
    monkeypatch.setattr(
        layout,
        "__file__",
        str(root / ".venv/Lib/site-packages/conquest/application_layout.py"),
    )
    monkeypatch.setattr(
        route_controller, "state_path", lambda value: str(state / value)
    )
    from conquest import routes

    monkeypatch.setattr(
        routes,
        "RouteLibrary",
        lambda directory: SimpleNamespace(load=lambda route: None),
    )
    calls = []
    monkeypatch.setattr(
        route_controller.subprocess,
        "Popen",
        lambda *a, **kw: calls.append((a, kw)) or SimpleNamespace(pid=8),
    )
    assert route_controller.ensure_running("bandit")
    assert len(calls) == 1
    argv = calls[0][0][0]
    options = calls[0][1]
    assert Path(argv[0]) == root / (
        ".venv/Scripts/pythonw.exe" if os.name == "nt" else ".venv/bin/python"
    )
    assert argv[1:] == [
        "-B",
        str(root / "scripts/run_overnight.py"),
        "--route",
        "bandit",
    ]
    assert options["cwd"] == root and options["env"][layout.APP_ROOT] == str(root)
    assert options["env"][layout.MANIFEST_PIN] == digest
    assert options["env"]["CONQUEST_DATA_ROOT"] == str(state.resolve())
    assert (state / ".runtime/route-controller-launch.json").exists()
    assert (root / release.MANIFEST).read_bytes() == before
    release.verify_release(root, expected_manifest_sha256=digest)


@pytest.mark.parametrize("fault", ["script", "python", "hash", "reparse", "mismatch"])
def test_invalid_launch_fails_before_stop_log_or_process_mutation(
    tmp_path, monkeypatch, fault
):
    root = app(tmp_path / "release")
    state = tmp_path / "state"
    state.mkdir()
    release.write_manifest(root)
    digest = release.verify_release(root)["manifest_sha256"]
    monkeypatch.setenv(layout.APP_ROOT, str(root))
    monkeypatch.setenv(layout.MANIFEST_PIN, digest)
    monkeypatch.setenv("CONQUEST_DATA_ROOT", str(state))
    monkeypatch.setattr(
        route_controller, "state_path", lambda value: str(state / value)
    )
    calls = []
    monkeypatch.setattr(
        route_controller.subprocess, "Popen", lambda *a, **kw: calls.append(a)
    )
    if fault == "script":
        (root / "scripts/run_overnight.py").unlink()
    if fault == "python":
        (
            root
            / (".venv/Scripts/pythonw.exe" if os.name == "nt" else ".venv/bin/python")
        ).unlink()
    if fault == "hash":
        monkeypatch.setenv(layout.MANIFEST_PIN, "0" * 64)
    if fault == "reparse":
        original = layout.real_path

        def checked(value):
            if Path(value) == root:
                raise ValueError("reparse")
            return original(value)

        monkeypatch.setattr(layout, "real_path", checked)
    with pytest.raises(ValueError):
        route_controller.ensure_running(
            "bandit", root=tmp_path / "other" if fault == "mismatch" else None
        )
    assert not calls and not list(state.iterdir())


def test_release_cannot_be_used_without_its_manifest_pin(tmp_path, monkeypatch):
    root = app(tmp_path / "release")
    release.write_manifest(root)
    with pytest.raises(ValueError, match="pin"):
        layout.application_root(root)


def test_live_controller_heartbeat_does_not_rehash_entire_release(
    tmp_path, monkeypatch
):
    root = app(tmp_path / "release")
    state = tmp_path / "state"
    state.mkdir()
    release.write_manifest(root)
    digest = release.verify_release(root)["manifest_sha256"]
    monkeypatch.setenv(layout.APP_ROOT, str(root))
    monkeypatch.setenv(layout.MANIFEST_PIN, digest)
    monkeypatch.setenv("CONQUEST_DATA_ROOT", str(state))
    monkeypatch.setattr(
        route_controller, "state_path", lambda value: str(state / value)
    )
    monkeypatch.setattr(
        route_controller,
        "read_json",
        lambda path: (
            {"phase": "hunting", "pid": 1, "updated_at": 99}
            if Path(path).name == "status.json"
            else {}
        ),
    )
    monkeypatch.setattr(route_controller.time, "time", lambda: 100)
    monkeypatch.setattr(route_controller, "process_alive", lambda _: True)
    monkeypatch.setattr(
        release,
        "verify_release",
        lambda *a, **k: pytest.fail("full payload reread on heartbeat"),
    )
    assert not route_controller.ensure_running("bandit")


def test_immutable_layout_requires_external_state_before_launch(tmp_path, monkeypatch):
    root = app(tmp_path / "release")
    release.write_manifest(root)
    monkeypatch.setenv(layout.APP_ROOT, str(root))
    monkeypatch.setenv(
        layout.MANIFEST_PIN, release.verify_release(root)["manifest_sha256"]
    )
    monkeypatch.delenv("CONQUEST_DATA_ROOT", raising=False)
    with pytest.raises(ValueError, match="managed state root"):
        layout.RuntimeLayout.resolve()
    monkeypatch.setenv("CONQUEST_DATA_ROOT", str(root / "state"))
    with pytest.raises(ValueError, match="separate"):
        layout.RuntimeLayout.resolve()


def test_runtime_layout_keeps_resolved_data_root_for_children(tmp_path, monkeypatch):
    root = app(tmp_path / "release")
    state = tmp_path / "state"
    other = tmp_path / "store-cache"
    release.write_manifest(root)
    digest = release.verify_release(root)["manifest_sha256"]
    monkeypatch.setenv(layout.APP_ROOT, str(root))
    monkeypatch.setenv(layout.MANIFEST_PIN, digest)
    monkeypatch.setenv("CONQUEST_DATA_ROOT", str(state))
    runtime = layout.RuntimeLayout.resolve()
    # The launcher can no longer redirect a previously qualified worker by
    # changing its ambient data-root environment after layout resolution.
    monkeypatch.setenv("CONQUEST_DATA_ROOT", str(other))
    assert runtime.environment()["CONQUEST_DATA_ROOT"] == str(state.resolve())


def test_full_verification_failure_backoff_never_writes_or_caches_success(
    tmp_path, monkeypatch
):
    root = app(tmp_path / "release")
    state = tmp_path / "state"
    state.mkdir()
    release.write_manifest(root)
    digest = release.verify_release(root)["manifest_sha256"]
    monkeypatch.setenv(layout.APP_ROOT, str(root))
    monkeypatch.setenv(layout.MANIFEST_PIN, digest)
    monkeypatch.setenv("CONQUEST_DATA_ROOT", str(state))
    monkeypatch.setattr(
        route_controller, "state_path", lambda value: str(state / value)
    )
    now = [100]
    monkeypatch.setattr(layout.time, "monotonic", lambda: now[0])
    calls = []

    def invalid(*args, **kwargs):
        calls.append(1)
        raise release.ReleaseError("Release file differs from the manifest: payload")

    monkeypatch.setattr(release, "verify_release", invalid)
    for second in (100, 102, 140):
        now[0] = second
        with pytest.raises(release.ReleaseError, match="payload"):
            route_controller.ensure_running("bandit")
    assert len(calls) == 1 and not list(state.iterdir())
    now[0] = 145
    with pytest.raises(release.ReleaseError, match="payload"):
        route_controller.ensure_running("bandit")
    assert len(calls) == 2 and not list(state.iterdir())
    now[0] = 190
    monkeypatch.setattr(release, "verify_release", lambda *a, **k: calls.append(1))
    layout.RuntimeLayout.resolve()
    layout.RuntimeLayout.resolve()
    assert len(calls) == 4  # Each successful launch attempt obtains a fresh proof.


def test_failed_full_verification_is_single_flight_per_root_and_pin(
    tmp_path, monkeypatch
):
    from concurrent.futures import ThreadPoolExecutor
    import threading

    entered = threading.Event()
    finish = threading.Event()
    calls = []

    def invalid(*args, **kwargs):
        calls.append(1)
        entered.set()
        assert finish.wait(5)
        raise release.ReleaseError("integrity failure")

    monkeypatch.setattr(release, "verify_release", invalid)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(layout._verify_release_for_launch, tmp_path, "pin")
        assert entered.wait(5)
        second = pool.submit(layout._verify_release_for_launch, tmp_path, "pin")
        finish.set()
        for pending in (first, second):
            with pytest.raises(release.ReleaseError, match="integrity failure"):
                pending.result()
    assert len(calls) == 1
    with pytest.raises(release.ReleaseError):
        layout._verify_release_for_launch(tmp_path, "new-pin")
    assert len(calls) == 2


def test_entrypoint_rejects_wrong_root_before_conquest_imports(tmp_path):
    root = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment[layout.APP_ROOT] = str(tmp_path)
    environment.pop(layout.MANIFEST_PIN, None)
    result = subprocess.run(
        [sys.executable, "-B", str(root / "scripts/run_overnight.py"), "--help"],
        env=environment,
        capture_output=True,
        text=True,
    )
    assert result.returncode and "differs from CONQUEST_APP_ROOT" in result.stderr
    assert "conquest.overnight" not in result.stderr
