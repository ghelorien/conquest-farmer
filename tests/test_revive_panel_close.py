"""A dead farmer can still close a display panel, so Revive can be hovered.

Toxic died in Ape City on 2026-09-29 10:44 with the Inventory window, opened
by a travel heal, lying over ##SkillsPopup (the ReviveButton's popup). Every
revive hover then failed, and no town click could close the panel while dead
("Town action requires a living character on the town map").
"""

from types import SimpleNamespace as NS

import pytest

from conquest import panel_close, town_trade


def trade_for(life, clicks, monkeypatch):
    monkeypatch.setattr(town_trade, "login_screen", lambda hwnd: False)
    monkeypatch.setattr(
        town_trade,
        "foreground_click",
        lambda target, x, y, size, **kwargs: clicks.append((x, y)),
    )
    monkeypatch.setattr(town_trade, "size_for", lambda observer: (1416, 876))
    trade = town_trade.TownTrade.__new__(town_trade.TownTrade)
    trade.observer = NS(
        operations=NS(target=NS(hwnd=1)),
        read_life=lambda: life,
    )
    return trade


DEAD = NS(dead_candidate=True, map_id=1020, current_hp=0, max_hp=759)


def test_town_clicks_still_refuse_a_dead_character(monkeypatch):
    clicks = []
    trade = trade_for(DEAD, clicks, monkeypatch)
    with pytest.raises(ValueError, match="living character"):
        trade.click((708, 724))
    assert clicks == []


def test_a_display_panel_close_may_click_while_dead(monkeypatch):
    clicks = []
    trade = trade_for(DEAD, clicks, monkeypatch)
    trade.click((820, 477), allow_dead=True)
    assert clicks == [(820, 477)]


def test_panel_close_asks_to_work_while_dead(monkeypatch):
    seen = []
    inventory = {"name": "Inventory", "address": 1, "geometry": [397, 465, 447, 287]}
    monkeypatch.setattr(
        panel_close,
        "GuiReader",
        NS(
            for_session=lambda adapter: NS(
                windows=lambda: [inventory], assert_hovered=lambda *args: None
            )
        ),
    )
    monkeypatch.setattr(panel_close, "wait_hover_validation", lambda guard, check: guard())

    def click(point, *, before_press, before_mouse_down=None, allow_dead=False):
        before_press()
        seen.append((point, allow_dead))

    panel_close.click_close(NS(observer=NS(adapter=object()), click=click), "Inventory")
    # The close button, 23.5 px from the right edge and 12 px down.
    assert seen == [((820, 477), True)]
