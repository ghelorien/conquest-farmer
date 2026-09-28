"""Executable identity without loading or executing the inspected image."""

import hashlib
import struct
import time
from pathlib import Path

# A settled file's hash is reused while its size, write time, creation time
# and file id are unchanged, for at most CACHE_SECONDS. A merchant thread
# re-hashed the 19 MB client twice a second (13 ms of CPU each, 2026-09-28).
# Files written within SETTLED_SECONDS are always hashed: a same-size rewrite
# inside one clock tick keeps its write time.
CACHE_SECONDS = 60
SETTLED_SECONDS = 2
_cache = {}


def _signature(stat):
    return (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino, stat.st_dev)


def fingerprint(path: Path) -> dict:
    """Hash and inspect the same file handle; reject malformed PE images."""
    path = Path(path)
    key = str(path)
    cached = _cache.get(key)
    if cached is not None:
        signature, stored_at, result = cached
        try:
            unchanged = _signature(path.stat()) == signature
        except OSError:
            unchanged = False
        if unchanged and time.monotonic() - stored_at < CACHE_SECONDS:
            return dict(result)
    result, before = _inspect(path)
    if time.time() - before.st_mtime >= SETTLED_SECONDS:
        _cache[key] = (_signature(before), time.monotonic(), dict(result))
    else:
        _cache.pop(key, None)
    return result


def _inspect(path):
    with path.open("rb") as stream:
        before = path.stat()
        if stream.read(2) != b"MZ":
            raise ValueError("Executable does not have a DOS header")
        stream.seek(0x3C)
        offset_bytes = stream.read(4)
        if len(offset_bytes) != 4:
            raise ValueError("Truncated DOS header")
        offset = struct.unpack("<I", offset_bytes)[0]
        if offset < 0x40 or offset > before.st_size - 6:
            raise ValueError("Invalid PE header offset")
        stream.seek(offset)
        if stream.read(4) != b"PE\0\0":
            raise ValueError("Invalid PE signature")
        machine = struct.unpack("<H", stream.read(2))[0]
        architecture = {0x014C: "x86", 0x8664: "x64", 0xAA64: "arm64"}.get(machine)
        if architecture is None:
            raise ValueError(f"Unsupported PE architecture: 0x{machine:04x}")
        stream.seek(0)
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
        after = path.stat()
        if (before.st_size, before.st_mtime_ns, before.st_ino) != (
            after.st_size,
            after.st_mtime_ns,
            after.st_ino,
        ):
            raise ValueError(
                "Executable changed during fingerprinting; retry diagnostics"
            )
    return {
        "path": str(path),
        "sha256": digest,
        "architecture": architecture,
        "size_bytes": before.st_size,
        "scope": "on_disk_executable_at_queried_process_path",
    }, before
