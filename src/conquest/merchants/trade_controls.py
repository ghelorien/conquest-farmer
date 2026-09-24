"""Pinned native Trade HUD and targeting-mode observations; no input."""

import struct

from conquest.memory_life import CLIENT_SHA256


TRADE_MODE_RVA = 0x699290
TRADE_MODE_VALUE = 19
SIGNATURES = {
    # Trade label -> button -> targeting-controller getter -> mode assignment.
    0x9A771: bytes.fromhex("488d0df8515200"),
    0x9A778: bytes.fromhex("e89394faff84c0744f"),
    0x9A7C1: bytes.fromhex("e81ac8ffffc780b006000013000000"),
    # The getter returns the static controller at RVA 0x698be0.
    0x97055: bytes.fromhex("488d05841b6000"),
}


def targeting_state(session):
    """Reject a different renderer before interpreting its targeting field."""
    if session.expected_sha256 != CLIENT_SHA256:
        raise ValueError("Trade targeting client fingerprint is not qualified")
    modules = [m for m in session.modules if m["name"].casefold() == "imconquer.exe"]
    if len(modules) != 1 or modules[0]["size"] < TRADE_MODE_RVA + 4:
        raise ValueError("Trade targeting module is absent or invalid")
    base = modules[0]["base"]
    session.assert_identity()
    for rva, expected in SIGNATURES.items():
        if session.read_block(base + rva, len(expected)) != expected:
            raise ValueError("Trade targeting renderer differs from the pinned client")
    getter = SIGNATURES[0x97055]
    controller = 0x97055 + len(getter) + struct.unpack_from("<i", getter, 3)[0]
    field, value = struct.unpack_from("<II", SIGNATURES[0x9A7C1], 7)
    if controller + field != TRADE_MODE_RVA or value != TRADE_MODE_VALUE:
        raise ValueError("Trade targeting accessor and mode disagree")
    mode = struct.unpack("<I", session.read_block(base + TRADE_MODE_RVA, 4))[0]
    session.assert_identity()
    return {
        "rva": TRADE_MODE_RVA,
        "value": TRADE_MODE_VALUE,
        "current": mode,
        "targeting_trade": mode == TRADE_MODE_VALUE,
    }


def trade_button(gui):
    """Trade is below Items in the pinned two-row, 40-pixel HUD column.

    inventory_button verifies the live table, column bounds, row height and
    frame. The caller must additionally verify the Trade hover ID before input.
    """
    from conquest.discard_loot import inventory_button

    targeting_state(gui.session)
    x, y = inventory_button(gui)
    return x, y + 20
