"""Read-only identification of the native 1078 Open Booth negative button.

Loaded Farmer code (PID 18532 / creation 134345064188672222) proves the
shared confirmation renderer calls its callback with false for model +A8.
The observed Open Booth strings name that control ``No``, not ``Cancel``.
No input authority or transaction completion is supplied by this module.
"""

import hashlib
import math
import struct

from conquest.merchants.memory import string
from conquest.merchants.trade_reader_1078 import assert_trade_code


TITLE = "Open Booth###Confirm"
LABEL = "No"
STRINGS = (TITLE, "Start Vending", "Yes", LABEL)
MODEL_VTABLE = 0x5E0148
# 97175 invokes the second button; 9717E zeroes EDX before 978A0. The
# callback consumes this bool at 9791C..9792E, then closes at slot +70.
CALLBACK = (
    0x978A0,
    256,
    "bc93fe0cafb72c623fa2a2610d2d886eaf97767ff232fde63abf9bd6251ea365",
)


def _model(gui):
    session = gui.session
    base = assert_trade_code(session)
    rva, size, digest = CALLBACK
    if hashlib.sha256(session.read_block(base + rva, size)).hexdigest() != digest:
        raise ValueError("1078 confirmation negative callback changed")
    if (
        struct.unpack("<Q", session.read_block(base + MODEL_VTABLE + 0x10, 8))[0]
        != base + 0x96FE0
    ):
        raise ValueError("1078 confirmation renderer dispatch changed")
    return gui.model(15, MODEL_VTABLE)


def read(gui):
    """Return an exact stable Open Booth model, None when closed, else reject."""
    session = gui.session
    model = _model(gui)
    visible = session.read_block(model + 12, 1)
    if visible not in (b"\x00", b"\x01"):
        raise ValueError("1078 confirmation visibility is invalid")
    if visible == b"\x00":
        session.assert_identity()
        return None
    values = tuple(
        string(session, model + offset, 256) for offset in (0x48, 0x68, 0x88, 0xA8)
    )
    if values != STRINGS:
        raise ValueError("1078 confirmation is not the exact Open Booth prompt")
    callback = session.read_block(model + 0x100, 8)
    if (
        not struct.unpack("<Q", callback)[0]
        or session.read_block(model + 12, 1) != visible
        or tuple(
            string(session, model + offset, 256) for offset in (0x48, 0x68, 0x88, 0xA8)
        )
        != values
        or session.read_block(model + 0x100, 8) != callback
    ):
        raise ValueError("1078 Open Booth confirmation changed during observation")
    session.assert_identity()
    return {
        "model_address": model,
        "title": values[0],
        "message": values[1],
        "positive_label": values[2],
        "negative_label": values[3],
        "callback_address": struct.unpack("<Q", callback)[0],
    }


def locate(gui, snapshot):
    """Return (window, logical point); caller must verify hover ID for ``No``."""
    if read(gui) != snapshot or snapshot is None:
        raise ValueError("1078 Open Booth confirmation differs from the saved instance")
    # ImGui reuses the ###Confirm identity; its stored window name can still
    # be Trade###Confirm. Only the model strings above identify this prompt.
    windows = [w for w in gui.windows() if w["name"].endswith("###Confirm")]
    if len(windows) != 1:
        raise ValueError("1078 Open Booth confirmation window is ambiguous")
    window = windows[0]
    session = gui.session
    raw = session.read_block(window["address"], 0x250)
    geometry = struct.unpack_from("<4f", raw, 0x18)
    if tuple(window["geometry"]) != geometry or not all(
        math.isfinite(v) for v in geometry
    ):
        raise ValueError("1078 Open Booth confirmation geometry changed")
    x, y, width, height = geometry
    end_x, button_y = struct.unpack_from("<2f", raw, 0xE8)
    line = struct.unpack_from("<f", raw, 0x114)[0]
    if (
        width != 200
        or not 100 <= height <= 400
        or line != 18
        or end_x != x + width - 8
        or not all(math.isfinite(v) for v in (end_x, button_y, line))
    ):
        raise ValueError("1078 Open Booth button layout changed")
    # Renderer emits the +88 then +A8 full-width buttons on separate lines.
    # The window's final cursor previous-line Y belongs to the latter, No.
    point = (round(x + width / 2), round(button_y + line / 2))
    viewport = gui.viewport_size()
    if (
        not x < point[0] < x + width
        or not y < point[1] < y + height
        or not all(0 < v < limit for v, limit in zip(point, viewport))
    ):
        raise ValueError("1078 Open Booth negative button is clipped")
    if (
        read(gui) != snapshot
        or [w for w in gui.windows() if w["name"].endswith("###Confirm")] != windows
        or session.read_block(window["address"] + 0x18, 16) != raw[0x18:0x28]
        or session.read_block(window["address"] + 0xE8, 8) != raw[0xE8:0xF0]
        or session.read_block(window["address"] + 0x114, 4) != raw[0x114:0x118]
    ):
        raise ValueError("1078 Open Booth target changed during observation")
    session.assert_identity()
    return window, point
