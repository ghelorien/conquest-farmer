import pytest
import time
from types import SimpleNamespace

from conquest.background_keyboard import (
    BackgroundControlScope,
    _run_scope,
    validate_spec,
)


SPEC = {
    "hwnd": 100,
    "identity": {
        "pid": 10,
        "creation_time_100ns": 42,
        "path": "dummy.exe",
        "architecture": "x64",
    },
    "hold_seconds": 0.05,
}


class Api:
    def __init__(self):
        self.state = bytearray(256)
        self.state[0x14] = 1  # Preserve toggle bit and every other table byte.
        self.initial = bytes(self.state)
        self.attached = False
        self.changed = False
        self.calls = []
        self.raise_restore = False
        self.inspection = {
            "thread": 20,
            "foreground": 300,
            "foreground_thread": 30,
            "active": 100,
            "focus": 100,
        }

    def inspect(self, spec):
        if self.changed:
            raise ValueError("identity or foreground changed")
        return dict(self.inspection)

    def thread_id(self):
        return 40

    def attach(self, a, b, value):
        self.attached = value
        self.calls.append(("attach", value))

    def keyboard(self):
        return bytes(self.state)

    def set_keyboard(self, value):
        if self.raise_restore and not value[0x11] & 0x80:
            raise OSError("restore failed")
        self.state[:] = value
        self.calls.append(("state", bytes(value)))


def test_scope_only_sets_control_and_restores_every_byte():
    api = Api()
    events = []
    _run_scope(api, SPEC, lambda: bool(events), events.append)
    held = api.calls[1][1]
    expected = bytearray(api.initial)
    expected[0x11] = expected[0xA2] = 128
    assert held == expected
    assert bytes(api.state) == api.initial
    assert api.attached is False
    assert [event["state"] for event in events] == ["ready", "released"]


def test_deadline_releases_even_if_caller_does_not_stop():
    api = Api()
    ticks = iter((0, 0.1))
    with pytest.raises(TimeoutError):
        _run_scope(api, SPEC, lambda: False, lambda e: None, clock=lambda: next(ticks))
    assert not api.attached and bytes(api.state) == api.initial


def test_manual_takeover_after_ready_releases_before_propagating():
    api = Api()
    with pytest.raises(ValueError):
        _run_scope(api, SPEC, lambda: False, lambda e: setattr(api, "changed", True))
    assert not api.attached and bytes(api.state) == api.initial


def test_restore_error_still_detaches():
    api = Api()
    with pytest.raises(OSError):
        _run_scope(
            api, SPEC, lambda: True, lambda e: setattr(api, "raise_restore", True)
        )
    assert not api.attached


def test_never_attaches_foreground_queue():
    api = Api()
    api.inspection["foreground_thread"] = 20
    with pytest.raises(ValueError):
        _run_scope(api, SPEC, lambda: True, lambda e: None)
    assert api.calls == []


def test_neutralized_ack_is_sent_only_after_restoration_while_attached():
    api = Api()
    events = []

    def emit(event):
        if event["state"] == "neutralized":
            assert api.attached
            assert bytes(api.state) == api.initial
        events.append(event["state"])

    _run_scope(
        api,
        SPEC,
        lambda: "neutralized" in events,
        emit,
        neutralize_requested=lambda: True,
        sleep=lambda seconds: None,
    )
    assert events == ["ready", "neutralized", "released"]
    assert not api.attached and bytes(api.state) == api.initial


def test_failed_neutralization_has_no_ack_and_still_detaches():
    api = Api()
    events = []

    def emit(event):
        events.append(event["state"])
        if event["state"] == "ready":
            api.raise_restore = True

    with pytest.raises(OSError):
        _run_scope(api, SPEC, lambda: False, emit, neutralize_requested=lambda: True)
    assert events == ["ready"]
    assert not api.attached


def test_public_neutralize_waits_for_ack_without_restarting_deadline():
    scope = BackgroundControlScope(SPEC["hwnd"], SPEC["identity"], hold_seconds=2)
    scope.ready = time.monotonic()
    original_ready = scope.ready
    commands = []

    def write(command):
        commands.append(command)
        scope.events.put({"state": "neutralized"})

    scope.process = SimpleNamespace(
        stdin=SimpleNamespace(write=write, flush=lambda: None), poll=lambda: None
    )
    scope.neutralize()
    scope.neutralize()
    assert commands == ["neutralize\n"]
    assert scope.neutralized and scope.ready == original_ready


def test_terminal_failure_cannot_be_cleared_by_catching_exception():
    scope = BackgroundControlScope(SPEC["hwnd"], SPEC["identity"])
    scope.terminal = {"state": "failed", "error": "guard failed"}
    for _ in range(2):
        with pytest.raises(RuntimeError, match="guard failed"):
            scope.check()


@pytest.mark.parametrize("value", [0, 0.01, 3, True, float("nan")])
def test_unbounded_or_invalid_duration_rejected(value):
    with pytest.raises(ValueError):
        validate_spec({**SPEC, "hold_seconds": value})
