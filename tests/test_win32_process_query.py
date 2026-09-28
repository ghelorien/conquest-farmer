"""Process enumeration takes one system snapshot instead of a toolhelp walk.

Live 2026-09-28 (Suicide's laptop, 245 processes): the toolhelp walk cost
5.3 ms of CPU and two kernel calls per process, each ctypes call handing the
GIL to the farmer's threads (~490 handoffs). merchant-market-safety spent 21%
of its samples there at 4 Hz. NtQuerySystemInformation returns every process
in one call: 2.5 ms, one GIL release.

Failure modes, written before the change:
1. The snapshot misses a process the toolhelp walk finds, or matches by a
   different rule (case-insensitive image name).
2. A failing snapshot query leaves discovery with nothing: it must fall back
   to the toolhelp walk.
3. A process list that outgrew the buffer is not retried with a larger one.
4. Enumeration hands off the GIL per process.
"""

import os

import pytest

from conquest.win32 import WindowsBackend

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows-only adapter")


def own_image():
    return os.path.basename(WindowsBackend().identity(os.getpid())["path"])


def test_the_snapshot_finds_this_process_like_the_toolhelp_walk():
    # 1
    backend = WindowsBackend()
    name = own_image()
    for spelling in (name, name.upper(), name.lower()):
        found = backend.processes(spelling)
        assert os.getpid() in [p["pid"] for p in found]
        assert all(p["executable_name"].casefold() == name.casefold() for p in found)
    walked = {p["pid"] for p in backend.toolhelp_processes(name)}
    queried = {p["pid"] for p in backend.processes(name)}
    # Other test processes may start or exit between the two calls.
    assert os.getpid() in walked & queried
    assert backend.processes("no-such-image-3c9f.exe") == []


def test_a_failed_query_falls_back_to_the_toolhelp_walk(monkeypatch):
    # 2
    backend = WindowsBackend()
    backend.system_information = lambda *args: -0x3FFFFFDE  # STATUS_ACCESS_DENIED
    assert os.getpid() in [p["pid"] for p in backend.processes(own_image())]


def test_a_grown_process_list_is_retried_with_a_larger_buffer():
    # 3
    backend = WindowsBackend()
    real = backend.system_information
    sizes = []

    def query(kind, buffer, size, needed):
        sizes.append(size)
        if len(sizes) == 1:
            needed.value = size + 4096
            return -0x3FFFFFFC  # STATUS_INFO_LENGTH_MISMATCH
        return real(kind, buffer, size, needed)

    backend.system_information = query
    backend.process_buffer_size = 4096
    assert os.getpid() in [p["pid"] for p in backend.processes(own_image())]
    assert len(sizes) >= 2 and sizes[1] > sizes[0]


def test_the_snapshot_needs_one_query_call():
    # 4: one kernel call for the whole list, not two per process.
    backend = WindowsBackend()
    real = backend.system_information
    calls = []
    backend.system_information = lambda *args: calls.append(1) or real(*args)
    backend.processes(own_image())
    assert len(calls) == 1
