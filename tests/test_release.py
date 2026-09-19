from contextlib import nullcontext
import json
import os
from pathlib import Path

import pytest

from conquest import release


def _release(root, name="release"):
    root = root / name
    root.mkdir(parents=True)
    (root / "pyproject.toml").write_text("[build-system]\nrequires=[]\n", encoding="utf-8")
    (root / "AGENTS.md").write_text("release test", encoding="utf-8")
    (root / "profiles").mkdir(parents=True)
    (root / "profiles" / "route.yaml").write_text("route: checked\n", encoding="utf-8")
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("print('release')\n", encoding="utf-8")
    (root / "scripts").mkdir()
    (root / "scripts" / "start_desktop_app.py").write_text("print('launch')\n", encoding="utf-8")
    release.write_manifest(root)
    return root


def _unlocked(_root):
    return nullcontext()


def test_manifest_is_exact_and_includes_profiles(tmp_path):
    root = _release(tmp_path)
    manifest = json.loads((root / release.MANIFEST).read_text())
    assert "profiles/route.yaml" in manifest["files"]
    assert release.verify_release(root)["file_count"] == 5
    (root / "profiles" / "route.yaml").write_text("route: changed\n", encoding="utf-8")
    with pytest.raises(release.ReleaseError, match="differs"):
        release.verify_release(root)
    (root / "profiles" / "route.yaml").write_text("route: checked\n", encoding="utf-8")
    (root / "added.txt").write_text("unexpected", encoding="utf-8")
    with pytest.raises(release.ReleaseError, match="exactly"):
        release.verify_release(root)
    (root / "added.txt").unlink()
    (root / "src" / "app.py").unlink()
    with pytest.raises(release.ReleaseError, match="exactly"):
        release.verify_release(root)


def test_manifest_and_release_roots_reject_reparse_points(tmp_path, monkeypatch):
    root = _release(tmp_path)
    real = release._reparse
    monkeypatch.setattr(release, "_reparse", lambda path: Path(path) == root or real(path))
    with pytest.raises(release.ReleaseError, match="reparse"):
        release.verify_release(root)

    monkeypatch.setattr(release, "_reparse", real)
    root = _release(tmp_path, "nested")
    linked = root / "profiles" / "link.yaml"
    linked.write_text("not really linked", encoding="utf-8")
    release.write_manifest(root)
    monkeypatch.setattr(release, "_reparse", lambda path: Path(path) == linked or real(path))
    with pytest.raises(release.ReleaseError, match="junction or symlink"):
        release.verify_release(root)


def test_live_state_and_local_profiles_are_not_release_content(tmp_path):
    root = tmp_path / "unsafe"
    (root / ".runtime").mkdir(parents=True)
    (root / ".runtime" / "state.json").write_text("{}", encoding="utf-8")
    with pytest.raises(release.ReleaseError, match="Live machine state"):
        release.build_manifest(root)
    local = tmp_path / "local"
    (local / "profiles").mkdir(parents=True)
    (local / "profiles" / "desktop.local.yaml").write_text("character: test", encoding="utf-8")
    with pytest.raises(release.ReleaseError, match="Live machine state"):
        release.build_manifest(local)


def test_build_uses_release_local_venv_and_noneditable_pip(tmp_path):
    source = _release(tmp_path, "source")
    commands = []

    def runner(command, **kwargs):
        commands.append((command, kwargs))
        if command[2] == "venv":
            venv = Path(command[3])
            executable = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            executable.parent.mkdir(parents=True)
            executable.write_text("fake", encoding="utf-8")

    result = release.build_release(source, tmp_path / "releases", "r1", runner=runner, python="build-python")
    assert result["release_id"] == "r1"
    assert (tmp_path / "releases" / "r1" / "profiles" / "route.yaml").exists()
    assert commands[0][0][:3] == ["build-python", "-m", "venv"]
    assert Path(commands[0][0][3]).parents[1] == tmp_path / "releases"
    pip = commands[1][0]
    assert pip[1:4] == ["-m", "pip", "install"]
    assert "-e" not in pip and "." == pip[-1]
    assert commands[1][1]["cwd"] != str(source)


def test_activation_is_atomic_and_retains_verified_rollback(tmp_path):
    first = _release(tmp_path, "first")
    second = _release(tmp_path, "second")
    state = tmp_path / "state"
    active = release.activate_release(first, state_root=state, lock=_unlocked)
    assert active["previous"] is None
    before = (state / release.ACTIVE).read_bytes()
    with pytest.raises(RuntimeError, match="crash"):
        release.activate_release(second, state_root=state, lock=_unlocked,
                                 crash_hook=lambda stage: (_ for _ in ()).throw(RuntimeError("crash")))
    assert (state / release.ACTIVE).read_bytes() == before
    current = release.active_release(state_root=state)
    assert Path(current["release_root"]) == first
    release.activate_release(second, state_root=state, lock=_unlocked)
    reverted = release.rollback_release(state_root=state, lock=_unlocked)
    assert Path(reverted["release_root"]) == first
    assert (state / release.ACTIVE).exists() and second.exists()


def test_activation_never_places_managed_state_inside_release(tmp_path):
    root = _release(tmp_path)
    with pytest.raises(release.ReleaseError, match="separate"):
        release.activate_release(root, state_root=root / "machine-state", lock=_unlocked)


def test_activation_rejects_release_nested_in_managed_state_but_allows_siblings(tmp_path):
    state = tmp_path / "state"
    nested = _release(state, "releases/current")
    with pytest.raises(release.ReleaseError, match="separate"):
        release.activate_release(nested, state_root=state, lock=_unlocked)
    sibling = _release(tmp_path, "release")
    activated = release.activate_release(sibling, state_root=state, lock=_unlocked)
    assert Path(activated["release_root"]) == sibling
    assert Path(release.active_release(state_root=state)["release_root"]) == sibling
    nested_receipt = {"schema_version": release.SCHEMA, "release_root": str(nested),
                      "manifest_sha256": release.verify_release(nested)["manifest_sha256"],
                      "previous": None}
    release.write_json(state / release.ACTIVE, nested_receipt)
    with pytest.raises(release.ReleaseError, match="separate"):
        release.active_release(state_root=state)
    release.write_json(state / release.ACTIVE, {**activated, "previous": nested_receipt})
    with pytest.raises(release.ReleaseError, match="separate"):
        release.rollback_release(state_root=state, lock=_unlocked)


def test_active_release_pins_manifest_and_launcher_uses_release_cwd(tmp_path):
    root = _release(tmp_path)
    executable = root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    executable.parent.mkdir(parents=True)
    executable.write_text("fake", encoding="utf-8")
    launcher = root / "scripts" / "start_desktop_app.py"
    launcher.parent.mkdir(exist_ok=True); launcher.write_text("", encoding="utf-8")
    state = tmp_path / "state"
    release.write_manifest(root)
    release.activate_release(root, state_root=state, lock=_unlocked)
    calls = []
    release.launch_active_release(("--manage-profiles",), state_root=state,
                                  popen=lambda *args, **kwargs: calls.append((args, kwargs)))
    command, options = calls[0]
    assert command[0][0] == str(executable)
    assert command[0][1] == "-B"
    assert options["cwd"] == str(root)
    assert options["env"]["CONQUEST_DATA_ROOT"] == str(state)
    assert options["env"]["PYTHONDONTWRITEBYTECODE"] == "1"
    (root / "profiles" / "route.yaml").write_text("tampered", encoding="utf-8")
    with pytest.raises(release.ReleaseError, match="differs"):
        release.active_release(state_root=state)


def test_changed_manifest_or_activation_receipt_is_rejected(tmp_path):
    root = _release(tmp_path)
    state = tmp_path / "state"
    release.activate_release(root, state_root=state, lock=_unlocked)
    (root / release.MANIFEST).write_text("{}", encoding="utf-8")
    with pytest.raises(release.ReleaseError, match="differs"):
        release.active_release(state_root=state)
    # Restore a valid release then prove the external receipt is validated too.
    release.write_manifest(root)
    release.activate_release(root, state_root=state, lock=_unlocked)
    receipt = json.loads((state / release.ACTIVE).read_text())
    receipt["manifest_sha256"] = "0" * 64
    (state / release.ACTIVE).write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(release.ReleaseError, match="activated manifest"):
        release.active_release(state_root=state)


def test_build_selects_only_release_payload_and_excludes_worktree_state(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "pyproject.toml").write_text("[build-system]\nrequires=[]\n", encoding="utf-8")
    (source / "AGENTS.md").write_text("rules", encoding="utf-8")
    for name in ("src", "scripts", "profiles"):
        (source / name).mkdir()
    (source / "src" / "app.py").write_text("app", encoding="utf-8")
    (source / "scripts" / "start_desktop_app.py").write_text("launch", encoding="utf-8")
    (source / "profiles" / "public.yaml").write_text("public", encoding="utf-8")
    (source / "profiles" / "desktop.local.yaml").write_text("private", encoding="utf-8")
    (source / ".runtime").mkdir(); (source / ".runtime" / "state.json").write_text("state", encoding="utf-8")
    (source / "reports").mkdir(); (source / "reports" / "receipt.json").write_text("state", encoding="utf-8")
    (source / "scratch.txt").write_text("arbitrary", encoding="utf-8")
    commands = []
    def runner(command, **kwargs):
        commands.append(command)
        if command[2] == "venv":
            executable = Path(command[3]) / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            executable.parent.mkdir(parents=True); executable.write_text("fake", encoding="utf-8")
    result = release.build_release(source, tmp_path / "releases", "payload", runner=runner)
    output = Path(result["root"])
    assert (output / "profiles" / "public.yaml").exists()
    assert not (output / "profiles" / "desktop.local.yaml").exists()
    assert not (output / ".runtime").exists() and not (output / "reports").exists()
    assert not (output / "scratch.txt").exists() and not (output / ".git").exists()
