from types import SimpleNamespace
import pytest
from conquest.focus_recovery import (
    AutoRefocuser,
    activate_client,
    activate_focused_client,
)


def test_refocus_retries_with_backoff_and_off_cancels():
    now = [0.0]
    calls = []
    r = AutoRefocuser(clock=lambda: now[0])

    def activate():
        calls.append(now[0])
        return False

    assert r.step(True, False, activate) is None
    now[0] = 0.5
    assert r.step(True, False, activate) is False
    now[0] = 0.9
    r.step(True, False, activate)
    now[0] = 1.5
    r.step(True, False, activate)
    assert calls == [0.5, 1.5]
    now[0] = 2.5
    r.step(False, False, activate)
    now[0] = 3
    r.step(True, False, activate)
    assert calls == [0.5, 1.5]
    now[0] = 3.5
    assert r.step(True, False, lambda: True) is True
    now[0] = 4
    r.step(True, True, activate)
    assert calls == [0.5, 1.5]


@pytest.mark.parametrize("fail", [False, True])
def test_foreground_input_queues_always_detach(monkeypatch, fail):
    import win32api, win32process

    calls = []
    foreground = [20]
    monkeypatch.setattr(win32api, "GetCurrentThreadId", lambda: 1)
    monkeypatch.setattr(win32process, "GetWindowThreadProcessId", lambda hwnd: (2, 3))
    monkeypatch.setattr(
        win32process, "AttachThreadInput", lambda a, b, on: calls.append(on)
    )

    def activate(hwnd):
        import pywintypes

        if not calls:
            raise pywintypes.error(0, "SetForegroundWindow", "Retry with queue")
        if fail:
            raise OSError("Focus denied")
        foreground[0] = hwnd

    api = SimpleNamespace(
        assert_owner=lambda hwnd, identity: None,
        gui=SimpleNamespace(
            GetAncestor=lambda hwnd, flag: 10,
            IsIconic=lambda hwnd: False,
            GetForegroundWindow=lambda: foreground[0],
            SetForegroundWindow=activate,
        ),
    )
    if fail:
        with pytest.raises(OSError):
            activate_client(10, {}, api=api)
    else:
        assert activate_client(10, {}, api=api)
    assert calls == [True, False]


def test_changed_client_is_rejected_before_focus_input():
    def reject(*args):
        raise ValueError("Client identity changed")

    with pytest.raises(ValueError, match="identity"):
        activate_client(10, {}, api=SimpleNamespace(assert_owner=reject, gui=None))


def test_caption_fallback_rejects_other_apps(monkeypatch):
    import win32process, os
    from conquest.caption_focus import activate_owner_caption

    monkeypatch.setattr(
        win32process, "GetWindowThreadProcessId", lambda hwnd: (1, os.getpid() + 1)
    )
    api = SimpleNamespace(
        assert_owner=lambda *args: None, gui=SimpleNamespace(GetWindow=lambda *args: 20)
    )
    assert activate_owner_caption(10, {}, api) is False


def test_caption_fallback_manual_stop_precedes_window_changes(monkeypatch):
    import win32process, os
    from conquest.caption_focus import activate_owner_caption
    from conquest.capture import CaptureUnavailable

    monkeypatch.setattr(
        win32process, "GetWindowThreadProcessId", lambda hwnd: (1, os.getpid())
    )

    def stopped():
        raise CaptureUnavailable("Manual Stop")

    monkeypatch.setattr("conquest.caption_focus.check_input", stopped)
    api = SimpleNamespace(
        assert_owner=lambda *args: None, gui=SimpleNamespace(GetWindow=lambda *args: 20)
    )
    with pytest.raises(CaptureUnavailable, match="Manual Stop"):
        activate_owner_caption(10, {}, api)


def test_owned_caption_fallback_runs_only_after_detaching_and_verifies_focus(
    monkeypatch,
):
    import win32api, win32process, pywintypes

    calls = []
    foreground = [20]
    monkeypatch.setattr(win32api, "GetCurrentThreadId", lambda: 1)
    monkeypatch.setattr(win32process, "GetWindowThreadProcessId", lambda hwnd: (2, 3))
    monkeypatch.setattr(
        win32process, "AttachThreadInput", lambda a, b, on: calls.append(on)
    )

    def activate(hwnd):
        if foreground[0] == 20:
            raise pywintypes.error(0, "SetForegroundWindow", "Denied")
        foreground[0] = hwnd

    def caption(hwnd, identity):
        assert calls == [True, False]
        foreground[0] = 30
        return True

    api = SimpleNamespace(
        assert_owner=lambda *args: None,
        activate_owned_caption=caption,
        gui=SimpleNamespace(
            GetAncestor=lambda *args: 10,
            IsIconic=lambda hwnd: False,
            GetForegroundWindow=lambda: foreground[0],
            SetForegroundWindow=activate,
        ),
    )
    assert activate_client(10, {}, api=api) and foreground[0] == 10


def test_windows_focus_denial_does_not_stop_refocus_loop():
    import pywintypes

    now = [0.0]
    r = AutoRefocuser(clock=lambda: now[0])

    def denied():
        raise pywintypes.error(0, "SetForegroundWindow", "Denied")

    r.step(True, False, denied)
    now[0] = 1
    assert r.step(True, False, denied) is False
    now[0] = 2
    assert r.step(True, False, lambda: True) is True


def test_foreground_denial_with_windows_zero_is_deferred_and_detached(monkeypatch):
    import win32api, win32process, pywintypes

    calls = []
    monkeypatch.setattr(win32api, "GetCurrentThreadId", lambda: 1)
    monkeypatch.setattr(win32process, "GetWindowThreadProcessId", lambda hwnd: (2, 3))
    monkeypatch.setattr(
        win32process, "AttachThreadInput", lambda a, b, on: calls.append(on)
    )

    def denied(hwnd):
        raise pywintypes.error(
            0, "SetForegroundWindow", "No error message is available"
        )

    api = SimpleNamespace(
        assert_owner=lambda *args: None,
        gui=SimpleNamespace(
            GetAncestor=lambda *args: 10,
            IsIconic=lambda hwnd: False,
            GetForegroundWindow=lambda: 20,
            SetForegroundWindow=denied,
        ),
    )
    assert activate_client(10, {}, api=api) is False
    assert calls == [True, False]


def test_focus_wait_observes_delayed_activation_without_new_input(monkeypatch):
    from conquest import focus_recovery as focus

    now = [0.0]
    checks = []
    monkeypatch.setattr(focus.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(
        focus.time, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds)
    )
    monkeypatch.setattr(
        "conquest.merchants.coordination.check_input", lambda: checks.append(now[0])
    )
    api = SimpleNamespace(
        assert_owner=lambda *a: None,
        gui=SimpleNamespace(GetForegroundWindow=lambda: 10 if now[0] >= 0.1 else 20),
    )
    assert focus.settled_foreground(api, 10, {}, 10)
    assert 0.1 <= now[0] <= 0.2 and len(checks) > 1


def test_focus_wait_honors_stop_before_delayed_activation(monkeypatch):
    from conquest.focus_recovery import settled_foreground
    from conquest.capture import CaptureUnavailable

    def stopped():
        raise CaptureUnavailable("Manual Stop")

    monkeypatch.setattr("conquest.merchants.coordination.check_input", stopped)
    with pytest.raises(CaptureUnavailable, match="Manual Stop"):
        settled_foreground(SimpleNamespace(), 10, {}, 10)


def test_combined_activation_verifies_keyboard_focus_and_final_foreground(monkeypatch):
    from conquest import focus_recovery as module

    foreground = [10]
    calls = []
    api = SimpleNamespace(
        assert_owner=lambda *a: calls.append("identity"),
        gui=SimpleNamespace(
            GetAncestor=lambda *a: 10, GetForegroundWindow=lambda: foreground[0]
        ),
    )
    monkeypatch.setattr(
        module, "activate_client", lambda *a, **k: calls.append("activate") or True
    )
    monkeypatch.setattr(
        "conquest.merchants.coordination.check_input", lambda: calls.append("check")
    )

    def focus():
        calls.append("focus")
        return 21

    assert activate_focused_client(20, {"pid": 1}, api=api, focus=focus) == 21
    assert calls == [
        "identity",
        "activate",
        "check",
        "identity",
        "focus",
        "check",
        "identity",
    ]


def test_combined_activation_rejects_focus_lost_after_keyboard_transfer(monkeypatch):
    from conquest import focus_recovery as module

    foreground = [10]
    api = SimpleNamespace(
        assert_owner=lambda *a: None,
        gui=SimpleNamespace(
            GetAncestor=lambda *a: 10, GetForegroundWindow=lambda: foreground[0]
        ),
    )
    monkeypatch.setattr(module, "activate_client", lambda *a, **k: True)
    monkeypatch.setattr("conquest.merchants.coordination.check_input", lambda: None)

    def focus():
        foreground[0] = 99
        return 21

    assert activate_focused_client(20, {"pid": 1}, api=api, focus=focus) is False
