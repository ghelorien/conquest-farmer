"""Every release carries the market add-on and a machine-managed Chromium.

Failure modes this module must catch (written before the implementation):

1. The release installs only ``.``: the optional ``market`` extra (Playwright)
   is missing, so market collection fails on every other PC.
2. Chromium is not installed, or lands in the per-account default cache
   (``%LOCALAPPDATA%\\ms-playwright``), which a Codex-launched app (different
   LOCALAPPDATA) never sees.
3. Chromium is installed inside the release tree, bloating the exact manifest
   or, if written after it, making the release fail verification.
4. The browser install uses the build interpreter instead of the release venv,
   or runs before the market extra is installed.
5. An offline/failed browser download fails the whole build (an otherwise
   valid release is lost), or it is swallowed without a clear warning.
6. The browser step writes bytecode into the verified tree (no ``-B`` /
   ``PYTHONDONTWRITEBYTECODE``).
7. The launcher or worker environment does not pin PLAYWRIGHT_BROWSERS_PATH to
   the managed folder (or keeps a different inherited value), so apps started
   from different hosts look in different places.
8. The managed browser folder is computed differently by the build, the
   launcher, the worker environment and the collector.
9. A missing browser executable becomes a vague error that the market worker
   retries every 60 seconds forever, repeating failure events.
10. The setup message omits the exact folder/command, or leaks browser
    exception text.
11. After the operator installs the browser, an explicit refresh can no longer
    start (sticky failure).
12. The browser folder can be committed to git.
13. Failures of the mandatory steps (venv, pip) no longer fail the build.

The end-to-end test builds, verifies, activates and launches a release with an
injected runner (no network), for both a successful and a failed browser
download, and writes ``market-release-build.json``; it is built twice in
separate roots and must be byte-identical after path normalisation.
"""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
from types import ModuleType, SimpleNamespace

import pytest

from conquest import release

REPO = Path(__file__).resolve().parents[1]
VENV_PYTHON = "Scripts/python.exe" if os.name == "nt" else "bin/python"


def source_tree(root):
    root.mkdir(parents=True)
    (root / "pyproject.toml").write_text("[build-system]\nrequires=[]\n", "utf-8")
    (root / "AGENTS.md").write_text("rules", encoding="utf-8")
    for name in ("src", "scripts", "profiles"):
        (root / name).mkdir()
    (root / "src" / "app.py").write_text("app", encoding="utf-8")
    (root / "scripts" / "start_desktop_app.py").write_text("launch", "utf-8")
    (root / "profiles" / "route.yaml").write_text("route: checked\n", "utf-8")
    return root


class Runner:
    """Offline stand-in for venv, pip and ``playwright install``."""

    def __init__(self, *, chromium_error=None, pip_error=None):
        self.calls = []
        self.chromium_error = chromium_error
        self.pip_error = pip_error

    def __call__(self, command, **kwargs):
        self.calls.append((list(command), dict(kwargs)))
        if command[1:3] == ["-m", "venv"]:
            python = Path(command[3]) / VENV_PYTHON
            python.parent.mkdir(parents=True)
            python.write_text("fake python", encoding="utf-8")
        elif "pip" in command:
            if self.pip_error:
                raise self.pip_error
            site = Path(kwargs["cwd"]) / ".venv" / "Lib" / "site-packages"
            (site / "playwright").mkdir(parents=True)
            (site / "playwright" / "__init__.py").write_text("", encoding="utf-8")
        elif "playwright" in command:
            if self.chromium_error:
                raise self.chromium_error
            browsers = Path(kwargs["env"]["PLAYWRIGHT_BROWSERS_PATH"])
            chrome = browsers / "chromium-1000" / "chrome-win" / "chrome.exe"
            chrome.parent.mkdir(parents=True)
            chrome.write_bytes(b"fake chromium")


def build(tmp_path, runner, name="r1"):
    source = tmp_path / "source"
    if not source.exists():
        source_tree(source)
    return release.build_release(
        source,
        tmp_path / "releases",
        name,
        runner=runner,
        python="build-python",
        state_root=tmp_path / "state",
    )


def test_build_installs_market_extra_then_managed_chromium(tmp_path):
    runner = Runner()
    result = build(tmp_path, runner)
    root = Path(result["root"])
    state = tmp_path / "state"
    browsers = state / "ms-playwright"
    (venv, _), (pip, pip_options), (chromium, options) = runner.calls
    stage = Path(pip_options["cwd"])
    assert venv[:3] == ["build-python", "-m", "venv"]
    assert pip == [str(stage / ".venv" / VENV_PYTHON), "-m", "pip", "install"] + [
        "--no-input",
        ".[market]",
    ]
    assert chromium == [
        str(stage / ".venv" / VENV_PYTHON),
        "-B",
        "-m",
        "playwright",
        "install",
        "chromium",
    ]
    assert options["cwd"] == str(stage)
    assert options["env"]["PLAYWRIGHT_BROWSERS_PATH"] == str(browsers)
    assert options["env"]["PYTHONDONTWRITEBYTECODE"] == "1"
    assert result["browsers_path"] == str(browsers) and result["warnings"] == []
    assert (browsers / "chromium-1000" / "chrome-win" / "chrome.exe").is_file()
    manifest = json.loads((root / release.MANIFEST).read_text(encoding="utf-8"))
    assert ".venv/Lib/site-packages/playwright/__init__.py" in manifest["files"]
    assert not any("chrom" in name for name in manifest["files"])
    assert release.verify_release(root)["file_count"] == len(manifest["files"])


@pytest.mark.parametrize(
    "error",
    [
        subprocess.CalledProcessError(1, ["playwright"]),
        OSError("network unavailable"),
    ],
)
def test_browser_download_failure_is_a_warning_not_a_failed_build(tmp_path, error):
    result = build(tmp_path, Runner(chromium_error=error))
    root = Path(result["root"])
    browsers = tmp_path / "state" / "ms-playwright"
    (warning,) = result["warnings"]
    assert warning.startswith("WARNING: Playwright Chromium was not installed")
    assert str(browsers) in warning
    assert f"& '{root / '.venv' / VENV_PYTHON}' -m playwright install chromium" in (
        warning
    )
    assert "network unavailable" not in warning
    assert release.verify_release(root)["file_count"] > 0


@pytest.mark.parametrize("step", ["venv", "pip"])
def test_mandatory_steps_still_fail_the_build(tmp_path, step):
    failure = subprocess.CalledProcessError(1, [step])

    class Failing(Runner):
        def __call__(self, command, **kwargs):
            if step in command:
                raise failure
            return super().__call__(command, **kwargs)

    with pytest.raises(subprocess.CalledProcessError):
        build(tmp_path, Failing())
    assert not (tmp_path / "releases" / "r1").exists()
    assert not list((tmp_path / "releases").glob(".r1.staging-*"))


def test_managed_state_containing_the_releases_is_rejected(tmp_path):
    source = source_tree(tmp_path / "source")
    with pytest.raises(release.ReleaseError, match="separate"):
        release.build_release(
            source,
            tmp_path / "state" / "releases",
            "r1",
            runner=Runner(),
            python="build-python",
            state_root=tmp_path / "state",
        )


def test_launcher_and_worker_environment_pin_the_managed_browsers(
    tmp_path, monkeypatch
):
    result = build(tmp_path, Runner())
    root = Path(result["root"])
    state = tmp_path / "state"
    release.activate_release(root, state_root=state, lock=lambda _: _Unlocked())
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path / "elsewhere"))
    calls = []
    release.launch_active_release(
        (), state_root=state, popen=lambda *a, **k: calls.append(k)
    )
    managed = str(state.resolve() / "ms-playwright")
    assert calls[0]["env"]["PLAYWRIGHT_BROWSERS_PATH"] == managed
    from conquest.application_layout import RuntimeLayout

    layout = RuntimeLayout(root, True, state.resolve())
    assert layout.environment()["PLAYWRIGHT_BROWSERS_PATH"] == managed
    assert release.playwright_browsers(state) == Path(managed)


class _Unlocked:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_git_ignores_browser_folders():
    lines = (REPO / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "ms-playwright/" in lines


def fake_playwright(monkeypatch, *, launch_error=None, missing=False):
    if missing:
        monkeypatch.setitem(sys.modules, "playwright.sync_api", None)
        return
    package = ModuleType("playwright")
    api = ModuleType("playwright.sync_api")

    class Error(Exception):
        pass

    class Chromium:
        def launch(self, **options):
            raise Error(launch_error)

    @contextmanager
    def sync_playwright():
        yield SimpleNamespace(chromium=Chromium())

    api.Error, api.sync_playwright = Error, sync_playwright
    package.sync_api = api
    monkeypatch.setitem(sys.modules, "playwright", package)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", api)


def collect(tmp_path):
    from conquest.merchants.collector import collect_market

    definitions = tmp_path / "itemtype.json"
    definitions.write_text("{}", encoding="utf-8")
    return collect_market(
        destination=str(tmp_path / "market.json"), definitions_path=definitions
    )


def test_missing_chromium_names_the_managed_folder_and_exact_command(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("CONQUEST_DATA_ROOT", str(tmp_path / "state"))
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    secret = r"BrowserType.launch: Executable doesn't exist at C:\secret\chrome.exe"
    fake_playwright(monkeypatch, launch_error=secret)
    from conquest.merchants.collector import MarketSetupRequired

    with pytest.raises(MarketSetupRequired) as caught:
        collect(tmp_path)
    managed = (tmp_path / "state").resolve() / "ms-playwright"
    message = str(caught.value)
    assert os.environ["PLAYWRIGHT_BROWSERS_PATH"] == str(managed)
    assert str(managed) in message
    assert f"$env:PLAYWRIGHT_BROWSERS_PATH='{managed}'" in message
    assert "-m playwright install chromium" in message
    assert "secret" not in message and "doesn't exist" not in message
    assert isinstance(caught.value, ValueError)


def test_missing_market_extra_is_a_setup_error(tmp_path, monkeypatch):
    monkeypatch.setenv("CONQUEST_DATA_ROOT", str(tmp_path / "state"))
    fake_playwright(monkeypatch, missing=True)
    from conquest.merchants.collector import MarketSetupRequired

    with pytest.raises(MarketSetupRequired, match=re.escape(".[market]")):
        collect(tmp_path)


def test_other_launch_failures_stay_retryable(tmp_path, monkeypatch):
    monkeypatch.setenv("CONQUEST_DATA_ROOT", str(tmp_path / "state"))
    fake_playwright(monkeypatch, launch_error="BrowserType.launch: crashed")
    from conquest.merchants.collector import MarketSetupRequired

    with pytest.raises(ValueError, match="could not start") as caught:
        collect(tmp_path)
    assert not isinstance(caught.value, MarketSetupRequired)


def test_setup_failure_reports_once_and_explicit_refresh_restarts(tmp_path):
    from conquest.merchants.collector import MarketSetupRequired
    from conquest.merchants.dashboard import merchant_text
    from conquest.merchants.journal import Journal
    from conquest.merchants.market import SOURCE
    from conquest.merchants.market_refresh import MarketRefreshWorker

    journal = Journal(tmp_path / "journal.sqlite3")
    now = [100.0]
    calls = []
    message = "Market browser setup required: install it once with: X"
    installed = [False]

    def collect_stub(**kwargs):
        calls.append(1)
        if not installed[0]:
            raise MarketSetupRequired(message)
        data = {
            "source": SOURCE,
            "server": "America",
            "complete": True,
            "observed_at": now[0],
            "total": 1,
            "listings": [
                {
                    "name": "Coat",
                    "category": "Trojan Armor",
                    "quality": "Normal",
                    "plus": 1,
                    "sockets": ["No socket", "No socket"],
                    "price": 100,
                    "seller": "Outside",
                    "server": "America",
                }
            ],
        }
        Path(kwargs["destination"]).write_text(json.dumps(data), encoding="utf-8")
        return data

    worker = MarketRefreshWorker(
        journal,
        threading.Event(),
        tmp_path / "market.json",
        collect=collect_stub,
        clock=lambda: now[0],
    )
    journal.request_scan("Dutch", "scan-1", now=1)
    worker.request("Dutch", "first")
    worker.step()
    state = worker.state("Dutch")
    assert state["phase"] == "needs_setup" and state["pending"] is False
    assert state["error"] == message and "Retrying" not in state["error"]
    for _ in range(5):
        now[0] += 600
        worker.step()
    assert len(calls) == 1
    with journal.db() as db:
        events = [
            row[0]
            for row in db.execute("SELECT event FROM events ORDER BY id").fetchall()
        ]
    assert events.count("market_browser_setup_required") == 1
    assert "market_refresh_failed" not in events
    text = merchant_text(
        {"market_refresh": worker.state("Dutch"), "scan": journal.get("Dutch", "scan")},
        now=now[0],
    )
    assert message in text
    installed[0] = True
    worker.request("Dutch", "after-install")
    worker.step()
    assert len(calls) == 2 and worker.state("Dutch")["phase"] == "ready"


def _normalise(value, replacements):
    text = json.dumps(value, sort_keys=True)
    for old, new in replacements:
        text = text.replace(json.dumps(str(old))[1:-1], new)
    return json.loads(re.sub(r"\.r1\.staging-[0-9a-f]{32}", "<stage>", text))


def _release_scenario(root):
    artifact = {}
    for case, runner in (
        ("chromium_installed", Runner()),
        ("chromium_download_failed", Runner(chromium_error=OSError("offline"))),
    ):
        base = root / case
        result = build(base, runner)
        release_root = Path(result["root"])
        state = base / "state"
        release.activate_release(
            release_root, state_root=state, lock=lambda _: _Unlocked()
        )
        launches = []
        release.launch_active_release(
            (), state_root=state, popen=lambda *a, **k: launches.append(k)
        )
        manifest = json.loads(
            (release_root / release.MANIFEST).read_text(encoding="utf-8")
        )
        replacements = [
            (state.resolve(), "<state>"),
            (state, "<state>"),
            (base / "releases", "<releases>"),
        ]
        artifact[case] = _normalise(
            {
                "commands": [command for command, _ in runner.calls],
                "browser_step_env": {
                    key: runner.calls[-1][1].get("env", {}).get(key)
                    for key in ("PLAYWRIGHT_BROWSERS_PATH", "PYTHONDONTWRITEBYTECODE")
                },
                "browsers_path": result["browsers_path"],
                "warnings": result["warnings"],
                "manifest_has_market_extra": (
                    ".venv/Lib/site-packages/playwright/__init__.py"
                    in manifest["files"]
                ),
                "manifest_browser_entries": sorted(
                    name for name in manifest["files"] if "chrom" in name
                ),
                "chromium_on_disk": (
                    state
                    / "ms-playwright"
                    / "chromium-1000"
                    / "chrome-win"
                    / "chrome.exe"
                ).is_file(),
                "verified": release.verify_release(release_root)["file_count"]
                == len(manifest["files"]),
                "launcher_browsers_path": launches[0]["env"][
                    "PLAYWRIGHT_BROWSERS_PATH"
                ],
            },
            replacements,
        )
    path = root / "market-release-build.json"
    path.write_text(json.dumps(artifact, indent=2, sort_keys=True), encoding="utf-8")
    return path


def test_market_release_build_e2e(tmp_path):
    first = _release_scenario(tmp_path / "pc-a")
    second = _release_scenario(tmp_path / "pc-b")
    assert first.read_bytes() == second.read_bytes()
    artifact = json.loads(first.read_text(encoding="utf-8"))
    venv = os.path.join("<releases>", "<stage>", ".venv")
    python = os.path.join(venv, *VENV_PYTHON.split("/"))
    commands = [
        ["build-python", "-m", "venv", venv],
        [python, "-m", "pip", "install", "--no-input", ".[market]"],
        [python, "-B", "-m", "playwright", "install", "chromium"],
    ]
    managed = "<state>" + os.sep + "ms-playwright"
    common = {
        "commands": commands,
        "browser_step_env": {
            "PLAYWRIGHT_BROWSERS_PATH": managed,
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        "browsers_path": managed,
        "manifest_has_market_extra": True,
        "manifest_browser_entries": [],
        "verified": True,
        "launcher_browsers_path": managed,
    }
    assert artifact["chromium_installed"] == {
        **common,
        "warnings": [],
        "chromium_on_disk": True,
    }
    failed = artifact["chromium_download_failed"]
    assert {k: v for k, v in failed.items() if k != "warnings"} == {
        **common,
        "chromium_on_disk": False,
    }
    (warning,) = failed["warnings"]
    assert managed in warning and "-m playwright install chromium" in warning
