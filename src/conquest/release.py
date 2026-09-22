"""Build, verify, activate, and launch immutable Conquest releases.

Release files never carry machine state.  Profiles and runtime receipts belong in
``%LOCALAPPDATA%\\Conquest`` and are selected by the existing profile bootstrap.
The release manifest deliberately excludes only itself: hashing a file which
contains its own hash is not possible.  The active-state receipt pins that
manifest's digest, so changing the manifest after activation is detected.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import uuid

from conquest.character_profiles import data_root, write_json


MANIFEST = "release-manifest.json"
ACTIVE = "active-release.json"
SCHEMA = 1
_RELEASE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
# These names are state namespaces at the release root, never application code.
_LIVE_ROOTS = frozenset((".runtime", "reports", "machine-state", "accounts", "characters"))
_LIVE_FILES = frozenset(("profiles.json", "machine.json", "migration.json", "app.lock", "profiles.lock"))
_BUILD_IGNORES = frozenset((".git", ".venv", "__pycache__", ".pytest_cache", "build", "dist", "*.egg-info"))
# Deliberate allowlist rather than a copy of an arbitrary working tree.  The
# launcher, package metadata, code, and packaged profiles are mandatory; the
# other entries are retained support/documentation artifacts, not live state.
_RELEASE_FILES = ("pyproject.toml", "README.md", "AGENTS.md")
_RELEASE_DIRECTORIES = ("src", "scripts", "profiles", "docs", "data")


class ReleaseError(ValueError):
    """A release is incomplete, unsafe, or does not exactly match its manifest."""


def _reparse(path: Path) -> bool:
    info = os.lstat(path)
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _absolute(path) -> Path:
    # Do not resolve before checking reparse status: resolve would bless a junction.
    return Path(os.path.abspath(path))


def _within(child: Path, parent: Path) -> bool:
    try:
        child.resolve(strict=False).relative_to(parent.resolve(strict=False))
        return True
    except ValueError:
        return False


def _require_separate_roots(state_root: Path, release: Path) -> None:
    """Managed state and immutable code must be disjoint sibling trees."""
    if _within(state_root, release) or _within(release, state_root):
        raise ReleaseError("Managed machine state and release roots must be separate trees")


def _assert_real_tree(root, *, live_state=False) -> list[Path]:
    root = _absolute(root)
    if not root.is_dir() or _reparse(root):
        raise ReleaseError("Release root must be a real local directory, not a reparse point")
    files: list[Path] = []
    for current, directories, names in os.walk(root, followlinks=False):
        current = Path(current)
        if _reparse(current):
            raise ReleaseError("Release contains a junction or symlink")
        for name in list(directories):
            path = current / name
            if _reparse(path):
                raise ReleaseError("Release contains a junction or symlink")
            relative = path.relative_to(root)
            if live_state and len(relative.parts) == 1 and name in _LIVE_ROOTS:
                raise ReleaseError("Live machine state must not be included in a release")
        for name in names:
            path = current / name
            if _reparse(path):
                raise ReleaseError("Release contains a junction or symlink")
            relative = path.relative_to(root)
            if live_state and _is_live_state(relative):
                raise ReleaseError("Live machine state must not be included in a release")
            files.append(path)
    return files


def _is_live_state(relative: Path) -> bool:
    if relative.name in _LIVE_FILES or relative.suffix.casefold() in (".dpapi", ".sqlite", ".sqlite3"):
        return True
    if relative.parts and relative.parts[0] == "profiles" and relative.name.endswith(".local.yaml"):
        return True
    return False


def _assert_source_payload(source: Path) -> None:
    """Check selected code and excluded live state without copying either blindly."""
    source = _absolute(source)
    if not source.is_dir() or _reparse(source):
        raise ReleaseError("Release source must be a real local directory, not a reparse point")
    # State is intentionally excluded, but it still may not be a link that
    # disguises another location as local release input.
    for name in _LIVE_ROOTS:
        candidate = source / name
        if candidate.exists():
            _assert_real_tree(candidate)
    profiles = source / "profiles"
    if profiles.exists():
        for local in profiles.rglob("*.local.yaml"):
            if _reparse(local):
                raise ReleaseError("Live machine state contains a junction or symlink")
    for name in _RELEASE_FILES:
        candidate = source / name
        if name == "README.md" and not candidate.exists():
            continue
        if not candidate.is_file() or _reparse(candidate):
            raise ReleaseError("Required release file is missing or unsafe: " + name)
    for name in _RELEASE_DIRECTORIES:
        candidate = source / name
        if name in ("docs", "data") and not candidate.exists():
            continue
        if not candidate.is_dir() or _reparse(candidate):
            raise ReleaseError("Required release directory is missing or unsafe: " + name)
        _assert_real_tree(candidate)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _manifest_digest(path: Path) -> str:
    return _sha256(path)


def build_manifest(root) -> dict:
    """Create an exact manifest of every release file, including ``profiles/``."""
    root = _absolute(root)
    records = {}
    for path in _assert_real_tree(root, live_state=True):
        relative = path.relative_to(root).as_posix()
        if relative == MANIFEST:
            continue
        records[relative] = {"sha256": _sha256(path), "size": path.stat().st_size}
    return {"schema_version": SCHEMA, "files": dict(sorted(records.items()))}


def write_manifest(root) -> dict:
    root = _absolute(root)
    manifest = build_manifest(root)
    write_json(root / MANIFEST, manifest)
    return manifest


def verify_release(root, *, expected_manifest_sha256: str | None = None) -> dict:
    """Reject added, missing, changed, and reparse-point files exactly."""
    root = _absolute(root)
    files = _assert_real_tree(root, live_state=True)
    manifest_path = root / MANIFEST
    if not manifest_path.is_file() or _reparse(manifest_path):
        raise ReleaseError("Release manifest is missing or unsafe")
    digest = _manifest_digest(manifest_path)
    if expected_manifest_sha256 is not None and digest != expected_manifest_sha256:
        raise ReleaseError("Release manifest differs from the activated manifest")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ReleaseError("Release manifest is unreadable") from error
    if set(manifest) != {"schema_version", "files"} or manifest["schema_version"] != SCHEMA:
        raise ReleaseError("Unsupported release manifest")
    listed = manifest["files"]
    if not isinstance(listed, dict):
        raise ReleaseError("Invalid release manifest files")
    actual = {path.relative_to(root).as_posix(): path for path in files
              if path.relative_to(root).as_posix() != MANIFEST}
    if set(actual) != set(listed):
        raise ReleaseError("Release files do not exactly match the manifest")
    for relative, record in listed.items():
        if not isinstance(record, dict) or set(record) != {"sha256", "size"}:
            raise ReleaseError("Invalid release manifest entry")
        path = actual[relative]
        if type(record["size"]) is not int or path.stat().st_size != record["size"]:
            raise ReleaseError("Release file size differs from the manifest: " + relative)
        if not isinstance(record["sha256"], str) or _sha256(path) != record["sha256"]:
            raise ReleaseError("Release file differs from the manifest: " + relative)
    return {"root": str(root), "manifest_sha256": digest, "file_count": len(actual)}


def _ignore_build_artifacts(directory, names):
    ignored = set()
    for name in names:
        if (name in _BUILD_IGNORES or name.endswith(".egg-info") or name == MANIFEST
                or name.endswith(".local.yaml")):
            ignored.add(name)
    return ignored


def _copy_release_payload(source: Path, stage: Path) -> None:
    stage.mkdir()
    for name in _RELEASE_FILES:
        candidate = source / name
        if candidate.exists():
            shutil.copy2(candidate, stage / name)
    for name in _RELEASE_DIRECTORIES:
        candidate = source / name
        if candidate.exists():
            shutil.copytree(candidate, stage / name, ignore=_ignore_build_artifacts)


def _venv_python(root: Path) -> Path:
    return root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _default_run(command, **kwargs):
    return subprocess.run(command, check=True, **kwargs)


def build_release(source, releases_root, release_id, *, runner=_default_run, python=None,
                  crash_hook=None) -> dict:
    """Stage a release, install it non-editably, verify it, then publish atomically.

    ``runner`` exists solely for offline tests; normal builds invoke the local
    interpreter and pip and may download declared production dependencies.
    """
    source = _absolute(source)
    _assert_source_payload(source)
    releases_root = _absolute(releases_root)
    if releases_root.exists() and _reparse(releases_root):
        raise ReleaseError("Release parent is a reparse point")
    if not _RELEASE_ID.fullmatch(release_id):
        raise ReleaseError("Release identifier must be a short filesystem-safe identifier")
    target = releases_root / release_id
    if target.exists() or _within(releases_root, source):
        raise ReleaseError("Release destination already exists or is inside the source tree")
    releases_root.mkdir(parents=True, exist_ok=True)
    stage = releases_root / ("." + release_id + ".staging-" + uuid.uuid4().hex)
    try:
        _copy_release_payload(source, stage)
        _assert_real_tree(stage, live_state=True)
        executable = str(python or sys.executable)
        runner([executable, "-m", "venv", str(stage / ".venv")])
        venv_python = _venv_python(stage)
        # Deliberately no ``-e``: code and production dependencies are installed
        # into the release-local venv, never borrowed from a developer checkout.
        runner([str(venv_python), "-m", "pip", "install", "--no-input", "."], cwd=str(stage))
        write_manifest(stage)
        verified = verify_release(stage)
        if crash_hook:
            crash_hook("verified")
        os.replace(stage, target)
        return {**verified, "root": str(target), "release_id": release_id}
    except Exception:
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)
        raise


def _state_lock(state_root, lock):
    if lock is not None:
        return lock(state_root)
    from conquest.profile_bootstrap import managed_root_owner
    return managed_root_owner(state_root)


def _state_root(value=None) -> Path:
    root = _absolute(value if value is not None else data_root())
    if root.exists() and _reparse(root):
        raise ReleaseError("Managed state root is a reparse point")
    return root


def _active_path(state_root: Path) -> Path:
    return state_root / ACTIVE


def _read_active(state_root: Path) -> dict | None:
    path = _active_path(state_root)
    if not path.exists():
        return None
    if _reparse(path):
        raise ReleaseError("Activation receipt is a reparse point")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ReleaseError("Activation receipt is unreadable") from error
    if not isinstance(value, dict) or set(value) != {"schema_version", "release_root", "manifest_sha256", "previous"}:
        raise ReleaseError("Activation receipt is invalid")
    if value["schema_version"] != SCHEMA or not isinstance(value["release_root"], str):
        raise ReleaseError("Activation receipt is invalid")
    if not re.fullmatch(r"[0-9a-f]{64}", value["manifest_sha256"]):
        raise ReleaseError("Activation receipt is invalid")
    previous = value["previous"]
    if previous is not None:
        # A bounded one-release chain preserves rollback without accepting a
        # malformed or unbounded user-edited receipt tree.
        if not isinstance(previous, dict) or set(previous) != {"schema_version", "release_root", "manifest_sha256", "previous"}:
            raise ReleaseError("Activation receipt is invalid")
        if previous["schema_version"] != SCHEMA or previous["previous"] is not None:
            raise ReleaseError("Activation receipt is invalid")
        if (not isinstance(previous["release_root"], str)
                or not isinstance(previous["manifest_sha256"], str)
                or not re.fullmatch(r"[0-9a-f]{64}", previous["manifest_sha256"])):
            raise ReleaseError("Activation receipt is invalid")
    return value


def _receipt(release: Path, verified: dict, previous: dict | None) -> dict:
    retained = None if previous is None else {
        "schema_version": SCHEMA, "release_root": previous["release_root"],
        "manifest_sha256": previous["manifest_sha256"], "previous": None}
    return {"schema_version": SCHEMA, "release_root": str(release),
            "manifest_sha256": verified["manifest_sha256"], "previous": retained}


def activate_release(release, *, state_root=None, lock=None, crash_hook=None) -> dict:
    """Atomically switch only after the complete new release has verified."""
    release = _absolute(release)
    state_root = _state_root(state_root)
    _require_separate_roots(state_root, release)
    with _state_lock(state_root, lock):
        from conquest.managed_security import ensure_managed_directory
        ensure_managed_directory(state_root)
        prior = _read_active(state_root)
        verified = verify_release(release)
        receipt = _receipt(release, verified, prior)
        if crash_hook:
            crash_hook("before-activation")
        # write_json writes a sibling temporary file and replaces the receipt;
        # an interrupted activation therefore retains the prior complete receipt.
        write_json(_active_path(state_root), receipt)
        return receipt


def active_release(*, state_root=None) -> dict:
    state_root = _state_root(state_root)
    receipt = _read_active(state_root)
    if receipt is None:
        raise ReleaseError("No Conquest release is active")
    release = _absolute(receipt.get("release_root", ""))
    _require_separate_roots(state_root, release)
    verify_release(release, expected_manifest_sha256=receipt.get("manifest_sha256"))
    return receipt


def rollback_release(*, state_root=None, lock=None, crash_hook=None) -> dict:
    """Return to the retained prior release only after re-verifying it."""
    state_root = _state_root(state_root)
    with _state_lock(state_root, lock):
        current = _read_active(state_root)
        if not current or not isinstance(current.get("previous"), dict):
            raise ReleaseError("No retained prior release is available for rollback")
        prior = current["previous"]
        release = _absolute(prior.get("release_root", ""))
        _require_separate_roots(state_root, release)
        verified = verify_release(release, expected_manifest_sha256=prior.get("manifest_sha256"))
        receipt = _receipt(release, verified, current)
        if crash_hook:
            crash_hook("before-rollback")
        write_json(_active_path(state_root), receipt)
        return receipt


def launch_active_release(arguments=(), *, state_root=None, popen=subprocess.Popen):
    """Launch the active release with its root as CWD and external managed state."""
    state_root = _state_root(state_root)
    receipt = active_release(state_root=state_root)
    root = _absolute(receipt["release_root"])
    python = _venv_python(root)
    launcher = root / "scripts" / "start_desktop_app.py"
    if not python.is_file() or not launcher.is_file():
        raise ReleaseError("Active release is missing its launcher or local virtual environment")
    environment = os.environ.copy()
    environment["CONQUEST_DATA_ROOT"] = str(state_root)
    environment["CONQUEST_APP_ROOT"] = str(root)
    environment["CONQUEST_RELEASE_MANIFEST_SHA256"] = receipt["manifest_sha256"]
    # Imports must not create __pycache__ files inside the verified release.
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    # Preserve the selected state namespace through the launcher bootstrap as
    # well as its process environment.  This is important for elevated or
    # packaged launch hosts whose inherited LOCALAPPDATA differs from ours.
    return popen([str(python), "-B", str(launcher), "--data-root", str(state_root), *arguments],
                 cwd=str(root), env=environment)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Immutable Conquest release tooling")
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    build.add_argument("source", type=Path); build.add_argument("releases_root", type=Path); build.add_argument("release_id")
    activate = commands.add_parser("activate"); activate.add_argument("release", type=Path); activate.add_argument("--data-root", type=Path)
    verify = commands.add_parser("verify"); verify.add_argument("release", type=Path)
    rollback = commands.add_parser("rollback"); rollback.add_argument("--data-root", type=Path)
    launch = commands.add_parser("launch"); launch.add_argument("--data-root", type=Path); launch.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.command == "build": value = build_release(args.source, args.releases_root, args.release_id)
    elif args.command == "activate": value = activate_release(args.release, state_root=args.data_root)
    elif args.command == "verify": value = verify_release(args.release)
    elif args.command == "rollback": value = rollback_release(state_root=args.data_root)
    else:
        launch_active_release(args.arguments, state_root=args.data_root); return 0
    print(json.dumps(value, indent=2))
    return 0
