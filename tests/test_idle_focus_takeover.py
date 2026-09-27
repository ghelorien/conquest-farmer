"""The farmer takes the foreground back from another app once the PC is idle.

Live 2026-09-27 on Laptop2 the Claude window kept the foreground for over two
minutes while Suicide walked through the Robin field: Windows refused every
background SetForegroundWindow (and the shared-queue retry), the route could
send no input, and Suicide stood under attack down to 9.9% HP.

Ways this can fail, written before the code:
1. The farmer stays frozen behind another app while nobody uses the PC.
2. The takeover steals focus from a user typing or moving the mouse in
   another app.
3. The takeover runs before the ordinary SetForegroundWindow and shared-queue
   attempts, so it fires when a plain activation would have worked.
4. The ALT key stays held when activation raises.
5. Success is reported while the game still is not the foreground window.
6. A replaced client (identity change) receives the takeover input.
"""

import pywintypes
import pytest

from conquest.focus_recovery import activate_client
from conquest.window_host import HostApi

GAME, OTHER_APP = 10, 20


class Desktop:
    """Foreground lock: another app keeps focus unless ALT is held."""

    def __init__(self, idle, *, raise_on_takeover=False):
        self.foreground = OTHER_APP
        self.alt_down = False
        self.idle = idle
        self.raise_on_takeover = raise_on_takeover
        self.log = []

    def SetForegroundWindow(self, hwnd):
        self.log.append(("activate", self.alt_down))
        if self.alt_down and self.raise_on_takeover:
            raise pywintypes.error(0, "SetForegroundWindow", "Denied")
        if not self.alt_down:
            raise pywintypes.error(0, "SetForegroundWindow", "Denied")
        self.foreground = hwnd

    def GetForegroundWindow(self):
        return self.foreground

    def GetAncestor(self, hwnd, flag):
        return hwnd

    def IsIconic(self, hwnd):
        return False


def host(desktop, *, owner=lambda hwnd, identity: None):
    api = HostApi.__new__(HostApi)
    api.gui = desktop
    api.assert_owner = owner
    api.idle_seconds = lambda: desktop.idle

    def key_event(vk, up):
        assert vk == 0x12
        desktop.alt_down = not up
        desktop.log.append(("alt_up" if up else "alt_down", None))

    api.key_event = key_event
    # The verified-caption fallbacks click window captions; out of scope here.
    api.activate_owned_caption = api.activate_native_caption = lambda *args: False
    return api


@pytest.fixture(autouse=True)
def single_queue(monkeypatch):
    import win32api
    import win32process

    monkeypatch.setattr(win32api, "GetCurrentThreadId", lambda: 1)
    monkeypatch.setattr(win32process, "GetWindowThreadProcessId", lambda hwnd: (2, 3))
    monkeypatch.setattr(win32process, "AttachThreadInput", lambda a, b, on: None)
    monkeypatch.setattr("conquest.focus_recovery.time.sleep", lambda s: None)


def test_idle_pc_takes_the_foreground_back_after_plain_attempts():
    # 1, 3, 5: plain activations first, then one ALT-held activation.
    desktop = Desktop(idle=40)
    assert activate_client(GAME, {}, api=host(desktop)) is True
    assert desktop.foreground == GAME
    first_alt = desktop.log.index(("alt_down", None))
    assert first_alt > 0 and all(e == ("activate", False) for e in desktop.log[:first_alt])
    assert desktop.log[first_alt:] == [
        ("alt_down", None),
        ("activate", True),
        ("alt_up", None),
    ]
    assert desktop.alt_down is False


def test_active_user_keeps_their_window():
    # 2: someone typed or moved the mouse five seconds ago.
    desktop = Desktop(idle=5)
    assert activate_client(GAME, {}, api=host(desktop)) is False
    assert desktop.foreground == OTHER_APP
    assert ("alt_down", None) not in desktop.log


def test_alt_is_released_when_the_takeover_raises():
    # 4, 5
    desktop = Desktop(idle=40, raise_on_takeover=True)
    assert activate_client(GAME, {}, api=host(desktop)) is False
    assert desktop.alt_down is False and desktop.foreground == OTHER_APP


def test_replaced_client_never_gets_the_takeover():
    # 6
    desktop = Desktop(idle=40)
    checks = []

    def owner(hwnd, identity):
        checks.append(hwnd)
        if len(checks) > 1:
            raise ValueError("Client identity changed")

    with pytest.raises(ValueError, match="identity"):
        activate_client(GAME, {}, api=host(desktop, owner=owner))
    assert ("alt_down", None) not in desktop.log


def test_idle_seconds_reads_the_last_input_tick(monkeypatch):
    import win32api

    monkeypatch.setattr(win32api, "GetTickCount", lambda: 5_000)
    monkeypatch.setattr(win32api, "GetLastInputInfo", lambda: 2_000)
    assert HostApi.idle_seconds(HostApi.__new__(HostApi)) == 3.0
    # The 49.7-day tick wrap still gives a small positive idle time.
    monkeypatch.setattr(win32api, "GetTickCount", lambda: 1_000)
    monkeypatch.setattr(win32api, "GetLastInputInfo", lambda: 0xFFFFFFFF - 999)
    assert HostApi.idle_seconds(HostApi.__new__(HostApi)) == 2.0
