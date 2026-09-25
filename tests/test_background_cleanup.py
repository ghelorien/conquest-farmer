from types import SimpleNamespace
import subprocess
import sys
import threading

import pytest

from conquest.merchants.background_probe import restoration_lease


@pytest.mark.skipif(sys.platform != "win32", reason="Windows process byte lock")
def test_restoration_respects_separate_process_lock_and_still_works_when_stopped(
    tmp_path,
):
    path = tmp_path / "input.lock"
    code = """import msvcrt,sys
with open(sys.argv[1],'w+b') as f:
 f.write(b'0');f.flush();f.seek(0)
 msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
 print('ready',flush=True)
 sys.stdin.readline()
"""
    child = subprocess.Popen(
        [sys.executable, "-c", code, str(path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    ui = SimpleNamespace(
        coordinator=SimpleNamespace(
            lock=threading.RLock(), owner=None, thread=None, path=path, stopped=True
        )
    )
    try:
        assert child.stdout.readline().strip() == "ready"
        with pytest.raises(OSError):
            with restoration_lease(ui):
                pytest.fail("Foreign lease was ignored")
        assert ui.coordinator.owner is None
    finally:
        child.communicate("\n", timeout=5)
    with restoration_lease(ui):
        assert ui.coordinator.owner == "Background restoration"
        assert ui.coordinator.stopped is True
    assert ui.coordinator.owner is None
