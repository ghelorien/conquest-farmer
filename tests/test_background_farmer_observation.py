from types import SimpleNamespace
import threading

import pytest

from conquest import background_farmer_observation as module
from conquest.memory_build_layout import CLIENT_SHA256_1078


@pytest.fixture
def ui():
    identity = {"pid": 12, "creation_time_100ns": 34}
    control = {"enabled": True, "revision": 5, "input_mode": "foreground"}
    session = SimpleNamespace(
        expected_sha256=CLIENT_SHA256_1078,
        identity=identity,
        assert_identity=lambda: None,
        read_block=lambda address, size: pytest.fail("farmer memory was read"),
    )
    observer = SimpleNamespace(
        character="Parasite",
        lock=threading.Lock(),
        adapter=session,
        health_layout=object(),
        operations=SimpleNamespace(
            target=SimpleNamespace(
                hwnd=56,
                snapshot=lambda: {"pid": 12, "hwnd": 56, "client_size": [1000, 700]},
            )
        ),
    )
    app = SimpleNamespace(
        observer=observer,
        client=(12, 56, identity),
        control=SimpleNamespace(snapshot=lambda: dict(control)),
    )
    return SimpleNamespace(app=app)


def test_absent_and_wrong_character_are_clear_unavailable(ui):
    ui.app.observer.character = "Dutch"
    assert "not Parasite" in module.snapshot(ui)["reason"]
    ui.app.observer = None
    assert "no attached" in module.snapshot(ui)["reason"]


def test_attached_farmer_fails_closed_without_reading_memory(ui):
    observer = ui.app.observer
    result = module.snapshot(ui)
    assert result["available"] is False and not result["input_qualified"]
    assert result["reason"] == "Farmer observation could not be verified"
    assert result["error_type"] == "ValueError"
    assert ui.app.observer is observer
    # The observer lock is released for the farmer's own readers.
    assert observer.lock.acquire(blocking=False)
    observer.lock.release()
