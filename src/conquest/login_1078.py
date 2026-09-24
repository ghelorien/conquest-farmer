"""Exact 1078 login layout, read from loaded code and native GUI metadata.

No credentials, field contents, screenshots or input are read here. The native
root accessor returns 6B93C0; its main loop renders root+78 for login and passes
the same root to the error renderer. E7BD0 emits Username/##username followed by
Password/##password (0x8020 password flags), Server, Remember Login?, Login and
the footer. Its submit branch calls E8D00 only on button/field submission with a
selected server. C0E60 renders root+6E8 and clears +708 on its final OK button.
Callers retain saved intent, exclusive input, Stop, focus and credential guards.
"""

import hashlib
import math
import struct
import zlib

from conquest.memory_build_layout import CLIENT_SHA256_1078

ROOT_RVA = 0x6B93C0
LOGIN_VTABLE_RVA = 0x5E44A8
CODE_BLOCKS = (
    (0x982D0, 132, "469655e0e31bb34cb93d6f78b815763b3ca90f9a6453d22a83080491dcdd9046"),
    (0xEB66B, 13, "26d0745a0ff5397dff9db4ee988e58a00e082d013fcf3f2c7a6ca594631727ee"),
    (0xC1140, 64, "89efa416ba0996145ee6c990694bdc6ac77bdc43475ced5ec92c697e4bfd4e2b"),
    (0xC1618, 70, "82fcaa611293f5fa9455d0d011c73a1cf03a94042ba51bc5da66a2730d5cc815"),
    (0xC0E60, 721, "5f97ab315e5a7aa5987f7e8eb49d748dc432217500bbb7782d353c845bd21693"),
    (
        0xE7BD0,
        0x1125,
        "54af5c6e0553a2a5bae1f37f5b2c5d3ee4b0646300670f250b5d77e3d165f971",
    ),
    (0x14B70, 0xD0, "678740725e0b19be15ed4c2c77767cb82a0ddc47c22588af2ef6e57531e460f3"),
)
LITERALS = (
    (0x5E4360, b"Login\0"),
    (0x5E4368, b"Password\0"),
    (0x5E4378, b"##password\0"),
    (0x5E4388, b"Username\0"),
    (0x5E4398, b"##username\0"),
    (0x5E2418, b"##ErrorModal\0"),
    (0x5DABD8, b"OK\0"),
)


def assert_code(session):
    if session.expected_sha256 != CLIENT_SHA256_1078:
        raise ValueError("1078 login reader requires its exact client build")
    session.assert_identity()
    modules = [m for m in session.modules if m["name"].lower() == "imconquer.exe"]
    if len(modules) != 1:
        raise ValueError("1078 login requires one native client module")
    base = modules[0]["base"]
    for rva, size, digest in CODE_BLOCKS:
        if hashlib.sha256(session.read_block(base + rva, size)).hexdigest() != digest:
            raise ValueError("1078 login renderer changed")
    for rva, value in LITERALS:
        if session.read_block(base + rva, len(value)) != value:
            raise ValueError("1078 login control label changed")
    if session.read_block(base + ROOT_RVA + 0x78, 8) != struct.pack(
        "<Q", base + LOGIN_VTABLE_RVA
    ) or session.read_block(base + LOGIN_VTABLE_RVA + 0x10, 8) != struct.pack(
        "<Q", base + 0xE7BD0
    ):
        raise ValueError("1078 login model differs from its native renderer")
    session.assert_identity()
    return base


def form_points(session, window):
    from conquest.memory_shop import MemoryGui
    from conquest.merchants.memory import GuiReader

    base = assert_code(session)
    if session.read_block(base + ROOT_RVA + 0x708, 1) != b"\0":
        raise ValueError("1078 login error must be resolved before credential entry")
    gui = MemoryGui.for_session(session)
    if gui.read("Login") != window:
        raise ValueError("1078 login form changed")
    raw = session.read_block(window.address + 0xE0, 0x38)
    dc = struct.unpack("<14f", raw)
    x, y = window.position
    if (
        window.name != "Login"
        or window.size != (208.0, 186.0)
        or window.scroll != (0.0, 0.0)
        or not all(math.isfinite(v) for v in (*window.position, *dc))
        or (dc[0], dc[1], dc[3], dc[4], dc[5], dc[6], dc[7], dc[13])
        != (x + 8, y + 182, y + 166, x + 8, y + 8, x + 200, y + 178, 12.0)
    ):
        raise ValueError("1078 login form layout changed")
    points = (
        (round(x + 104), round(y + 33)),
        (round(x + 104), round(y + 71)),
        (round(x + 104), round(y + 153)),
    )
    viewport = GuiReader.for_session(session).viewport_size()
    if any(
        not (0 < point[0] < viewport[0] and 0 < point[1] < viewport[1])
        for point in points
    ):
        raise ValueError("1078 login control is clipped")
    if (
        session.read_block(window.address + 0xE0, 0x38) != raw
        or gui.read("Login") != window
        or session.read_block(base + ROOT_RVA + 0x708, 1) != b"\0"
    ):
        raise ValueError("1078 login form changed during observation")
    session.assert_identity()
    return points


def assert_hovered(session, window, label):
    from conquest.merchants.memory import GuiReader

    if label not in ("##username", "##password", "Login", "OK"):
        raise ValueError("Unqualified 1078 login control")
    assert_code(session)
    GuiReader.for_session(session).assert_hovered({"address": window.address}, label)


def assert_active_field(session, window, label):
    """Prove keyboard ownership without reading either credential buffer.

    Loaded SetActiveID at 14B70 stores ECX at GUI context+3F04 and RDX
    (the input's window) at +3F20. Window+8 is the native ID seed.
    """
    from conquest.addressing import checked_address

    if label not in ("##username", "##password") or window.name != "Login":
        raise ValueError("Unqualified 1078 login text field")
    base = assert_code(session)
    read = session.read_block
    context_raw = read(base + 0x6B5EF0, 8)
    context = checked_address(struct.unpack("<Q", context_raw)[0])
    seed_raw = read(window.address + 8, 4)
    expected = zlib.crc32(label.encode(), struct.unpack("<I", seed_raw)[0])
    active_id = read(context + 0x3F04, 4)
    active_window = read(context + 0x3F20, 8)
    if (
        struct.unpack("<I", active_id)[0] != expected
        or struct.unpack("<Q", active_window)[0] != window.address
    ):
        raise ValueError("1078 login text field does not own keyboard focus")
    if (
        read(base + 0x6B5EF0, 8) != context_raw
        or read(window.address + 8, 4) != seed_raw
        or read(context + 0x3F04, 4) != active_id
        or read(context + 0x3F20, 8) != active_window
    ):
        raise ValueError("1078 login keyboard focus changed during observation")
    session.assert_identity()
