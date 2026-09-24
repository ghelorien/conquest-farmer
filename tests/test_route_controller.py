from pathlib import Path
from types import SimpleNamespace
from conquest import route_controller as r


def test_alive_route_is_not_duplicated_and_stopped_route_can_restart(
    monkeypatch, tmp_path
):
    from conquest import routes

    for name in ("src/conquest", "profiles/routes", "scripts"):
        (tmp_path / name).mkdir(parents=True)
    (tmp_path / "pyproject.toml").touch()
    (tmp_path / "scripts/run_overnight.py").touch()
    monkeypatch.setattr(r.time, "time", lambda: 100)
    state = {"phase": "hunting", "updated_at": 99, "pid": 1}
    monkeypatch.setattr(
        r, "read_json", lambda path: state if Path(path).name == "status.json" else {}
    )
    monkeypatch.setattr(r, "process_alive", lambda pid: True)
    calls = []
    monkeypatch.setattr(
        r.subprocess,
        "Popen",
        lambda *a, **kw: calls.append((a, kw)) or SimpleNamespace(pid=2),
    )
    monkeypatch.setattr(
        routes, "RouteLibrary", lambda root: SimpleNamespace(load=lambda route: None)
    )
    assert not r.ensure_running("wingedsnake", root=tmp_path)
    assert not calls
    state["phase"] = "stopped"
    assert r.ensure_running("wingedsnake", root=tmp_path)
    assert len(calls) == 1 and "--route" in calls[0][0][0]


def test_controller_process_lock_blocks_duplicates_and_releases(tmp_path):
    with r.controller_guard(tmp_path / "lock") as first:
        assert first
        with r.controller_guard(tmp_path / "lock") as duplicate:
            assert not duplicate
    with r.controller_guard(tmp_path / "lock") as later:
        assert later
