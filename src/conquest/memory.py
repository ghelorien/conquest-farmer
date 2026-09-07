"""Bounded, read-only process memory access for calibration.

Only query and read rights are requested. No process memory is persisted here.
"""

import ctypes as c
from ctypes import wintypes as w
from dataclasses import dataclass
from pathlib import Path

from conquest.identity import fingerprint
from conquest.win32 import WindowsBackend, bind


class MemoryBasicInformation(c.Structure):
    _fields_ = [
        ("BaseAddress", c.c_void_p), ("AllocationBase", c.c_void_p),
        ("AllocationProtect", w.DWORD), ("PartitionId", w.WORD),
        ("RegionSize", c.c_size_t), ("State", w.DWORD),
        ("Protect", w.DWORD), ("Type", w.DWORD),
    ]


class ModuleEntry(c.Structure):
    _fields_ = [
        ("dwSize", w.DWORD), ("th32ModuleID", w.DWORD), ("th32ProcessID", w.DWORD),
        ("GlblcntUsage", w.DWORD), ("ProccntUsage", w.DWORD),
        ("modBaseAddr", c.c_void_p), ("modBaseSize", w.DWORD),
        ("hModule", w.HMODULE), ("szModule", w.WCHAR * 256),
        ("szExePath", w.WCHAR * 260),
    ]


@dataclass(frozen=True)
class Region:
    base: int
    size: int
    allocation_base: int
    kind: int
    protection: int


class MemorySession:
    def __init__(self, pid: int, expected_sha256: str, executable="ImConquer.exe"):
        self.backend = WindowsBackend()
        self.pid = pid
        self.expected_sha256 = expected_sha256
        self.executable = executable
        self.handle = None
        self.identity = None
        self.modules = []
        self.read_api = bind(self.backend.kernel, "ReadProcessMemory", [w.HANDLE, c.c_void_p, c.c_void_p, c.c_size_t, c.POINTER(c.c_size_t)], w.BOOL)
        self.query_api = bind(self.backend.kernel, "VirtualQueryEx", [w.HANDLE, c.c_void_p, c.POINTER(MemoryBasicInformation), c.c_size_t], c.c_size_t)

    def __enter__(self):
        if c.sizeof(c.c_void_p) != 8:
            raise ValueError("Calibration requires 64-bit Python")
        self.identity = self.backend.identity(self.pid)
        if Path(self.identity["path"]).name.casefold() != self.executable.casefold():
            raise ValueError("Process executable does not match calibration target")
        if self.identity["architecture"] != "x64":
            raise ValueError("This calibration adapter supports the inspected x64 client only")
        image = fingerprint(Path(self.identity["path"]))
        if image["sha256"] != self.expected_sha256:
            raise ValueError("Executable fingerprint differs; discard this client profile")
        # VirtualQueryEx requires QUERY_INFORMATION; ReadProcessMemory requires VM_READ.
        self.handle = self.backend.open_process(0x0400 | 0x0010, False, self.pid)
        if not self.handle:
            raise self.backend.error("OpenProcess(QUERY_INFORMATION | VM_READ)")
        try:
            self.assert_identity()
            self.modules = self._modules()
            main = next((module for module in self.modules if Path(module["path"]).name.casefold() == self.executable.casefold()), None)
            if main is None or self.read(main["base"], 2) != b"MZ":
                raise ValueError("Could not verify an actual read of the main module header")
            return self
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.handle:
            self.backend.close_handle(self.handle)
            self.handle = None

    def __exit__(self, *_):
        self.close()

    def assert_identity(self):
        if self.backend.identity(self.pid) != self.identity:
            raise ValueError("Process exited or restarted; calibration addresses are invalid")

    def read(self, address: int, size: int) -> bytes:
        if not self.handle:
            raise ValueError("Memory session is closed")
        if address < 0x10000 or not 0 < size <= 2 * 1024 * 1024:
            raise ValueError("Read address or size is outside calibration bounds")
        buffer = c.create_string_buffer(size)
        received = c.c_size_t()
        if not self.read_api(self.handle, address, buffer, size, c.byref(received)):
            raise self.backend.error(f"ReadProcessMemory(address=0x{address:x}, size={size})")
        if received.value != size:
            raise OSError(f"Short memory read: expected {size}, received {received.value}")
        return buffer.raw

    def regions(self):
        address = 0
        while address < 0x7FFFFFFFFFFF:
            information = MemoryBasicInformation()
            size = self.query_api(self.handle, address, c.byref(information), c.sizeof(information))
            if not size:
                if c.get_last_error() == 87:  # End of the process address space.
                    return
                raise self.backend.error("VirtualQueryEx")
            if size != c.sizeof(information):
                raise OSError("Unexpected MEMORY_BASIC_INFORMATION size")
            base = information.BaseAddress or 0
            end = base + information.RegionSize
            if end <= address:
                raise OSError("VirtualQueryEx returned a non-advancing region")
            # Scan writable data in private allocations and image data only.
            # Exclude guard, executable, no-access, and mapped graphics regions.
            if (information.State == 0x1000 and information.Type in (0x20000, 0x1000000)
                    and not information.Protect & 0x100
                    and information.Protect & 0xFF in (0x04, 0x08)):
                yield Region(base, information.RegionSize, information.AllocationBase or 0,
                             information.Type, information.Protect)
            address = end

    def _modules(self):
        first = bind(self.backend.kernel, "Module32FirstW", [w.HANDLE, c.POINTER(ModuleEntry)], w.BOOL)
        next_module = bind(self.backend.kernel, "Module32NextW", [w.HANDLE, c.POINTER(ModuleEntry)], w.BOOL)
        snapshot = self.backend.snapshot(0x08 | 0x10, self.pid)
        if snapshot == c.c_void_p(-1).value:
            raise self.backend.error("CreateToolhelp32Snapshot(modules)")
        result = []
        try:
            module = ModuleEntry()
            module.dwSize = c.sizeof(module)
            success = first(snapshot, c.byref(module))
            while success:
                result.append({"name": module.szModule, "path": module.szExePath,
                               "base": int(module.modBaseAddr), "size": module.modBaseSize})
                success = next_module(snapshot, c.byref(module))
            if c.get_last_error() != 18:
                raise self.backend.error("Module32FirstW/Module32NextW")
        finally:
            self.backend.close_handle(snapshot)
        return result
