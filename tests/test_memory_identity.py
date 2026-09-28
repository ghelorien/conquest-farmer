"""MemorySession proves process identity through its own held handle.

Read-only checks against this test process, never the game. The full
identity() opened another handle and made four more calls per check, each
releasing the GIL: 16-18% of the combat thread's samples (2026-09-28).
"""

import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows-only adapter")


def own_session():
    from conquest.identity import fingerprint
    from conquest.memory import MemorySession
    from conquest.win32 import WindowsBackend

    path = Path(WindowsBackend().identity(os.getpid())["path"])
    return MemorySession(os.getpid(), fingerprint(path)["sha256"], path.name)


def test_live_check_uses_the_held_handle_only():
    with own_session() as session:
        assert session.handle_verified
        session.backend.identity = lambda pid: pytest.fail("identity() re-queried")
        session.assert_identity()


def test_exited_process_reads_as_identity_did(monkeypatch):
    import ctypes

    with own_session() as session:

        def exited(handle, code):
            ctypes.cast(code, ctypes.POINTER(ctypes.c_ulong)).contents.value = 0
            return True

        session.backend.exit_code = exited
        # manual_farmer releases a reader hold on exactly this wording.
        with pytest.raises(OSError, match=rf"^Process {os.getpid()} exited during diagnostics$"):
            session.assert_identity()


def test_handle_of_another_process_instance_is_refused(monkeypatch):
    from conquest.win32 import WindowsBackend

    real = WindowsBackend.identity

    def older(self, pid):
        identity = dict(real(self, pid))
        identity["creation_time_100ns"] -= 1  # the PID's previous owner
        return identity

    monkeypatch.setattr(WindowsBackend, "identity", older)
    session = own_session()
    with pytest.raises(ValueError, match="exited or restarted"):
        session.__enter__()
    assert session.handle is None and not session.handle_verified


def test_closed_session_falls_back_to_the_full_identity():
    session = own_session().__enter__()
    session.close()
    assert not session.handle_verified
    calls = []
    identity = session.identity
    session.backend.identity = lambda pid: calls.append(pid) or identity
    session.assert_identity()
    assert calls == [os.getpid()]
