"""Real cross-process hosting check using only a temporary window we create."""

import json
import os
from pathlib import Path
import subprocess
import sys
import time
import tkinter as tk

import win32gui

from conquest.window_host import EmbeddedWindow, use_unaware_dpi


def main():
    use_unaware_dpi()
    root = tk.Tk()
    root.title("Conquest wrapper verification (temporary)")
    root.geometry("360x220+50+50")
    root.update()
    if "--child" in sys.argv:
        hwnd = win32gui.GetAncestor(root.winfo_id(), 2)
        print(json.dumps({"pid": os.getpid(), "hwnd": hwnd}), flush=True)
        root.after(15000, root.destroy)
        root.mainloop()
        return
    root.withdraw()
    pane = tk.Frame(root, width=300, height=180)
    pane.pack()
    root.update_idletasks()
    child = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--child"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    host = EmbeddedWindow()
    try:
        # Child prints once immediately after creating its window and self-exits
        # after 15 seconds, bounding this pipe wait even if setup fails.
        ready = json.loads(child.stdout.readline())
        hwnd = ready["hwnd"]
        identity = host.api.backend.identity(ready["pid"])
        before = host.api.snapshot(hwnd, identity)
        host.attach(hwnd, identity, pane.winfo_id(), 300, 180)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and win32gui.GetClientRect(hwnd)[2:] != (
            300,
            180,
        ):
            time.sleep(0.02)
        assert win32gui.GetParent(hwnd) == pane.winfo_id()
        assert win32gui.GetClientRect(hwnd)[2:] == (300, 180)
        host.resize(280, 160)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and win32gui.GetClientRect(hwnd)[2:] != (
            280,
            160,
        ):
            time.sleep(0.02)
        assert win32gui.GetClientRect(hwnd)[2:] == (280, 160)
        host.detach()
        after = host.api.snapshot(hwnd, identity)
        assert before.style == after.style
        assert before.exstyle == after.exstyle
        assert before.owner == after.owner
        assert before.placement == after.placement
        assert win32gui.GetParent(hwnd) == 0
        result = {
            "scope": "temporary Tk window, separate process",
            "embedded": True,
            "resized": True,
            "restored": True,
            "live_conquer_tested": False,
            "time": time.time(),
        }
        output = Path("reports/window-host-verification.json")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, indent=2))
        print(json.dumps(result))
    finally:
        if host.saved:
            host.detach()
        # Only our child process is stopped; no user/game process is touched.
        if child.poll() is None:
            child.terminate()
        child.wait(timeout=5)
        root.destroy()


if __name__ == "__main__":
    main()
