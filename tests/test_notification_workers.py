from contextlib import contextmanager
import copy
import json
from pathlib import Path
import pytest

from conquest import notification_workers as workers


def born(at):
    return int((11644473600 + at) * 10000000)


class Processes:
    def __init__(self, rows):
        self.rows = rows
        self.stopped = []
        self.started = []
        self.access = []

    @contextmanager
    def pinned(self, pid, *, terminate=True):
        self.access.append((pid, terminate))
        identity = copy.deepcopy(self.rows[pid])

        def stop(proof):
            assert terminate
            if any(self.rows[pid][key] != proof[key] for key in proof):
                raise ValueError("PID reused")
            self.stopped.append(pid)

        yield identity, stop

    def start(self, python, script, root):
        self.started.append((python, script, root))
        return 300


@pytest.fixture
def setup(tmp_path, monkeypatch):
    root = tmp_path / "release"
    (root / "scripts").mkdir(parents=True)
    for name in ("src/conquest", "profiles/routes"):
        (root / name).mkdir(parents=True)
    (root / "pyproject.toml").touch()
    (root / "scripts/run_discord_notifications.py").touch()
    (root / "scripts/run_shop_notifications.py").touch()
    data = tmp_path / "data"
    (data / ".runtime").mkdir(parents=True)
    (data / "reports/desktop-farming").mkdir(parents=True)
    python = tmp_path / "pythonw.exe"
    python.touch()
    monkeypatch.setattr(workers, "state_path", lambda name: str(data / name))
    (data / ".runtime/discord.paused").touch()
    (data / ".runtime/discord-notifications.json").write_text(
        '{"queue":[{"id":"keep"}],"merchant_cursor":17}'
    )
    (data / "reports/discord-status.json").write_text(
        json.dumps({"pid": 20, "updated_at": 100, "state": "Paused"})
    )
    (data / "reports/desktop-farming/app-state.json").write_text(
        json.dumps({"pid": 10, "updated_at": 100})
    )

    def process(pid, parent, script):
        return {
            "pid": pid,
            "parent_pid": parent,
            "creation_time_100ns": born(90),
            "path": str(python),
            "argv": [str(python), str(root / "scripts" / script)],
        }

    rows = {
        10: process(10, 1, "start_desktop_app.py"),
        20: process(20, 10, "run_discord_notifications.py"),
    }
    return root, data, python, Processes(rows)


def test_restarts_only_owned_paused_worker_and_preserves_queue(setup):
    root, data, python, processes = setup
    queue = data / ".runtime/discord-notifications.json"
    saved = queue.read_bytes()
    result = workers.restart(
        "farmer",
        root=root,
        python=python,
        processes=processes,
        owner_pid=10,
        clock=lambda: 100,
    )
    assert result["replacement_pid"] == 300 and processes.stopped == [20]
    assert processes.started == [
        (python, root / "scripts/run_discord_notifications.py", root)
    ]
    assert queue.read_bytes() == saved and (data / ".runtime/discord.paused").exists()


@pytest.mark.parametrize(
    "change",
    [
        {"parent_pid": 999},
        {"creation_time_100ns": born(101)},
        {"argv": ["python.exe", "game.py"]},
        {"path": "ImConquer.exe"},
    ],
)
def test_unrelated_process_or_reused_pid_is_never_stopped(setup, change):
    root, data, python, processes = setup
    processes.rows[20].update(change)
    with pytest.raises((ValueError, KeyError)):
        workers.restart(
            "farmer",
            root=root,
            python=python,
            processes=processes,
            owner_pid=10,
            clock=lambda: 100,
        )
    assert processes.stopped == [] and processes.started == []


def test_prepared_handover_survives_owner_exit_consumes_once(setup):
    root, data, python, processes = setup
    receipt = workers.prepare_handover(
        root, python=python, processes=processes, clock=lambda: 100
    )
    assert receipt["verification"] == "full_process_identity_and_owned_script"
    assert not any(terminate for _, terminate in processes.access)
    del processes.rows[10]
    result = workers.restart(
        "farmer",
        root=root,
        python=python,
        processes=processes,
        owner_pid=99,
        clock=lambda: 101,
    )
    assert result["replacement_pid"] == 300 and processes.stopped == [20]
    with pytest.raises(ValueError, match="already consumed"):
        workers.restart(
            "farmer",
            root=root,
            python=python,
            processes=processes,
            owner_pid=99,
            clock=lambda: 102,
        )
    assert processes.stopped == [20]


def test_hidden_command_line_cannot_mint_authoritative_receipt(setup):
    root, data, python, processes = setup
    for row in processes.rows.values():
        row["argv"] = []
    with pytest.raises(ValueError, match="expected native desktop app"):
        workers.prepare_handover(
            root, python=python, processes=processes, clock=lambda: 100
        )
    assert processes.stopped == []


def test_delegated_owner_uses_fixed_validated_legacy_launcher(setup, monkeypatch):
    root, data, python, processes = setup
    monkeypatch.setenv("CONQUEST_LEGACY_DATA_ROOT", str(data))
    processes.rows[10]["argv"][1] = str(data / "scripts/start_desktop_app.py")
    assert workers.prepare_handover(
        root, python=python, processes=processes, clock=lambda: 100
    )["workers"]["farmer"]


def test_venv_wrapper_allows_same_script_with_different_approved_python(
    setup, monkeypatch
):
    root, data, python, processes = setup
    base = python.parent / "base/pythonw.exe"
    base.parent.mkdir()
    base.touch()
    monkeypatch.setattr(
        workers,
        "python_paths",
        lambda executable: {str(python).casefold(), str(base).casefold()},
    )
    processes.rows[21] = copy.deepcopy(processes.rows[20])
    processes.rows[21].update(pid=21, parent_pid=10)
    processes.rows[20].update(
        parent_pid=21, path=str(base), argv=[str(base), processes.rows[20]["argv"][1]]
    )
    workers.prepare_handover(
        root, python=python, processes=processes, clock=lambda: 100
    )
    workers.restart(
        "farmer",
        root=root,
        python=python,
        processes=processes,
        owner_pid=99,
        clock=lambda: 101,
    )
    assert processes.stopped == [20]


def test_stale_pause_and_changed_prepared_identity_rejected(setup):
    root, data, python, processes = setup
    workers.prepare_handover(
        root, python=python, processes=processes, clock=lambda: 100
    )
    processes.rows[20]["creation_time_100ns"] = born(99)
    with pytest.raises(ValueError, match="changed"):
        workers.restart(
            "farmer",
            root=root,
            python=python,
            processes=processes,
            owner_pid=99,
            clock=lambda: 101,
        )
    assert not processes.stopped


def test_native_endpoint_has_no_pid_path_or_command_arguments(monkeypatch):
    from conquest.merchants.ui import UnifiedUI

    calls = []
    monkeypatch.setattr(
        workers, "restart", lambda worker: calls.append(worker) or {"paused": True}
    )
    ui = UnifiedUI.__new__(UnifiedUI)
    assert ui.dispatch(
        {"action": "notification-workers-restart", "worker": "farmer"}
    ) == {"paused": True}
    with pytest.raises(ValueError):
        ui.dispatch(
            {"action": "notification-workers-restart", "worker": "farmer", "pid": 20}
        )
    assert calls == ["farmer"]
