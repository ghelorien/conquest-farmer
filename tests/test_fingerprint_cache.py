"""An unchanged executable is hashed once, not on every discovery pass.

Live 2026-09-28 (Suicide's app, thread_sampler): the merchant thread of a
merchant that does not run on the PC SHA-256'd the 19 MB ImConquer.exe about
twice a second (refresh_build_presence plus MemorySession.__enter__), 13 ms of
CPU each, the file read and hash handing the GIL to other threads throughout.

Failure modes, written before the change:
1. A settled file (same size, write time and file id) is hashed again.
2. A file written in the last two seconds is served from the cache, so a
   same-size rewrite within one clock tick keeps the old hash.
3. A changed file keeps its old hash.
4. A cached hash outlives a minute.
5. A caller mutating its result changes what the next caller gets.
6. A malformed file is cached instead of raising every time.
"""

import hashlib
import os
import struct
import time

import pytest

from conquest import identity
from conquest.identity import fingerprint


def image(machine=0x8664, size=256):
    data = bytearray(size)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, 0x80)
    data[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<H", data, 0x84, machine)
    return bytes(data)


def settle(path, age=3600):
    old = time.time() - age
    os.utime(path, (old, old))


@pytest.fixture
def hashes(monkeypatch):
    calls = []
    real = hashlib.file_digest

    def counting(stream, digest):
        calls.append(stream.name)
        return real(stream, digest)

    monkeypatch.setattr(identity.hashlib, "file_digest", counting)
    return calls


def test_a_settled_executable_is_hashed_once(tmp_path, hashes):
    # 1
    path = tmp_path / "ImConquer.exe"
    path.write_bytes(image())
    settle(path)
    first = fingerprint(path)
    assert fingerprint(path) == first
    assert len(hashes) == 1


def test_a_fresh_file_is_hashed_every_time(tmp_path, hashes):
    # 2
    path = tmp_path / "ImConquer.exe"
    path.write_bytes(image())
    assert fingerprint(path)["architecture"] == "x64"
    path.write_bytes(image(machine=0x014C))
    assert fingerprint(path)["architecture"] == "x86"
    assert len(hashes) == 2


def test_a_changed_file_is_hashed_again(tmp_path, hashes):
    # 3
    path = tmp_path / "ImConquer.exe"
    path.write_bytes(image())
    settle(path, age=7200)
    first = fingerprint(path)
    path.write_bytes(image(machine=0x014C))  # same size, new content
    settle(path, age=3600)
    second = fingerprint(path)
    assert (first["architecture"], second["architecture"]) == ("x64", "x86")
    assert first["sha256"] != second["sha256"]
    with path.open("ab") as stream:
        stream.write(b"update")
    settle(path, age=1800)
    assert fingerprint(path)["size_bytes"] == 262
    assert len(hashes) == 3


def test_the_cache_expires_after_a_minute(tmp_path, hashes, monkeypatch):
    # 4
    path = tmp_path / "ImConquer.exe"
    path.write_bytes(image())
    settle(path)
    now = [1000.0]
    monkeypatch.setattr(identity.time, "monotonic", lambda: now[0])
    fingerprint(path)
    now[0] += identity.CACHE_SECONDS - 1
    fingerprint(path)
    assert len(hashes) == 1
    now[0] += 2
    fingerprint(path)
    assert len(hashes) == 2


def test_results_are_independent_copies(tmp_path, hashes):
    # 5
    path = tmp_path / "ImConquer.exe"
    path.write_bytes(image())
    settle(path)
    fingerprint(path)["sha256"] = "tampered"
    assert fingerprint(path)["sha256"] == hashlib.sha256(image()).hexdigest()


def test_a_malformed_file_raises_every_time(tmp_path, hashes):
    # 6
    path = tmp_path / "bad.exe"
    path.write_bytes(b"MZ" + b"\xff" * 100)
    settle(path)
    for _ in range(2):
        with pytest.raises(ValueError):
            fingerprint(path)
