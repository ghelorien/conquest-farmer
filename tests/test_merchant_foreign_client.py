"""A merchant's discovery does not re-probe another character's client every second.

Live 2026-09-28 (Suicide's app, thread_sampler): the merchant thread of
Kalhiam, which does not run on that PC, opened a full memory session on
Suicide's client every second (identity, module snapshot, actor read) to learn
again that it was not Kalhiam, each step handing the GIL to the farmer.

Failure modes, written before the change:
1. A client already identified as another character is probed again on the
   next pass.
2. It is never probed again, so the merchant logged into that same client
   process later is never found.
3. A client passing through the login screen keeps its old verdict.
4. One merchant's verdict hides the client from another merchant.
5. The configured merchant's own client stops binding.
"""

from collections import defaultdict
import threading
from types import SimpleNamespace

import pytest

from test_merchant_uid_first_sight import (
    IDENTITY,
    FakeSession,
    Observer,
    World,
    activate,
    fresh_pc,
)


def rig(monkeypatch, tmp_path, actor):
    from conquest.character_context import merchant_context
    from conquest.client_attachment import AttachmentStatus
    from conquest.merchants import runtime as runtime_module

    registry, farmer = fresh_pc(tmp_path)
    activate(monkeypatch, registry, farmer)
    World(*actor).install(monkeypatch)
    state = SimpleNamespace(now=500.0, login=False, probes=0)

    class Counting(FakeSession):
        def __enter__(self):
            state.probes += 1
            return super().__enter__()

    monkeypatch.setattr("conquest.memory.MemorySession", Counting)
    monkeypatch.setattr(runtime_module.time, "monotonic", lambda: state.now)
    monkeypatch.setattr("conquest.reconnect.login_screen", lambda hwnd: state.login)
    monkeypatch.setattr(
        "conquest.merchants.return_1078.pinned_identity", lambda runtime, character: None
    )
    monkeypatch.setattr(
        "conquest.input_probe.MessageTarget", lambda pid, hwnd: ("target", pid, hwnd)
    )
    client = SimpleNamespace(hwnd=5150, identity=dict(IDENTITY))
    fake = SimpleNamespace(
        attachments=defaultdict(AttachmentStatus),
        discovery_lock=threading.Lock(),
        lock=threading.RLock(),
        observers={},
        latest={},
        merchant_windows=lambda: [client],
        observer_factory=lambda client, character: Observer(
            client, character, merchant_context(character)
        ),
        _bind_controller_1078=lambda c, o: None,
        _attach_login_1078=lambda *a: "login-rebind",
    )

    def attach(name):
        from conquest.character_context import resolve_merchant
        from conquest.merchants.runtime import MerchantRuntime

        return MerchantRuntime.attach_observation_1078(fake, resolve_merchant(name))

    return state, attach, fake


def test_another_characters_client_is_probed_twice_a_minute(tmp_path, monkeypatch):
    # 1, 2
    from conquest.merchants.runtime import FOREIGN_CLIENT_SECONDS

    state, attach, _ = rig(monkeypatch, tmp_path, ("Brix", 7002))
    for _ in range(5):
        with pytest.raises(ValueError, match="found 0"):
            attach("Kalhiam")
        state.now += 1
    assert state.probes == 1
    state.now += FOREIGN_CLIENT_SECONDS
    with pytest.raises(ValueError, match="found 0"):
        attach("Kalhiam")
    assert state.probes == 2


def test_the_login_screen_clears_the_verdict(tmp_path, monkeypatch):
    # 3
    state, attach, _ = rig(monkeypatch, tmp_path, ("Brix", 7002))
    with pytest.raises(ValueError, match="found 0"):
        attach("Kalhiam")
    state.login = True
    with pytest.raises(ValueError, match="found 0"):  # no actor to probe
        attach("Kalhiam")
    state.login = False
    state.now += 1
    with pytest.raises(ValueError, match="found 0"):
        attach("Kalhiam")
    assert state.probes == 2


def test_each_merchant_keeps_its_own_verdict_and_its_own_client_binds(
    tmp_path, monkeypatch
):
    # 4, 5
    state, attach, fake = rig(monkeypatch, tmp_path, ("Brix", 7002))
    with pytest.raises(ValueError, match="found 0"):
        attach("Kalhiam")
    attach("Brix")
    assert state.probes == 2
    assert [str(c) for c in fake.observers] == ["Brix"]
