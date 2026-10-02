"""Mapping the Status window for gear repair (Alex 2026-10-02 04:4x: "Keep an
eye on gear durability and go automatically repair before it gets
destroyed"; his way: unequip into the bag, the shop's Repair, click the item,
wear it again)."""

import json
from types import SimpleNamespace as NS

from conquest import discard_loot, gear_repair


def verified(read, accept, failure, timeout=2):
    result = read()
    if not accept(result):
        raise ValueError(failure)
    return result


def status_trade(monkeypatch, toggles=True):
    shown = {"open": False}
    clicks = []

    def windows(trade):
        names = {"##Control": {"geometry": (243, 774, 930, 102), "scroll": (0, 0)}}
        if shown["open"]:
            names["Status"] = {"geometry": (100, 120, 400, 520), "scroll": (0, 0)}
            names["Status/##Gear_A1"] = {"geometry": (110, 150, 380, 300), "scroll": (0, 0)}
        return names

    def click(point, button="left", **kwargs):
        clicks.append((tuple(point), button))
        if toggles or not shown["open"]:
            shown["open"] = not shown["open"]

    monkeypatch.setattr(gear_repair, "windows", windows)
    monkeypatch.setattr(discard_loot, "control_button", lambda gui, column: (766, 836) if column == 0 else None)
    monkeypatch.setattr(gear_repair.time, "sleep", lambda seconds: None)
    trade = NS(life=lambda: None, shop=NS(gui=None), click=click, verified_read=verified, input_attempted=False)
    return trade, clicks, shown


def test_the_probe_opens_the_status_window_reports_it_and_closes_it(monkeypatch):
    trade, clicks, shown = status_trade(monkeypatch)
    result = gear_repair.probe(trade)
    assert clicks == [((766, 836), "left"), ((766, 836), "left")]
    assert set(result["opened"]) == {"Status", "Status/##Gear_A1"}
    assert result["opened"]["Status"]["geometry"] == [100, 120, 400, 520]
    assert result["closed_by"] == "button" and not shown["open"]


def test_a_status_window_the_button_leaves_open_is_closed_by_its_close_button(monkeypatch):
    trade, clicks, shown = status_trade(monkeypatch, toggles=False)
    closed = []

    def click_close(trade, name, display_only=False):
        closed.append((name, display_only))
        shown["open"] = False

    monkeypatch.setattr("conquest.panel_close.click_close", click_close)
    result = gear_repair.probe(trade)
    assert closed == [("Status", True)] and result["closed_by"] == "close"


def test_a_probe_runs_once_and_only_in_town(monkeypatch):
    events, actions = [], []
    town = {"inside": False}
    monkeypatch.setattr("conquest.return_scroll.in_town", lambda life, map_id: town["inside"])
    loop = NS(
        route=NS(restock_map_id=1002),
        living=lambda: {"embedded_controls": {"life": {"map_id": 1002, "position": [430, 380]}}},
        town=lambda action: actions.append(action) or {"opened": {"Status": {}}},
        record=lambda event, **fields: events.append(event),
    )
    assert gear_repair.probe_if_asked(loop) is None and actions == []  # not asked
    gear_repair.PROBE.write_text(json.dumps({"pending": True}))
    assert gear_repair.probe_if_asked(loop) is None and gear_repair.PROBE.exists()  # in the field
    town["inside"] = True
    assert gear_repair.probe_if_asked(loop) == {"opened": {"Status": {}}}
    assert actions == ["gear-window"] and not gear_repair.PROBE.exists()
    assert events == ["gear_window_probe_opening", "gear_window_probe"]
    assert gear_repair.probe_if_asked(loop) is None and actions == ["gear-window"]
