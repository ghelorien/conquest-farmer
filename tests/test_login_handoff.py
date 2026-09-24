from contextlib import nullcontext
from types import SimpleNamespace as NS
import threading
import pytest
from conquest.capture import CaptureUnavailable
from conquest.merchants import coordination
from conquest import reconnect


@pytest.mark.parametrize("blocked", [False, True])
def test_login_holds_one_input_owner_through_all_fields(tmp_path, monkeypatch, blocked):
    guard = coordination.InputCoordinator(lambda: True, path=tmp_path / "input.lock")
    monkeypatch.setattr(coordination, "_coordinator", guard)
    guard.stopped = blocked
    calls = []
    window = object()
    monkeypatch.setattr(reconnect, "login_screen", lambda hwnd: True)

    def exclusive(label):
        assert guard.owner == "Farmer"
        result = []

        def contender():
            try:
                with guard.lease("Dutch"):
                    result.append("acquired")
            except CaptureUnavailable:
                result.append("denied")

        thread = threading.Thread(target=contender)
        thread.start()
        thread.join(1)
        assert result == ["denied"]
        calls.append(label)

    monkeypatch.setattr(
        "conquest.focus_recovery.activate_client", lambda *a: exclusive("focus") or True
    )
    monkeypatch.setattr(
        reconnect, "dismiss_login_error", lambda *a: exclusive("dialog")
    )
    monkeypatch.setattr("conquest.viewport.size_for", lambda s: (1400, 900))
    monkeypatch.setattr(
        "conquest.memory_shop.MemoryGui", lambda s: NS(read=lambda name: window)
    )
    monkeypatch.setattr(
        reconnect, "login_form_points", lambda *a: ((100, 100), (100, 140), (100, 180))
    )
    monkeypatch.setattr(
        reconnect,
        "load_credentials",
        lambda p: {"username": "test", "password": "test"},
    )
    monkeypatch.setattr("conquest.desktop_runtime.physical_coordinates", nullcontext)
    monkeypatch.setattr(
        "conquest.foreground.foreground_click", lambda *a: exclusive("click")
    )
    monkeypatch.setattr(reconnect, "type_login_field", lambda *a: exclusive("field"))
    target = NS(hwnd=1, snapshot=lambda: {"client_size": (1400, 900)})
    session = NS(identity={"pid": 1}, assert_identity=lambda: None)
    if blocked:
        with pytest.raises(CaptureUnavailable):
            reconnect.submit_login(target, session=session)
        assert not calls
    else:
        assert reconnect.submit_login(target, session=session) == {"submitted": True}
        assert calls == ["focus", "dialog", "click", "field", "click", "field", "click"]
    assert guard.owner is None
