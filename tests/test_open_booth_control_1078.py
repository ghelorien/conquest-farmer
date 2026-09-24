import struct
from types import SimpleNamespace

import pytest

from conquest.merchants import open_booth_control_1078 as control


@pytest.fixture
def gui(monkeypatch):
    raw = bytearray(0x250)
    struct.pack_into("<4f", raw, 0x18, 608, 387, 200, 100)
    struct.pack_into("<2f", raw, 0xE8, 800, 451)
    struct.pack_into("<f", raw, 0x114, 18)
    state = {"visible": b"\x01", "strings": list(control.STRINGS), "callback": 9000}

    def read(address, size):
        if address == 1012:
            return state["visible"]
        if address == 1256:
            return struct.pack("<Q", state["callback"])
        if 2000 <= address < 2000 + len(raw):
            return bytes(raw[address - 2000 : address - 2000 + size])
        raise AssertionError((address, size))

    session = SimpleNamespace(read_block=read, assert_identity=lambda: None)
    window = {
        "name": "Trade###Confirm",
        "address": 2000,
        "geometry": (608.0, 387.0, 200.0, 100.0),
        "scroll": (0.0, 0.0),
    }
    result = SimpleNamespace(
        session=session,
        windows=lambda: [window],
        viewport_size=lambda: (1416, 876),
        state=state,
        raw=raw,
    )
    monkeypatch.setattr(control, "_model", lambda _: 1000)
    monkeypatch.setattr(
        control,
        "string",
        lambda s, at, maximum: state["strings"][(at - 1000 - 0x48) // 32],
    )
    return result


def test_exact_no_button_uses_shared_window_identity(gui):
    snapshot = control.read(gui)
    window, point = control.locate(gui, snapshot)
    assert snapshot["negative_label"] == "No"
    assert window["name"] == "Trade###Confirm"
    assert point == (708, 460)


@pytest.mark.parametrize(
    "index,value",
    [(0, "Trade###Confirm"), (1, "Other question"), (2, "Accept"), (3, "Cancel")],
)
def test_unknown_or_trade_confirmation_never_authorizes_no(gui, index, value):
    gui.state["strings"][index] = value
    with pytest.raises(ValueError, match="not the exact"):
        control.read(gui)


def test_closed_model_is_not_an_input_target(gui):
    gui.state["visible"] = b"\x00"
    assert control.read(gui) is None
    with pytest.raises(ValueError, match="saved instance"):
        control.locate(gui, None)


def test_replaced_callback_is_a_different_confirmation_instance(gui):
    snapshot = control.read(gui)
    gui.state["callback"] += 8
    with pytest.raises(ValueError, match="saved instance"):
        control.locate(gui, snapshot)


def test_invalid_button_geometry_fails_closed(gui):
    snapshot = control.read(gui)
    struct.pack_into("<f", gui.raw, 0xEC, float("nan"))
    with pytest.raises(ValueError, match="layout changed"):
        control.locate(gui, snapshot)


def test_callback_disappearing_during_read_fails_closed(gui):
    original = gui.session.read_block
    count = 0

    def changing(address, size):
        nonlocal count
        if address == 1256:
            count += 1
            if count > 1:
                return bytes(8)
        return original(address, size)

    gui.session.read_block = changing
    with pytest.raises(ValueError, match="changed during observation"):
        control.read(gui)
