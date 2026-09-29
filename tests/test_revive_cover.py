"""The recovery revive closes a display panel lying over the Revive popup.

Toxic died in Ape City on 2026-09-29 10:44 with a travel heal's Inventory
(397,465,447,287) over ##SkillsPopup (680,696,56,56), which holds the
ReviveButton. RouteRecovery's revive was held before input on every pass
("Pointer is not over the memory-identified merchant control") and the
farmer stayed dead for over an hour.
"""

from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from conquest import route_recovery
from conquest.capture import CaptureUnavailable

POPUP = {"name": "##SkillsPopup", "geometry": [680.0, 696.0, 56.0, 56.0]}
INVENTORY = {"name": "Inventory", "geometry": [397.0, 465.0, 447.0, 287.0]}


def test_toxics_inventory_covers_the_revive_popup():
    assert route_recovery.covering_panel([INVENTORY, POPUP]) == "Inventory"


def test_panels_clear_of_the_popup_or_no_popup_cover_nothing():
    aside = {"name": "Inventory", "geometry": [100.0, 100.0, 300.0, 200.0]}
    chat = {"name": "Chat##Message", "geometry": [600.0, 650.0, 300.0, 200.0]}
    assert route_recovery.covering_panel([aside, POPUP]) is None
    assert route_recovery.covering_panel([chat, POPUP]) is None
    assert route_recovery.covering_panel([INVENTORY]) is None


def sender(monkeypatch, cover):
    from conquest import desktop_runtime, viewport

    monkeypatch.setattr(desktop_runtime, "physical_coordinates", nullcontext)
    monkeypatch.setattr(route_recovery, "revive_cover", lambda observer: cover)
    closed = []
    monkeypatch.setattr(
        "conquest.panel_close.click_close",
        lambda trade, name: closed.append((trade, name)),
    )

    def no_revive_point(*args):
        raise ValueError("reached the revive point")

    monkeypatch.setattr(viewport, "revive_point", no_revive_point)
    life = SimpleNamespace(
        position=(602, 278),
        map_id=1020,
        ghost_candidate=True,
        revive_ready_candidate=True,
        current_hp=0,
        max_hp=759,
        status=1056,
    )
    target = SimpleNamespace(
        snapshot=lambda: dict(
            client_size=[1416, 876], foreground=1, root_hwnd=1, minimized=False
        )
    )
    trade = object()
    observer = SimpleNamespace(
        adapter=None,
        viewport_size=(1416, 876),
        bridge=SimpleNamespace(operations=SimpleNamespace(target=target)),
        focus_client=lambda: None,
        read_life=lambda: life,
        town_trade=trade,
    )
    monkeypatch.setattr("conquest.viewport.size_for", lambda observer: (1416, 876))
    monkeypatch.setattr("conquest.mouse_priority.require_idle", lambda: None)
    send = route_recovery.EmbeddedRecoveryInput(observer, None, terrain=None, layout=None)
    return send, vars(life), closed, trade


def test_revive_closes_the_covering_panel_and_sends_no_revive(monkeypatch):
    send, observed, closed, trade = sender(monkeypatch, "Inventory")
    with pytest.raises(CaptureUnavailable, match="Closed Inventory over Revive"):
        send.send("revive", None, observed)
    assert closed == [(trade, "Inventory")]


def test_an_uncovered_popup_goes_straight_to_the_revive_point(monkeypatch):
    send, observed, closed, trade = sender(monkeypatch, None)
    with pytest.raises(CaptureUnavailable, match="reached the revive point"):
        send.send("revive", None, observed)
    assert closed == []
