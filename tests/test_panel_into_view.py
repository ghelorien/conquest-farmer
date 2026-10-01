"""A town panel stuck past the viewport's edge is dragged back, then used.

Suicide, 2026-10-01 06:03, Phoenix Pharmacist: the client kept the Shop where
it was last dragged, at x = -35. memory_shop's strict read refused it ("GUI
window geometry is invalid"), so its opening, the sale and its close all
failed, and every restock retry stopped in town for 10 minutes.

Failure modes, written before the change:
1. The panel is never moved, or is moved somewhere still past an edge.
2. The drag grabs outside the visible title bar (or on the close button), or
   presses before the pointer is over this exact panel.
3. An unverified move is reported as done.
4. The close and open actions still give up on an off-view panel.
"""

from types import SimpleNamespace as NS

import pytest

from conquest import panel_close
from conquest import town_trade as t


def stage(monkeypatch, geometry, moves_to=None):
    window = {"name": "Shop", "address": 0xABC, "geometry": list(geometry)}
    drags = []

    def windows():
        return [window, {"name": "Inventory", "address": 0xDEF, "geometry": [766, 410, 447, 287]}]

    gui = NS(windows=windows, viewport_size=lambda: [1416, 876], session=None, base=0, context_rva=0)
    monkeypatch.setattr("conquest.merchants.memory.GuiReader.for_session", lambda session: gui)
    hovered = {"address": 0xABC}
    monkeypatch.setattr(
        "conquest.merchants.memory.unpack",
        lambda session, address, fmt: (hovered["address"],) if address == 0x3EC0 else (0,),
    )

    def drag(target, source, destination, size, *, before_press, before_release, activate):
        before_press()
        before_release()
        drags.append((source, destination, activate))
        if moves_to is not None:
            window["geometry"][:2] = moves_to

    monkeypatch.setattr("conquest.foreground.foreground_drag", drag)
    monkeypatch.setattr("conquest.viewport.size_for", lambda observer: (1416, 876))
    trade = NS(observer=NS(adapter="adapter", operations=NS(target="target")))
    return trade, drags, hovered


def test_the_phoenix_shop_at_x_minus_35_is_dragged_back_by_its_title_bar(monkeypatch):
    # 1, 2
    trade, drags, _ = stage(monkeypatch, (-35, 266, 288, 438), moves_to=[8, 266])
    assert panel_close.bring_into_view(trade, "Shop") is True
    (grab, drop, activate), = drags
    assert grab == (40, 276) and drop == (83, 276) and activate
    assert trade.input_attempted


def test_a_panel_inside_the_view_is_left_alone(monkeypatch):
    trade, drags, _ = stage(monkeypatch, (20, 266, 288, 438))
    assert panel_close.bring_into_view(trade, "Shop") is False and drags == []


def test_an_unverified_move_is_no_success(monkeypatch):
    # 3
    trade, drags, _ = stage(monkeypatch, (-35, 266, 288, 438), moves_to=None)
    with pytest.raises(ValueError, match="move was not verified"):
        panel_close.bring_into_view(trade, "Shop")


def test_no_press_while_the_pointer_is_over_another_window(monkeypatch):
    # 2
    trade, drags, hovered = stage(monkeypatch, (-35, 266, 288, 438), moves_to=[8, 266])
    hovered["address"] = 0xDEF
    monkeypatch.setattr(panel_close, "wait_hover_validation", lambda guard, check: guard())
    with pytest.raises(panel_close.HoverNotReady):
        panel_close.bring_into_view(trade, "Shop")
    assert drags == []


@pytest.mark.parametrize("action", ["close", "open"])
def test_close_and_open_drag_an_off_view_shop_back_first(monkeypatch, action):
    # 4
    state = {"moved": False}
    panel = NS(position=(8, 266), size=(288, 438))

    def read(name):
        if not state["moved"]:
            raise ValueError(t.OFF_VIEW)
        if action == "close" and state.get("closed"):
            raise ValueError("Window absent")
        return panel

    trade = t.TownTrade.__new__(t.TownTrade)
    trade.observer = NS(adapter=NS(assert_identity=lambda: None))
    trade.shop = NS(gui=NS(read=read), read=lambda vendor_id: read("Shop"))
    trade.vendor = lambda vendor_type: NS(entity_id=7)
    monkeypatch.setattr(
        "conquest.merchants.memory.GuiReader.for_session",
        lambda session: NS(windows=lambda: []),
    )
    monkeypatch.setattr(
        "conquest.panel_close.bring_into_view",
        lambda trade, name: state.update(moved=True) or True,
    )
    monkeypatch.setattr(
        "conquest.panel_close.click_close",
        lambda trade, name, **kw: state.update(closed=True),
    )
    if action == "close":
        assert trade.execute({"action": "close", "window": "Shop"}) == {"closed": True}
    else:
        assert trade.execute({"action": "open", "vendor_type": 3}) == {
            "opened": True,
            "vendor_id": 7,
        }
    assert state["moved"]
