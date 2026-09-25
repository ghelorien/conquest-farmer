from contextlib import nullcontext
from copy import deepcopy
import ctypes
import time
from types import SimpleNamespace as NS

import pytest

from conquest.capture import CaptureUnavailable
from conquest.merchants import delivery_probe as probe, farmer_trade, farmer_preferences
from conquest.merchants import memory, driver
from conquest import foreground, desktop_runtime, memory_shop


@pytest.mark.parametrize(
    "change",
    [
        "occupied_move",
        "occupied_order",
        "address",
        "uid",
        "name",
        "position",
        "point",
        "actionability",
        "mode",
        "scene_changed",
        "post_hud_position",
    ],
)
def test_supervised_request_guard_ignores_other_players_but_rejects_target_drift(
    monkeypatch, change
):
    item = dict(
        uid=10, type_id=410008, plus=1, gem1=0, gem2=0, quantity=1, bound=False, slot=0
    )

    def account(name, uid, stock):
        return dict(
            character=name,
            character_uid=uid,
            identity={"pid": uid},
            server="America",
            timestamp=time.time(),
            map_id=1036,
            hp=100,
            silver=200,
            capacity=40,
            position=[10, 10],
            inventory=stock,
            booth=[],
            trade=None,
            request=None,
            windows=[{"name": "##Control"}],
        )

    farmer, merchant = account("Parasite", 1, [item]), account("Spiritual", 2, [])
    intent = deepcopy({"farmer": farmer, "merchant": merchant, "items": [item]})
    recipient = {
        "address": 100,
        "uid": 2,
        "name": "Spiritual",
        "position": [10, 10],
        "point": [400, 300],
        "occupied_tiles": [[10, 10], [20, 20], [30, 30]],
    }
    changed = deepcopy(recipient)
    if change == "occupied_move":
        changed["occupied_tiles"][1] = [21, 20]
    if change == "occupied_order":
        changed["occupied_tiles"].reverse()
    if change in ("address", "uid"):
        changed[change] += 1
    if change == "name":
        changed["name"] = "Other"
    if change == "position":
        changed["position"] = [11, 10]
    if change == "point":
        changed["point"] = [401, 300]
    calls = []
    presses = []
    saved = []
    targeting = [False]

    def read_recipient(observer, profile, peer, *, farmer=None, targeting=False):
        assert farmer is not None
        calls.append(targeting)
        if len(calls) < 3:
            return deepcopy(recipient)
        if change == "actionability":
            raise CaptureUnavailable("Receiver is covered")
        if change == "mode":
            raise ValueError("Client is not in the qualified trade targeting mode")
        if change == "scene_changed":
            raise farmer_trade.RecipientSceneChanged("Receiver scene changed")
        return changed

    def click(target, x, y, size, **kwargs):
        kwargs["before_press"]()
        presses.append((x, y))
        if len(presses) == 1:
            targeting[0] = True
            if change == "post_hud_position":
                farmer["position"] = [11, 10]
        else:
            merchant["request"] = {"participant": "Parasite", "participant_uid": 1}

    gui = NS(
        viewport_size=lambda: [1000, 800], assert_hovered=lambda *args, **kwargs: None
    )
    target = NS(snapshot=lambda: {"client_size": [1000, 800]})
    observer = NS(adapter=NS(expected_sha256="pinned"), operations=NS(target=target))

    class Queue:
        def put(self, item):
            item[1].set()

    ui = NS(
        closed=False,
        safe_to_yield=lambda: True,
        ui_requests=Queue(),
        coordinator=NS(check=lambda: None, lease=lambda _, **kw: nullcontext()),
        app=NS(
            observer=observer,
            closing=False,
            show_game=lambda: None,
            control=NS(
                snapshot=lambda: {"enabled": False, "paused": False, "revision": 1}
            ),
        ),
    )
    monkeypatch.setattr(farmer_preferences, "permits_new_delivery", lambda _: None)
    monkeypatch.setattr(
        "conquest.merchants.delivery_farmer_surface.prepare",
        lambda *a, **k: lambda: None,
    )
    monkeypatch.setattr(
        "conquest.merchants.delivery_farmer_surface.verify_stage_pair",
        lambda *a, **k: None,
    )
    observer.operations.target.hwnd = 7
    monkeypatch.setattr("conquest.focus_recovery.activate_client", lambda *a: True)
    monkeypatch.setattr(desktop_runtime, "physical_coordinates", nullcontext)
    monkeypatch.setattr(foreground, "foreground_click", click)
    monkeypatch.setattr(memory, "MerchantMemory", lambda _: NS(gui=gui))
    monkeypatch.setattr(
        "conquest.merchants.delivery_bridge.source_memory", lambda _: NS(gui=gui)
    )
    monkeypatch.setattr(memory_shop.MemoryGui, "for_session", lambda _: object())
    monkeypatch.setattr(
        driver, "wait_hover_validation", lambda callback, check: callback()
    )
    monkeypatch.setattr(farmer_trade, "trade_button", lambda _: (500, 600))
    monkeypatch.setattr(
        farmer_trade,
        "targeting_state",
        lambda _: {
            "current": 19 if targeting[0] else 16,
            "targeting_trade": targeting[0],
        },
    )
    monkeypatch.setattr(farmer_trade, "recipient_record", read_recipient)
    monkeypatch.setattr(
        probe, "pair", lambda *args: (deepcopy(farmer), deepcopy(merchant))
    )
    monkeypatch.setattr(probe, "read_json", lambda _: {"client_sha256": "pinned"})
    monkeypatch.setattr(
        probe, "write_probe", lambda path, state: saved.append(deepcopy(state))
    )
    monkeypatch.setattr(ctypes, "windll", NS(user32=NS(GetAsyncKeyState=lambda _: 0)))
    state = {"phase": "prepared", "character": "Spiritual", "intent": intent}
    if change.startswith("occupied_"):
        probe.run(ui, intent, 1, state)
        assert presses == [(500, 600), (400, 300)]
        assert state["phase"] == "request_verified"
    else:
        with pytest.raises((ValueError, CaptureUnavailable)):
            probe.run(ui, intent, 1, state)
        assert presses == [(500, 600)]
        assert state["phase"] == (
            "targeting_verified"
            if change == "post_hud_position"
            else "request_submitted"
        )
        assert merchant["request"] is None
    assert calls == ([False] if change == "post_hud_position" else [False, True, True])
