"""Native selected-actor proof for one supervised owned-booth panel click.

This does not qualify the click dispatcher or promote the projection candidate.
The effective manager selection includes the native override path.
"""

import hashlib
import struct

from conquest.memory_build_layout import CLIENT_SHA256_1078
from conquest.merchants.memory import HoverNotReady

PINS = (
    (
        0x19F3D0,
        1974,
        "e395400a19ba0d93d910de45c7d55d99b41dacd6cf31fda6c9a83ed5d8768e0d",
    ),
    (0x19B970, 32, "48302af00a1d902b6fe2294eac865d8bee56dc77b0a536efb76eee7d26e65c0e"),
    (0x77120, 128, "2a216cfc894e6a32dacd4011e2eae6ebef2084480a73cf9eef3b9fcdad61296f"),
    (0x77080, 56, "3908e3bcca989cfd808f0f88535ba89e48d96780e7df6f8c888a66b4562e216a"),
)


def qualify(session):
    if session.expected_sha256 != CLIENT_SHA256_1078:
        raise ValueError("Owned booth hover requires exact 1078")
    modules = [m for m in session.modules if m["name"].lower() == "imconquer.exe"]
    if len(modules) != 1 or modules[0]["size"] < 0x6C4F10:
        raise ValueError("Owned booth selection module changed")
    base = modules[0]["base"]
    for rva, size, digest in PINS:
        if hashlib.sha256(session.read(base + rva, size)).hexdigest() != digest:
            raise ValueError("Native owned booth selection code changed")
    if struct.unpack("<Q", session.read(base + 0x5EB480, 8))[0] != base + 0x1ABEE0:
        raise ValueError("Owned booth native hit-test slot changed")
    session.assert_identity()
    return base


def assert_selected(session, gui, base, candidate):
    """Read the final effective hover actor, never infer it from pointer X/Y."""
    actor = int(candidate["actor_address"], 16)
    root = base + 0x6C4F08
    pointer = session.read(root, 8)
    manager = struct.unpack("<Q", pointer)[0]
    if not 0x10000 <= manager <= 0x7FFFFFFFFFFF:
        raise ValueError("Native scene selection manager unavailable")
    vtable = session.read(manager, 8)
    if struct.unpack("<Q", vtable)[0] != base + 0x5EAB40:
        raise ValueError("Native scene selection manager changed")
    selected = session.read(manager + 0xD8, 8)
    # The actor collection stores the primary object; the native actor API
    # uses its +0x10 secondary base (whose UID is at +0x68).
    if struct.unpack("<Q", selected)[0] != actor + 0x10:
        raise HoverNotReady("Native scene selection is not the exact owned booth")
    uid = session.read(actor + 0x78, 4)
    secondary = session.read(actor + 0x10, 8)
    if (
        struct.unpack("<I", uid)[0] != candidate["owned_booth_uid"]
        or struct.unpack("<Q", secondary)[0] != base + 0x5EB2D0
    ):
        raise ValueError("Selected owned booth actor changed")
    context_raw = session.read(base + gui.context_rva, 8)
    context = struct.unpack("<Q", context_raw)[0]
    # The GUI must neither capture this scene click nor own an active widget.
    capture = session.read(context + 0xD0, 1)
    active = session.read(context + 0x3F04, 4)
    if capture != b"\0" or active != b"\0" * 4:
        raise HoverNotReady("Native GUI captures the owned booth scene point")
    for address, raw in (
        (root, pointer),
        (manager, vtable),
        (manager + 0xD8, selected),
        (actor + 0x78, uid),
        (actor + 0x10, secondary),
        (base + gui.context_rva, context_raw),
        (context + 0xD0, capture),
        (context + 0x3F04, active),
    ):
        if session.read(address, len(raw)) != raw:
            raise HoverNotReady("Native booth selection changed during observation")
    session.assert_identity()
    return {
        "actor_address": hex(actor),
        "selected_actor": hex(actor + 0x10),
        "owned_booth_uid": candidate["owned_booth_uid"],
        "manager": hex(manager),
    }
