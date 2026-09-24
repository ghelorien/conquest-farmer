"""Exact-1078 native Revive semantics and live widget identity, without pixels.

Renderer/gate evidence was read from the fingerprinted running client. This
qualifies control identity, not a successful server-side revival. Recovery
still requires fresh living observations after input.
"""

import hashlib
import math
import struct

from conquest.memory_build_layout import CLIENT_SHA256_1078

RENDER_RVA = 0x9C289
RENDER_SIZE = 587
RENDER_SHA256 = "974522bdeb0e7f62cfe89cec3cab9169adaaed724df989763648f3566a3ac303"


def require_semantics(session):
    if session.expected_sha256 != CLIENT_SHA256_1078:
        raise ValueError("Native Revive semantics belong to exact client 1078")
    session.assert_identity()
    modules = [m for m in session.modules if m["name"].casefold() == "imconquer.exe"]
    if len(modules) != 1:
        raise ValueError("Revive client module is ambiguous")
    base = modules[0]["base"]
    read = session.read_block
    # Includes popup layout, disabled-state branch, ReviveButton identity and
    # the selected button's call to the native player revival method.
    if (
        hashlib.sha256(read(base + RENDER_RVA, RENDER_SIZE)).hexdigest()
        != RENDER_SHA256
        or read(base + 0x5D6720, 8) != struct.pack("<Q", 0x400)
        or read(base + 0x5F0AC8, 4) != struct.pack("<f", 50.0)
        or read(base + 0x5E0420, 14) != b"##SkillsPopup\0"
        or read(base + 0x5E0440, 13) != b"ReviveButton\0"
    ):
        raise ValueError("Native Revive renderer or enable semantics changed")
    session.assert_identity()


def point(session, size, *, require_hover=False):
    from conquest.memory_shop import MemoryGui
    from conquest.merchants.memory import GuiReader
    from conquest.viewport import validate_size

    size = validate_size(size)
    require_semantics(session)
    gui = MemoryGui.for_session(session)
    control = gui.read("##Control")
    popup = gui.read("##SkillsPopup")
    center = (
        popup.position[0] + popup.size[0] / 2,
        popup.position[1] + popup.size[1] / 2,
    )
    if (
        control.size != (930.0, 102.0)
        or control.scroll != (0.0, 0.0)
        or popup.size != (56.0, 56.0)
        or popup.scroll != (0.0, 0.0)
        or not all(math.isfinite(v) for v in center)
        or abs(center[0] - size[0] / 2) > 1
        or abs(control.position[0] + 465.0 - center[0]) > 1
        or abs(control.position[1] + 102.0 - size[1]) > 1
        or abs(center[1] - (control.position[1] - 50.0)) > 1
    ):
        raise ValueError("Native Revive popup geometry is not qualified")
    if require_hover:
        # Called only after ordinary mouse movement, immediately before down.
        # A same-position XP/other button never satisfies this label identity.
        GuiReader.for_session(session).assert_hovered(
            {"address": popup.address}, "ReviveButton"
        )
    if gui.read("##SkillsPopup") != popup or gui.read("##Control") != control:
        raise ValueError("Native Revive popup changed before input")
    session.assert_identity()
    return tuple(round(v) for v in center)
