"""Shared booth-window helpers and the retired booth-control check.

The enter-a-test-price-then-cancel booth calibration was pinned to client
build 1074, which is retired. verify_booth_controls now refuses every build
after its existing preconditions; stock, window, grid_control and
modal_controls remain for their other callers.
"""

import struct

from conquest.merchants.controller import identities


def stock(snapshot):
    return (
        snapshot["identity"],
        identities(snapshot["inventory"]),
        identities(snapshot["booth"]),
        {i["uid"]: i["price"] for i in snapshot["booth"]},
        snapshot["silver"],
    )


def window(snapshot, name):
    matches = [w for w in snapshot["windows"] if w["name"] == name]
    if len(matches) != 1:
        raise ValueError(f"Open exactly one {name} window for booth verification")
    return matches[0]


def grid_control(gui, snapshot, name, label, offset):
    w = window(snapshot, name)
    table = gui.table(w, label)
    cells = table["columns"]
    strides = [
        cells[i + 1]["content_x"] - cells[i]["content_x"] for i in range(len(cells) - 1)
    ]
    if strides and (min(strides) <= 0 or max(strides) - min(strides) > 0.01):
        raise ValueError("Nonuniform merchant table columns")
    width = strides[0] if strides else cells[0]["maximum"] - cells[0]["minimum"]
    x, y, sx, sy = w["geometry"]
    return {
        "window": name,
        "size": [sx, sy],
        "offset": [
            cells[0]["content_x"] - x + offset[0],
            table["outer"][1] - y + w["scroll"][1] + offset[1],
        ],
        "columns": len(cells),
        "stride": [width, table["row_height"]],
        "table": label,
        "cell_offset": list(offset),
    }, table


def modal_controls(session, snapshot):
    w = window(snapshot, "Add Item to Booth")
    raw = session.read_block(w["address"], 0x250)
    x, y, width, height = struct.unpack_from("<4f", raw, 0x18)
    # Last item is Cancel, after the 120-pixel Confirm button and 8-pixel gap.
    # Read CursorPosPrevLine and PrevLineSize from this window's own layout.
    end_x, button_y = struct.unpack_from("<2f", raw, 0xE8)
    button_height = struct.unpack_from("<f", raw, 0x114)[0]
    start_x, start_y = struct.unpack_from("<2f", raw, 0xF0)
    if (
        [x, y, width, height] != list(w["geometry"])
        or [width, height] != [264.0, 92.0]
        or button_height != 18
        or end_x != x + 256
        or button_y != y + 66
        or [start_x, start_y] != [x + 8, y + 26]
        or session.read_block(w["address"] + 0x18, 16) != raw[0x18:0x28]
    ):
        raise ValueError(
            "Price dialog layout differs from the verified client renderer"
        )

    def spec(px, py):
        return {
            "window": w["name"],
            "size": [width, height],
            "offset": [px - x, py - y],
        }

    return {
        "price_field": spec(start_x + 64, button_y - 13),
        "confirm_listing": spec(end_x - 188, button_y + 9),
        "cancel_listing": spec(end_x - 60, button_y + 9),
    }


def verify_booth_controls(driver, journal, check):
    """Caller holds the character's observer lock and foreground lease."""
    check()
    before = driver.read()
    if (
        before.get("trade")
        or before.get("request")
        or not before["booth_open"]
        or journal.pending(driver.observer.character)
    ):
        raise ValueError(
            "Booth verification needs an open own booth and no pending trade/transaction"
        )
    # No live build has a qualified booth-control calibration: the 1074
    # renderer it was pinned to is retired, and 1078 was always refused here.
    # Fail closed before any input or qualification write.
    raise ValueError("Unqualified client fingerprint")
