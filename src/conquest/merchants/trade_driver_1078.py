"""Memory-only 1078 trade control locations; this module never sends input.

The caller must retain exact-build capability qualification, transaction
journaling, foreground ownership and manual/Stop checks around every action.
"""

import math
import struct
import hashlib
import json

from conquest.merchants.trade_reader_1078 import assert_trade_code
from conquest.merchants.delivery_trade_controls import nested_table, cell
from conquest.merchants.qualification import window


MODES = ("native_trade_drop", "native_trade_confirm", "native_trade_request")
TRADE_MODE_RVA = 0x6B9A70
TRADE_MODE_VALUE = 19
TARGET_PINS = {
    0x9BA41: "488d0d38f25300",
    0x9BA48: "e8138dfaff84c0744f",
    0x9BA91: "e83ac8ffffc780b006000013000000",
    0x98345: "488d0574106200",
}


def validate_receipt(state):
    """Only a complete bilateral native transfer can qualify trade input."""
    from conquest.memory_build_layout import CLIENT_SHA256_1078
    from conquest.merchants.delivery import reconcile, exact_items

    if not isinstance(state, dict):
        raise ValueError("1078 trade receipt is unavailable")
    intent = state.get("intent") or {}
    farmer = state.get("farmer_after") or {}
    merchant = state.get("merchant_after") or {}
    before_farmer = intent.get("farmer") or {}
    before_merchant = intent.get("merchant") or {}
    if (
        state.get("phase") != "delivery_verified"
        or not state.get("verified_at")
        or not intent.get("items")
        or not state.get("accept_point")
        or not state.get("confirm_point")
        or any(
            s.get("client_sha256") != CLIENT_SHA256_1078
            or s.get("reader_build") != "1078-canonical-trade"
            for s in (farmer, merchant, before_farmer, before_merchant)
        )
        or farmer.get("identity") != before_farmer.get("identity")
        or merchant.get("identity") != before_merchant.get("identity")
        or state.get("recipient", {}).get("uid") != before_merchant.get("character_uid")
        or state.get("recipient", {}).get("name") != before_merchant.get("character")
        or set(state.get("offered_uids", [])) != set(exact_items(intent["items"]))
        or not reconcile(
            intent,
            farmer,
            merchant,
            now=max(farmer.get("timestamp", 0), merchant.get("timestamp", 0)),
        )
    ):
        raise ValueError(
            "1078 trade qualification lacks a verified bilateral native receipt"
        )
    return state


def receipt_digest(receipt):
    return hashlib.sha256(
        json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def validate_qualification(profile, capability, character):
    receipt = profile.get("trade_receipt_1078")
    if profile.get("trade_receipt_1078_sha256") != receipt_digest(receipt):
        raise ValueError("1078 trade qualification receipt digest differs")
    validate_receipt(receipt)
    role = "farmer" if capability == "farmer_delivery" else "merchant"
    if receipt["intent"][role]["character"] != character:
        raise ValueError("1078 trade receipt belongs to another participant")
    if profile.get("native_trade_layout_revision") != 1:
        raise ValueError("1078 trade requires native window-owned controls")
    if role == "farmer" and profile.get("target_mode") != {
        "rva": TRADE_MODE_RVA,
        "value": TRADE_MODE_VALUE,
    }:
        raise ValueError("1078 trade targeting qualification has a different accessor")


def targeting_state(session):
    base = assert_trade_code(session)
    read = getattr(session, "read_block", None) or session.read
    for rva, hexdata in TARGET_PINS.items():
        expected = bytes.fromhex(hexdata)
        if read(base + rva, len(expected)) != expected:
            raise ValueError("1078 native Trade target-mode handler changed")
    getter = bytes.fromhex(TARGET_PINS[0x98345])
    controller = 0x98345 + len(getter) + struct.unpack_from("<i", getter, 3)[0]
    if controller + 0x6B0 != TRADE_MODE_RVA:
        raise ValueError("1078 target-mode accessor disagrees with its pinned field")
    mode = struct.unpack("<I", read(base + TRADE_MODE_RVA, 4))[0]
    session.assert_identity()
    return {
        "rva": TRADE_MODE_RVA,
        "value": TRADE_MODE_VALUE,
        "current": mode,
        "targeting_trade": mode == TRADE_MODE_VALUE,
    }


def trade_button(gui):
    from conquest.merchants.trade_hud_1078 import trade_button as locate

    targeting_state(gui.session)
    require_recipient_scale(gui.session)
    return locate(gui)


def require_recipient_scale(session):
    """The observed actor draw pair is a target point only at native scale one."""
    from conquest.merchants.booth_target_1078 import _PROJECTION_CODE

    base = assert_trade_code(session)
    read = getattr(session, "read_block", None) or session.read
    # Pin both the camera getter and the native hit-test's zoom/percent fields.
    for rva in (0x982B5, 0x1ABF45):
        expected = bytes.fromhex(_PROJECTION_CODE[rva])
        if read(base + rva, len(expected)) != expected:
            raise ValueError("1078 recipient projection accessor changed")
    address = base + 0x6B9B40 + 0x26C
    raw = read(address, 16)
    zoom, width, height, percent = struct.unpack("<4i", raw)
    if (
        zoom != 256
        or percent != 100
        or not 640 <= width <= 7680
        or not 480 <= height <= 4320
        or read(address, 16) != raw
    ):
        raise ValueError(
            "1078 recipient targeting requires unchanged native 100 percent zoom"
        )
    session.assert_identity()
    return {"zoom": zoom, "percent": percent, "size": [width, height]}


def _request_control(driver, snapshot):
    from conquest.merchants.memory import string

    gui = driver.memory.gui
    s = gui.session
    model = gui.model(15, 0x5E0148)
    request = snapshot.get("request")
    if (
        not isinstance(request, dict)
        or snapshot.get("trade") is not None
        or type(request.get("participant_uid")) is not int
        or request["participant_uid"] <= 0
        or not request.get("participant")
        or request.get("message")
        != f"{request['participant']} wishes to trade with you."
        or s.read_block(model + 12, 1) != b"\x01"
        or [string(s, model + o, 256) for o in (0x48, 0x68, 0x88, 0xA8)]
        != ["Trade###Confirm", request["message"], "Accept", "Cancel"]
    ):
        raise ValueError("1078 acceptance is not the exact incoming Trade request")
    matches = [
        w for w in snapshot["windows"] if str(w.get("name", "")).endswith("###Confirm")
    ]
    if len(matches) != 1:
        raise ValueError("1078 incoming confirmation is absent or ambiguous")
    w = {**matches[0], "model_address": model}
    raw = s.read_block(w["address"], 0x250)
    geometry = w.get("geometry")
    if (
        not isinstance(geometry, (tuple, list))
        or len(geometry) != 4
        or not all(type(v) in (int, float) and math.isfinite(v) for v in geometry)
        or tuple(geometry) != struct.unpack_from("<4f", raw, 0x18)
    ):
        raise ValueError("1078 request geometry differs from the native window")
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
        raise ValueError("1078 request button layout changed")
    point = (round(x + width / 2), round(button_y - 22 + line / 2))
    if not x < point[0] < x + width or not y < point[1] < y + height:
        raise ValueError("1078 request acceptance is clipped")
    return w, point, "Accept", None


def locate(driver, snapshot, mode):
    gui = driver.memory.gui
    assert_trade_code(gui.session)
    if mode == "native_trade_request":
        return _request_control(driver, snapshot)
    trade = snapshot.get("trade")
    if not trade:
        raise ValueError("1078 native Trade window is not open")
    w = window(snapshot, "Trade##TradeWindow")
    outer = gui.table(w, "##TradeWindowGrid")
    if len(outer["columns"]) != 3:
        raise ValueError("1078 trade layout changed")
    if mode == "native_trade_drop":
        if trade["accepted"] or trade["other_accepted"]:
            raise ValueError(
                "1078 trade is already confirmed; no item placement allowed"
            )
        table = nested_table(gui, w, "##TradeWindowGrid1", outer["id"])
        if len(table["columns"]) != 5 or table["row_height"] != 44:
            raise ValueError("1078 trade item grid changed")
        count = len(trade["own_items"])
        if count >= 20:
            raise ValueError("1078 trade grid is full")
        return w, cell(table, count), None, None
    if mode == "native_trade_confirm":
        if trade["accepted"]:
            raise ValueError("1078 own trade was already confirmed")
        bar = nested_table(gui, w, "##MyTradeBar", outer["id"])
        if len(bar["columns"]) != 2 or bar["row_height"] != 18:
            raise ValueError("1078 trade confirmation layout changed")
        l, t, r, b = bar["outer"]
        point = (round((l + r) / 2), round(b + 13))
        left, top, right, bottom = outer["clip"]
        if not left < point[0] < right or not top < point[1] < bottom:
            raise ValueError("1078 trade confirmation is clipped")
        return w, point, "Accept Trade", [outer["id"]]
    raise ValueError("Unknown native 1078 trade control")


def point(driver, snapshot, mode):
    _, point, _, _ = locate(driver, snapshot, mode)
    gui = driver.memory.gui.viewport_size()
    size = driver.target.snapshot()["client_size"]
    if not all(0 < v < limit for v, limit in zip(point, gui)):
        raise ValueError("1078 native trade point is outside the viewport")
    return tuple(
        round(v * actual / logical) for v, actual, logical in zip(point, size, gui)
    )


def hover(driver, snapshot, mode):
    w, _, label, seeds = locate(driver, snapshot, mode)
    if not label:
        raise ValueError("1078 trade drop target is not a button")
    driver.memory.gui.assert_hovered(w, label, seeds=seeds)


def native_foreground(driver, identity, *, activate=False):
    """Bind foreground ownership to the exact native HWND inside its lease."""
    from conquest.focus_recovery import activate_client

    observer = driver.observer
    target = driver.target
    observer.adapter.assert_identity()
    before = target.snapshot()
    if (
        observer.adapter.identity != identity
        or before["pid"] != identity["pid"]
        or before["root_hwnd"] != target.hwnd
    ):
        raise ValueError("1078 trade requires its exact native client window")
    if activate and not activate_client(target.hwnd, identity):
        raise ValueError("1078 native trade client did not acquire foreground")
    after = target.snapshot()
    observer.adapter.assert_identity()
    if (
        after["pid"] != identity["pid"]
        or after["root_hwnd"] != target.hwnd
        or after["foreground"] != target.hwnd
        or after["minimized"]
    ):
        raise ValueError("1078 native trade client lost foreground ownership")
    return True
