"""Request a normal close of one exact Conquest controller window.

This does not terminate a process or send input to a game client. It is meant
to run with the same administrator integrity level as the desktop controller.
"""

import argparse
import ctypes
from ctypes import wintypes


class FILETIME(ctypes.Structure):
    _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]


def expected_title(data_root=None):
    """The title the desktop app derives from this PC's local profiles."""
    from conquest.character_profiles import ProfileRegistry
    from conquest.portable_ui import controller_title

    return controller_title(ProfileRegistry(data_root))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--created", type=int, required=True)
    parser.add_argument("--hwnd", type=int, required=True)
    parser.add_argument(
        "--data-root",
        help="Managed data root whose profiles titled the controller "
        "(default: CONQUEST_DATA_ROOT or %%LOCALAPPDATA%%\\Conquest)",
    )
    args = parser.parse_args()
    if min(args.pid, args.created, args.hwnd) <= 0:
        raise ValueError("Positive controller identity is required")
    title_expected = expected_title(args.data_root)

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    user = ctypes.WinDLL("user32", use_last_error=True)
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(FILETIME),
        ctypes.POINTER(FILETIME),
        ctypes.POINTER(FILETIME),
        ctypes.POINTER(FILETIME),
    )
    kernel.GetProcessTimes.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    user.GetWindowThreadProcessId.argtypes = (
        wintypes.HWND,
        ctypes.POINTER(wintypes.DWORD),
    )
    user.GetWindowThreadProcessId.restype = wintypes.DWORD
    user.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    user.GetWindowTextW.restype = ctypes.c_int
    user.PostMessageW.argtypes = (
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    )
    user.PostMessageW.restype = wintypes.BOOL

    handle = kernel.OpenProcess(0x1000, False, args.pid)
    if not handle:
        raise OSError(ctypes.get_last_error(), "Cannot query controller process")
    try:
        created, exited, kernel_time, user_time = (
            FILETIME(),
            FILETIME(),
            FILETIME(),
            FILETIME(),
        )
        if not kernel.GetProcessTimes(
            handle,
            ctypes.byref(created),
            ctypes.byref(exited),
            ctypes.byref(kernel_time),
            ctypes.byref(user_time),
        ):
            raise OSError(
                ctypes.get_last_error(), "Cannot read controller creation time"
            )
        actual_created = created.low | (created.high << 32)
        if actual_created != args.created:
            raise ValueError("Controller creation identity changed")
        owner = wintypes.DWORD()
        if (
            not user.GetWindowThreadProcessId(args.hwnd, ctypes.byref(owner))
            or owner.value != args.pid
        ):
            raise ValueError("Controller window owner changed")
        title = ctypes.create_unicode_buffer(max(128, len(title_expected) + 2))
        user.GetWindowTextW(args.hwnd, title, len(title))
        if title.value != title_expected:
            raise ValueError("Controller window title changed")
        if not user.PostMessageW(args.hwnd, 0x0010, 0, 0):
            raise OSError(
                ctypes.get_last_error(), "Normal controller close was refused"
            )
        print("normal_close_sent")
    finally:
        kernel.CloseHandle(handle)


if __name__ == "__main__":
    main()
