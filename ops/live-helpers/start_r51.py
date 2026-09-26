"""Start the Conquest controller on a built release after a PC restart.

Activates the release, launches the desktop app DETACHED (no handles shared
with the calling shell, so the shell never waits on the app), waits for the
embedded farmer worker, then switches Farming On once. Game clients are
identified by their window titles; nothing is typed into any client.
Usage: python start_r51.py <release-id> [--no-farming]
"""

import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(
    r"C:\Users\Floor\AppData\Local\Packages\OpenAI.Codex_2p2nqsd0c76g0"
    r"\LocalCache\Local\Conquest"
)
RELEASES = Path(r"C:\Users\Floor\Documents\ChatGPT\Conquest-releases")
PROFILE = "6c98e401-89a9-4d8a-9bfd-9a2ed7880d26"
CHAR = ROOT / "characters" / PROFILE
HERE = Path(__file__).resolve().parent
os.environ["CONQUEST_DATA_ROOT"] = str(ROOT)
os.environ["CONQUEST_PROFILE_ID"] = PROFILE
sys.path.insert(0, r"C:\Users\Floor\Documents\ChatGPT\cf-smooth\src")

from conquest.discord_notify import read_json, write_json, process_alive  # noqa: E402
from conquest.release import activate_release, launch_active_release, verify_release  # noqa: E402
from conquest.win32 import WindowsBackend  # noqa: E402
from conquest.worker import request as worker  # noqa: E402


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def detached(args, **kwargs):
    flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    return subprocess.Popen(
        args,
        creationflags=flags,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        **kwargs,
    )


def farmer_client(backend):
    import ctypes

    user32 = ctypes.windll.user32
    titles = {}

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def each(hwnd, _):
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        length = user32.GetWindowTextLengthW(hwnd)
        if length and user32.IsWindowVisible(hwnd):
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            titles.setdefault(pid.value, []).append((hwnd, buffer.value))
        return True

    user32.EnumWindows(each, 0)
    matches = [
        (pid, hwnd)
        for pid, rows in titles.items()
        for hwnd, title in rows
        if title == "[Parasite - ClassicConquer]"
    ]
    require(len(matches) == 1, "Parasite must be logged in (one '[Parasite - ClassicConquer]' window)")
    pid, hwnd = matches[0]
    identity = backend.identity(pid)
    require(identity and identity["path"].lower().endswith("imconquer.exe"), "Farmer client identity unreadable")
    return identity, int(hwnd)


def main():
    release_id = sys.argv[1]
    farming = "--no-farming" not in sys.argv
    result = HERE / f"start-{release_id}.json"
    release = RELEASES / release_id
    verify_release(release)
    app = read_json(CHAR / "reports/desktop-farming/app-state.json") or {}
    require(
        not process_alive(app.get("pid")) or app.get("pid") is None,
        "A Conquest controller is already running; use the deploy helper instead",
    )
    require(not (CHAR / ".runtime/overnight.stop").exists(), "A user Stop marker is present")
    backend = WindowsBackend()
    identity, hwnd = farmer_client(backend)
    write_json(result, {"phase": "activating", "at": time.time(), "farmer": identity, "hwnd": hwnd})
    activate_release(release, state_root=ROOT)
    launched = launch_active_release(
        [
            "--profile-id",
            PROFILE,
            "--embed-client",
            "--client-pid",
            str(identity["pid"]),
            "--client-started",
            str(identity["creation_time_100ns"]),
            "--client-hwnd",
            str(hwnd),
        ],
        state_root=ROOT,
        popen=detached,
    )
    write_json(result, {"phase": "launched", "at": time.time(), "launcher_pid": launched.pid})
    deadline = time.monotonic() + 120
    info = None
    while time.monotonic() < deadline:
        app = read_json(CHAR / "reports/desktop-farming/app-state.json") or {}
        path = Path(app.get("worker_info_path") or "")
        if app.get("pid") and process_alive(app["pid"]) and path.is_file():
            try:
                health = worker(path, "health")
                if health.get("target", {}).get("pid") == identity["pid"]:
                    info = path
                    break
            except (OSError, ValueError):
                pass
        time.sleep(1)
    require(info is not None, "Controller did not attach to the farmer in time")
    write_json(result, {"phase": "attached", "at": time.time(), "controller": app.get("pid")})
    if not farming:
        print(json.dumps({"attached": True, "farming": False}))
        return
    time.sleep(5)
    embedded = worker(info, "health")["embedded_controls"]
    require(
        not embedded.get("manual_mouse") and not embedded.get("manual_input_fence"),
        "Manual input is active; Farming not switched On",
    )
    current = worker(info, "controls", {"enabled": True, "explicit_restart": True})
    require(current.get("enabled") is True, "Farming control response is uncertain")
    write_json(result, {"phase": "farming_on", "at": time.time(), "revision": current.get("revision")})
    print(json.dumps({"attached": True, "farming": True, "revision": current.get("revision")}))


if __name__ == "__main__":
    main()
