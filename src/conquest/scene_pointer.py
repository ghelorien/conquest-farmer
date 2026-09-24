"""Wait for the native actor hit-test pointer, without pixels or extra input."""

import struct
import time

from conquest.capture import CaptureUnavailable
from conquest.memory_build_layout import CLIENT_SHA256_1074, CLIENT_SHA256_1078


# Each address is tied to the exact executable fingerprint.  The 1078 fields
# were traced from WM_MOUSEMOVE storage to Actor::hit_test and cross-checked
# against the native client cursor in the live, unpacked module.
_SCENE_POINTERS = {
    CLIENT_SHA256_1074: (
        0x6985A8,
        0x6985B0,
        (
            (0x19FCB1, bytes.fromhex("8b2df5884f00")),
            (0x19FCBC, bytes.fromhex("8b35e6884f00")),
        ),
    ),
    CLIENT_SHA256_1078: (
        0x6B8D20,
        0x6B8D28,
        (
            (0x1ABF11, bytes.fromhex("8b2d0dce5000")),
            (0x1ABF1C, bytes.fromhex("8b35fecd5000")),
            (0xC1E88, bytes.fromhex("8905926e5f00")),
            (0xC1E96, bytes.fromhex("8905886e5f00")),
        ),
    ),
}


def wait_scene_pointer(
    session, point, check, *, timeout=0.35, clock=time.monotonic, sleep=time.sleep
):
    if (
        len(point) != 2
        or any(type(v) is not int for v in point)
        or not 0 < timeout <= 0.5
    ):
        raise ValueError("Invalid native scene pointer check")
    try:
        pointer_rva, minimum_size, pins = _SCENE_POINTERS[session.expected_sha256]
    except KeyError as error:
        raise ValueError("Scene pointer client fingerprint is not qualified") from error
    modules = [m for m in session.modules if m["name"].casefold() == "imconquer.exe"]
    if len(modules) != 1 or modules[0]["size"] < minimum_size:
        raise ValueError("Scene pointer module is absent or invalid")
    base = modules[0]["base"]
    # Actor::hit_test loads Y, then X, from these native input fields.
    for rva, expected in pins:
        if session.read_block(base + rva, len(expected)) != expected:
            raise ValueError("Scene pointer accessor differs from the pinned client")
    deadline = clock() + timeout
    while True:
        check()
        session.assert_identity()
        current = struct.unpack("<2i", session.read_block(base + pointer_rva, 8))
        if clock() >= deadline:
            raise CaptureUnavailable(
                "Game has not confirmed the scene pointer; no button pressed"
            )
        if all(abs(a - b) <= 1 for a, b in zip(current, point)):
            return
        sleep(0.01)
