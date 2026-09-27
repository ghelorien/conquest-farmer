"""Decline another player's team invitation instead of freezing the Farmer.

Live 2026-09-27 18:35: "sir.KAMOTE¡¡ invites you to join their team." opened
the shared ###Confirm prompt; the Open Booth cleanup refused it as unknown and
every farm start and town action failed for ten minutes.

Failure modes, written before the code:
1. A team invitation blocks farming (it must be declined).
2. Anything but the exact invitation is answered (Open Booth, a trade
   confirmation, an unknown question, other button labels).
3. The press is not the invitation's No button, or it is sent without the
   hover check.
4. A decline is claimed while the invitation is still open.
"""

from contextlib import nullcontext
import struct
from types import SimpleNamespace

import pytest

from conquest.merchants import open_booth_control_1078 as control
from conquest.merchants import team_invite_1078 as invite
from conquest.merchants.reader_1078 import CLIENT_SHA256_1078

CLIENT = SimpleNamespace(expected_sha256=CLIENT_SHA256_1078)

INVITE = (
    "Join Team###Confirm",
    "sir.KAMOTE¡¡ invites you to join their team.\n\nDo you accept?",
    "Yes",
    "No",
)


@pytest.fixture
def gui(monkeypatch):
    raw = bytearray(0x250)
    struct.pack_into("<4f", raw, 0x18, 608, 387, 200, 120)
    struct.pack_into("<2f", raw, 0xE8, 800, 471)
    struct.pack_into("<f", raw, 0x114, 18)
    state = {"visible": b"\x01", "strings": list(INVITE), "hovered": []}

    def read(address, size):
        if address == 1012:
            return state["visible"]
        if address == 1256:
            return struct.pack("<Q", 9000)
        if 2000 <= address < 2000 + len(raw):
            return bytes(raw[address - 2000 : address - 2000 + size])
        raise AssertionError((address, size))

    session = SimpleNamespace(read_block=read, assert_identity=lambda: None)
    window = {
        "name": "Trade###Confirm",
        "address": 2000,
        "geometry": (608.0, 387.0, 200.0, 120.0),
        "scroll": (0.0, 0.0),
    }
    fake = SimpleNamespace(
        session=session,
        windows=lambda: [window],
        viewport_size=lambda: (1416, 876),
        assert_hovered=lambda w, label: state["hovered"].append(label),
        state=state,
    )
    monkeypatch.setattr(control, "_model", lambda _: 1000)
    text = lambda s, at, maximum: state["strings"][(at - 1000 - 0x48) // 32]
    monkeypatch.setattr(control, "string", text)
    monkeypatch.setattr(invite, "string", text)
    return fake


def test_exact_invitation_is_read_and_located_on_its_no_button(gui):
    snapshot = invite.read(gui)
    assert snapshot["title"] == "Join Team###Confirm"
    window, point = control.locate(gui, snapshot, reader=invite.read)
    assert window["name"] == "Trade###Confirm" and point == (708, 480)


@pytest.mark.parametrize(
    "strings",
    [
        control.STRINGS,  # Open Booth keeps its own cleanup
        ("Trade###Confirm", "Accept the trade?", "Yes", "No"),
        ("Join Team###Confirm", "Something else entirely", "Yes", "No"),
        ("Join Team###Confirm", INVITE[1], "Accept", "Refuse"),
    ],
)
def test_nothing_but_the_exact_invitation_is_answered(gui, strings):
    # 2
    gui.state["strings"] = list(strings)
    assert invite.read(gui) is None


def test_decline_presses_no_after_the_hover_check_and_verifies_it_closed(
    gui, monkeypatch
):
    # 1, 3, 4
    monkeypatch.setattr("conquest.merchants.memory.GuiReader.for_session", lambda s: gui)
    monkeypatch.setattr("conquest.merchants.coordination.input_scope", nullcontext)
    monkeypatch.setattr("conquest.desktop_runtime.physical_coordinates", nullcontext)
    monkeypatch.setattr(
        "conquest.merchants.driver.wait_hover_validation", lambda guard, check: guard()
    )
    clicks = []

    def click(point, before_press):
        before_press()
        clicks.append(point)
        gui.state["visible"] = b"\x00"

    layout = SimpleNamespace(assert_current=lambda revision: None)
    trade = SimpleNamespace(
        observer=SimpleNamespace(adapter=CLIENT),
        warehouse_layout=lambda: (layout, "rev"),
        warehouse_native_point=lambda point, revision: point,
        click=click,
    )
    assert invite.decline(trade) is True
    assert clicks == [(708, 480)] and gui.state["hovered"] == ["No"]
    # No invitation: nothing is pressed.
    assert invite.decline(trade) is False and len(clicks) == 1


def test_an_invitation_still_open_after_the_press_is_not_claimed(gui, monkeypatch):
    # 4
    monkeypatch.setattr("conquest.merchants.memory.GuiReader.for_session", lambda s: gui)
    monkeypatch.setattr("conquest.merchants.coordination.input_scope", nullcontext)
    monkeypatch.setattr("conquest.desktop_runtime.physical_coordinates", nullcontext)
    monkeypatch.setattr(
        "conquest.merchants.driver.wait_hover_validation", lambda guard, check: guard()
    )
    monkeypatch.setattr(invite.time, "sleep", lambda s: None)
    clock = [0.0]

    def monotonic():
        clock[0] += 0.5
        return clock[0]

    monkeypatch.setattr(invite.time, "monotonic", monotonic)
    trade = SimpleNamespace(
        observer=SimpleNamespace(adapter=CLIENT),
        warehouse_layout=lambda: (SimpleNamespace(assert_current=lambda r: None), "rev"),
        warehouse_native_point=lambda point, revision: point,
        click=lambda point, before_press: before_press(),
    )
    with pytest.raises(ValueError, match="not verified"):
        invite.decline(trade)
