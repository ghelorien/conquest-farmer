"""Explicit Start-button elevation for the administrator-only game client."""

import ctypes
from ctypes import wintypes
from pathlib import Path
import subprocess
from conquest.character_context import context_arguments
from conquest.legacy_startup import configure


def start_arguments(
    root, profile, calibration, *, launch_client=False, embed_client=None
):
    startup = configure([])
    legacy = (
        ["--legacy-data-root", str(startup.legacy_root)] if startup.legacy_root else []
    )
    action = (
        "--launch-client"
        if launch_client
        else ("--calibrate" if calibration else "--start")
    )
    selected = []
    if embed_client is not None:
        if (
            launch_client
            or calibration
            or len(embed_client) != 3
            or any(type(v) is not int or v <= 0 for v in embed_client)
        ):
            raise ValueError(
                "Embedding requires one pinned process and window identity"
            )
        action = "--embed-client"
        selected = [
            value
            for name, number in zip(
                ("--client-pid", "--client-started", "--client-hwnd"), embed_client
            )
            for value in (name, str(number))
        ]
    return [
        str(Path(root) / "scripts/start_desktop_app.py"),
        "--profile",
        str(Path(profile).resolve()),
        action,
        *selected,
        *context_arguments(),
        *legacy,
    ]


def elevated_start(
    hwnd, root, profile, calibration=False, *, launch_client=False, embed_client=None
):
    """One consent request initiated by Start; cancellation never retries."""
    from conquest.application_layout import RuntimeLayout

    layout = RuntimeLayout.resolve(root)
    root = layout.root
    layout.script("start_desktop_app.py")
    python = layout.python(windowed=True)
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    execute = shell.ShellExecuteW
    execute.argtypes = [
        wintypes.HWND,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        ctypes.c_int,
    ]
    execute.restype = ctypes.c_void_p
    result = execute(
        hwnd,
        "runas",
        str(python),
        subprocess.list2cmdline(
            start_arguments(
                root,
                profile,
                calibration,
                launch_client=launch_client,
                embed_client=embed_client,
            )
        ),
        str(root),
        1,
    )
    if not result or result <= 32:
        raise PermissionError(
            "Windows approval was canceled or denied. Farming is stopped; no retry was made."
        )
