"""A town panel under another panel is dragged clear of it.

Suicide, 2026-10-02 17:46, Twin City warehouse: the Inventory sat at
(122, 396) 447x287 under the Warehouse at (67, 109) 312x460, so bag columns
0-5 were covered. The urgent deposit of a valuable in slot 15 failed, the
warehouse reopen moved nothing, and the farmer stood in town retrying.

Failure modes, written before the change:
1. The panel is not moved, or is moved somewhere that still overlaps the
   other panel or leaves the viewport.
2. The drag grabs a title-bar point the other panel covers, or presses while
   the pointer is over any other window.
3. An unverified move is reported as done.
4. The town action is not reachable by name.
"""

from types import SimpleNamespace as NS

import pytest

from conquest import panel_close
from conquest import town_trade as t

INVENTORY = (122, 396, 447, 287)
WAREHOUSE = (67, 109, 312, 460)
VIEW = (1416, 876)


def stage(monkeypatch, inventory=INVENTORY, warehouse=WAREHOUSE, moves=True):
    panels = {
        "Inventory": {"name": "Inventory", "address": 0xABC, "geometry": list(inventory)},
        "Warehouse": {"name": "Warehouse", "address": 0xDEF, "geometry": list(warehouse)},
    }
    drags = []
    gui = NS(
        windows=lambda: list(panels.values()),
        viewport_size=lambda: list(VIEW),
        session=None,
        base=0,
        context_rva=0,
    )
    monkeypatch.setattr("conquest.merchants.memory.GuiReader.for_session", lambda session: gui)
    hovered = {"address": 0xABC}
    monkeypatch.setattr(
        "conquest.merchants.memory.unpack",
        lambda session, address, fmt: (hovered["address"],) if address == 0x3EC0 else (0,),
    )

    def drag(target, source, destination, size, *, before_press, before_release, activate):
        before_press()
        before_release()
        drags.append((source, destination))
        if moves:
            geometry = panels["Inventory"]["geometry"]
            geometry[0] += destination[0] - source[0]
            geometry[1] += destination[1] - source[1]

    monkeypatch.setattr("conquest.foreground.foreground_drag", drag)
    monkeypatch.setattr("conquest.viewport.size_for", lambda observer: VIEW)
    monkeypatch.setattr(panel_close, "wait_hover_validation", lambda guard, check: guard())
    trade = NS(observer=NS(adapter="adapter", operations=NS(target="target")))
    return trade, drags, hovered, panels


def overlaps(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah


def test_the_inventory_under_the_warehouse_goes_to_the_far_side(monkeypatch):
    # 1, 2
    trade, drags, _, panels = stage(monkeypatch)
    assert panel_close.move_clear_of(trade, "Inventory", "Warehouse") is True
    ((grab, drop),) = drags
    # The title bar's default grab point (162, 406) is under the Warehouse.
    assert grab == (399, 406) and not overlaps((*grab, 1, 1), WAREHOUSE)
    moved = panels["Inventory"]["geometry"]
    assert moved[:2] == [VIEW[0] - panel_close.VIEW_MARGIN - 447, 396]
    assert not overlaps(moved, WAREHOUSE)
    assert moved[0] + moved[2] <= VIEW[0] and moved[1] + moved[3] <= VIEW[1]
    assert trade.input_attempted


def test_panels_that_do_not_overlap_are_left_alone(monkeypatch):
    trade, drags, _, _ = stage(monkeypatch, inventory=(766, 410, 447, 287))
    assert panel_close.move_clear_of(trade, "Inventory", "Warehouse") is False
    assert drags == []


def test_a_warehouse_on_the_right_sends_the_inventory_left(monkeypatch):
    trade, drags, _, panels = stage(
        monkeypatch, inventory=(900, 396, 447, 287), warehouse=(1000, 109, 312, 460)
    )
    assert panel_close.move_clear_of(trade, "Inventory", "Warehouse") is True
    assert panels["Inventory"]["geometry"][:2] == [panel_close.VIEW_MARGIN, 396]


def test_no_press_while_the_pointer_is_over_the_warehouse(monkeypatch):
    # 2
    trade, drags, hovered, _ = stage(monkeypatch)
    hovered["address"] = 0xDEF
    with pytest.raises(panel_close.HoverNotReady):
        panel_close.move_clear_of(trade, "Inventory", "Warehouse")
    assert drags == []


def test_an_unverified_move_is_no_success(monkeypatch):
    # 3
    trade, _, _, _ = stage(monkeypatch, moves=False)
    with pytest.raises(ValueError, match="move was not verified"):
        panel_close.move_clear_of(trade, "Inventory", "Warehouse")


def test_only_town_panels_are_moved(monkeypatch):
    trade, drags, _, _ = stage(monkeypatch)
    for name, other in (("Status", "Warehouse"), ("Inventory", "Inventory")):
        with pytest.raises(ValueError, match="Unsupported display panel"):
            panel_close.move_clear_of(trade, name, other)
    assert drags == []


def test_the_town_action_moves_the_named_panel(monkeypatch):
    # 4
    calls = []
    monkeypatch.setattr(
        panel_close, "move_clear_of", lambda trade, name, other: calls.append((name, other)) or True
    )
    trade = t.TownTrade.__new__(t.TownTrade)
    trade.observer = NS(adapter=NS(assert_identity=lambda: None))
    body = {"action": "panel-clear", "window": "Inventory", "of": "Warehouse"}
    assert trade.execute(body) == {"moved": True}
    assert calls == [("Inventory", "Warehouse")]
