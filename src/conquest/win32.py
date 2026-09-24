"""Minimal, read-only Win32 bindings. Does not elevate or request write access."""

import ctypes as c
import os
from contextlib import contextmanager
from ctypes import wintypes as w


class ProcessEntry(c.Structure):
    _fields_ = [
        ("dwSize", w.DWORD),
        ("cntUsage", w.DWORD),
        ("th32ProcessID", w.DWORD),
        ("th32DefaultHeapID", c.c_size_t),
        ("th32ModuleID", w.DWORD),
        ("cntThreads", w.DWORD),
        ("th32ParentProcessID", w.DWORD),
        ("pcPriClassBase", w.LONG),
        ("dwFlags", w.DWORD),
        ("szExeFile", w.WCHAR * 260),
    ]


def bind(library, name, args, result):
    function = getattr(library, name)
    function.argtypes = args
    function.restype = result
    return function


class WindowsBackend:
    def __init__(self):
        if os.name != "nt":
            raise OSError("Live diagnostics require Windows")
        self.kernel = c.WinDLL("kernel32", use_last_error=True)
        self.user = c.WinDLL("user32", use_last_error=True)
        self.open_process = bind(
            self.kernel, "OpenProcess", [w.DWORD, w.BOOL, w.DWORD], w.HANDLE
        )
        self.close_handle = bind(self.kernel, "CloseHandle", [w.HANDLE], w.BOOL)
        self.snapshot = bind(
            self.kernel, "CreateToolhelp32Snapshot", [w.DWORD, w.DWORD], w.HANDLE
        )
        self.first = bind(
            self.kernel, "Process32FirstW", [w.HANDLE, c.POINTER(ProcessEntry)], w.BOOL
        )
        self.next = bind(
            self.kernel, "Process32NextW", [w.HANDLE, c.POINTER(ProcessEntry)], w.BOOL
        )
        self.image_name = bind(
            self.kernel,
            "QueryFullProcessImageNameW",
            [w.HANDLE, w.DWORD, w.LPWSTR, c.POINTER(w.DWORD)],
            w.BOOL,
        )
        self.times = bind(
            self.kernel,
            "GetProcessTimes",
            [w.HANDLE] + [c.POINTER(w.FILETIME)] * 4,
            w.BOOL,
        )
        self.exit_code = bind(
            self.kernel, "GetExitCodeProcess", [w.HANDLE, c.POINTER(w.DWORD)], w.BOOL
        )
        self.wow64 = bind(
            self.kernel,
            "IsWow64Process2",
            [w.HANDLE, c.POINTER(w.WORD), c.POINTER(w.WORD)],
            w.BOOL,
        )
        self.window_callback = c.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
        self.enum_windows = bind(
            self.user, "EnumWindows", [self.window_callback, w.LPARAM], w.BOOL
        )
        self.window_pid = bind(
            self.user, "GetWindowThreadProcessId", [w.HWND, c.POINTER(w.DWORD)], w.DWORD
        )
        self.window_text = bind(
            self.user, "GetWindowTextW", [w.HWND, w.LPWSTR, c.c_int], c.c_int
        )
        self.visible = bind(self.user, "IsWindowVisible", [w.HWND], w.BOOL)
        self.iconic = bind(self.user, "IsIconic", [w.HWND], w.BOOL)
        self.foreground = bind(self.user, "GetForegroundWindow", [], w.HWND)
        self.client_rect = bind(
            self.user, "GetClientRect", [w.HWND, c.POINTER(w.RECT)], w.BOOL
        )

    @staticmethod
    def error(operation: str) -> OSError:
        code = c.get_last_error()
        return c.WinError(code, f"{operation}: {c.FormatError(code).strip()}")

    @contextmanager
    def process_handle(self, pid: int, access: int):
        handle = self.open_process(access, False, pid)
        if not handle:
            raise self.error(f"OpenProcess(pid={pid}, access=0x{access:04x})")
        try:
            yield handle
        finally:
            self.close_handle(handle)

    def processes(self, executable: str) -> list[dict]:
        handle = self.snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
        if handle == c.c_void_p(-1).value:
            raise self.error("CreateToolhelp32Snapshot")
        matches = []
        try:
            entry = ProcessEntry()
            entry.dwSize = c.sizeof(entry)
            success = self.first(handle, c.byref(entry))
            while success:
                if entry.szExeFile.casefold() == executable.casefold():
                    matches.append(
                        {"pid": entry.th32ProcessID, "executable_name": entry.szExeFile}
                    )
                success = self.next(handle, c.byref(entry))
            if c.get_last_error() != 18:  # ERROR_NO_MORE_FILES
                raise self.error("Process32FirstW/Process32NextW")
        finally:
            self.close_handle(handle)
        return matches

    def windows(self, pid: int) -> list[dict]:
        windows = []
        callback_errors = []
        foreground = self.foreground()

        @self.window_callback
        def visit(hwnd, _):
            try:
                owner = w.DWORD()
                if not self.window_pid(hwnd, c.byref(owner)):
                    return True  # Window may have closed during enumeration.
                if owner.value == pid:
                    title = c.create_unicode_buffer(4096)
                    self.window_text(hwnd, title, len(title))
                    rect = w.RECT()
                    has_rect = self.client_rect(hwnd, c.byref(rect))
                    windows.append(
                        {
                            "hwnd": int(hwnd),
                            "title": title.value,
                            "visible": bool(self.visible(hwnd)),
                            "minimized": bool(self.iconic(hwnd)),
                            "foreground": hwnd == foreground,
                            "client_size": [
                                rect.right - rect.left,
                                rect.bottom - rect.top,
                            ]
                            if has_rect
                            else None,
                        }
                    )
                return True
            except Exception as error:
                callback_errors.append(error)
                return False

        success = self.enum_windows(visit, 0)
        if callback_errors:
            raise OSError(f"Window enumeration failed: {callback_errors[0]}")
        if not success:
            raise self.error("EnumWindows")
        return windows

    def identity(self, pid: int) -> dict:
        with self.process_handle(
            pid, 0x1000
        ) as handle:  # PROCESS_QUERY_LIMITED_INFORMATION
            path = c.create_unicode_buffer(32768)
            size = w.DWORD(len(path))
            if not self.image_name(handle, 0, path, c.byref(size)):
                raise self.error("QueryFullProcessImageNameW")
            creation, exit_time, kernel_time, user_time = (
                w.FILETIME() for _ in range(4)
            )
            if not self.times(
                handle,
                c.byref(creation),
                c.byref(exit_time),
                c.byref(kernel_time),
                c.byref(user_time),
            ):
                raise self.error("GetProcessTimes")
            process_machine, native_machine = w.WORD(), w.WORD()
            if not self.wow64(
                handle, c.byref(process_machine), c.byref(native_machine)
            ):
                raise self.error("IsWow64Process2")
            code = w.DWORD()
            if not self.exit_code(handle, c.byref(code)):
                raise self.error("GetExitCodeProcess")
            if code.value != 259:  # STILL_ACTIVE
                raise OSError(f"Process {pid} exited during diagnostics")
            machine = process_machine.value or native_machine.value
            return {
                "pid": pid,
                "path": path.value,
                "creation_time_100ns": (creation.dwHighDateTime << 32)
                | creation.dwLowDateTime,
                "architecture": {0x014C: "x86", 0x8664: "x64", 0xAA64: "arm64"}.get(
                    machine, f"0x{machine:04x}"
                ),
            }

    def check_memory_access(self, pid: int) -> None:
        with self.process_handle(pid, 0x0010):  # PROCESS_VM_READ only
            pass
