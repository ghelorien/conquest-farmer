"""Read-only live integration checks against this test process, never the game."""

import os

import pytest

from conquest.win32 import WindowsBackend

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows-only adapter")


def test_own_process_identity_and_read_access():
    backend = WindowsBackend()
    first = backend.identity(os.getpid())
    assert first["pid"] == os.getpid()
    assert first["architecture"] in ("x86", "x64", "arm64")
    assert os.path.isfile(first["path"])
    backend.check_memory_access(os.getpid())
    assert backend.identity(os.getpid()) == first


def test_process_enumeration_finds_self():
    backend = WindowsBackend()
    identity = backend.identity(os.getpid())
    matches = backend.processes(os.path.basename(identity["path"]))
    assert os.getpid() in [process["pid"] for process in matches]


def test_handle_closed_when_scope_raises():
    backend = WindowsBackend()
    real_close = backend.close_handle
    closed = []

    def close(handle):
        closed.append(handle)
        return real_close(handle)

    backend.close_handle = close
    with pytest.raises(ValueError, match="test failure"):
        with backend.process_handle(os.getpid(), 0x1000) as handle:
            raise ValueError("test failure")
    assert closed == [handle]


def test_window_enumeration_accepts_no_windows():
    assert WindowsBackend().windows(0) == []


def test_actual_read_and_module_resolution_on_own_process():
    import ctypes
    import struct
    from pathlib import Path
    from conquest.identity import fingerprint
    from conquest.memory import MemorySession

    identity = WindowsBackend().identity(os.getpid())
    path = Path(identity["path"])
    value = ctypes.c_uint32(0x12345678)
    with MemorySession(os.getpid(), fingerprint(path)["sha256"], path.name) as session:
        assert session.read(ctypes.addressof(value), 4) == struct.pack(
            "<I", value.value
        )
        assert any(
            region.base <= ctypes.addressof(value) < region.base + region.size
            for region in session.regions()
        )
        with pytest.raises(ValueError, match="outside calibration bounds"):
            session.read(ctypes.addressof(value), 3 * 1024 * 1024)
    with pytest.raises(ValueError, match="closed"):
        session.read(ctypes.addressof(value), 4)
