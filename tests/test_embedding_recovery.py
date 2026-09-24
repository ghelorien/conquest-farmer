from queue import Queue
from threading import RLock
from types import SimpleNamespace as NS

import pytest

from conquest.control import FarmingControl
from conquest.desktop_app import DesktopApp


def app_for(tmp_path, enabled=False):
    app = DesktopApp.__new__(DesktopApp)
    app.control = FarmingControl(tmp_path / "control.json")
    app.control.update({"enabled": enabled})
    app.messages = Queue()
    app.thread = None
    app.client = (42, 99, {"pid": 42, "creation_time_100ns": 123})
    app.records = []
    app.record = lambda **fields: app.records.append(fields)
    app.state_text = NS(set=lambda value: None)
    app.pane = NS(
        winfo_id=lambda: 10, winfo_width=lambda: 1420, winfo_height=lambda: 1009
    )
    app.calls = []

    def attach(*args):
        assert app.observer.lock._is_owned()
        app.calls.append(args)
        app.host.saved = object()

    def detach():
        app.calls.append("detach")
        app.host.saved = None

    app.host = NS(saved=None, mode="owned", attach=attach, detach=detach)
    app.observer = NS(lock=RLock(), bridge=NS(sync_window_mode=lambda host: None))
    app.runtime = object()
    return app


@pytest.mark.parametrize("enabled", [False, True])
def test_reattach_reuses_connection_and_current_intent(tmp_path, enabled):
    app = app_for(tmp_path, enabled)
    observer, runtime = app.observer, app.runtime
    revision = app.control.snapshot()["revision"]
    assert app.embed()
    assert app.observer is observer and app.runtime is runtime
    assert app.calls == [(99, app.client[2], 10, 1420, 1009)]
    assert app.control.snapshot()["enabled"] is enabled
    assert app.control.snapshot()["revision"] == revision
    assert app.messages.qsize() == int(enabled)
    assert app.records[-1]["window_mode"] == "owned"
    assert app.embed()  # Repeated Embed must not tear down the hosted client.
    assert len(app.calls) == 1


def test_manual_stop_wins_during_reattach(tmp_path):
    app = app_for(tmp_path, True)
    attach = app.host.attach

    def stop_then_attach(*args):
        app.control.update({"enabled": False})
        attach(*args)

    app.host.attach = stop_then_attach
    assert app.change_native_window(False)
    assert not app.control.snapshot()["enabled"] and app.messages.empty()


@pytest.mark.parametrize("enabled,running", [(True, False), (False, True)])
def test_detach_rejected_until_intent_and_runner_are_stopped(
    tmp_path, enabled, running
):
    app = app_for(tmp_path, enabled)
    app.thread = NS(is_alive=lambda: running)
    app.host.saved = object()
    assert not app.change_native_window(True)
    assert app.host.saved and not app.calls


def test_failed_reattach_keeps_connection_for_retry(tmp_path):
    app = app_for(tmp_path, True)
    observer = app.observer
    attach = app.host.attach
    app.host.attach = lambda *args: (_ for _ in ()).throw(ValueError("DPI changed"))
    assert not app.embed()
    assert app.observer is observer and app.messages.empty()
    assert app.records[-1]["embed_error"] == "DPI changed"
    app.host.attach = attach
    assert app.embed()


def test_reload_restores_host_before_safe_handoff(tmp_path):
    app = app_for(tmp_path, True)
    app.last = {}
    app.selected_route = None
    app._restart_now = lambda: pytest.fail(
        "Detached reload must not bypass safe handoff"
    )
    assert app.restart() is False  # Defers until memory/route are ready.
    assert app.host.saved and app.messages.empty()
