from copy import deepcopy
from contextlib import nullcontext
from types import SimpleNamespace as NS
import time

import pytest

from conquest.merchants import delivery_cancel_probe as probe
from conquest.capture import CaptureUnavailable


def participants(farmer="Parasite"):
    def snapshot(name, uid, items):
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
            inventory=items,
            booth=[],
            trade=None,
            request=None,
        )

    item = dict(
        uid=10, type_id=720027, plus=0, gem1=0, gem2=0, quantity=1, bound=False, slot=0
    )
    f = snapshot(farmer, 1, [item])
    m = snapshot("Spiritual", 2, [])
    intent = deepcopy({"farmer": f, "merchant": m, "items": [item]})
    m["request"] = {
        "participant": farmer,
        "participant_uid": f["character_uid"],
        "message": farmer + " wishes to trade with you.",
    }
    return intent, f, m


@pytest.mark.parametrize(
    "change",
    [
        None,
        "farmer_name",
        "trade",
        "farmer_request",
        "foreign_request",
        "identity",
        "uid",
        "currency",
        "position",
        "inventory",
        "stale",
    ],
)
def test_cancel_requires_exact_unchanged_participants(change):
    intent, f, m = participants(
        "AnotherFarmer" if change == "farmer_name" else "Parasite"
    )
    if change == "trade":
        m["trade"] = {"participant": "Parasite"}
    if change == "farmer_request":
        f["request"] = {"participant": "Someone"}
    if change == "foreign_request":
        m["request"]["participant"] = "Someone"
    if change == "identity":
        m["identity"] = {"pid": 3}
    if change == "uid":
        m["character_uid"] = 3
    if change == "currency":
        f["silver"] = 199
    if change == "position":
        m["position"] = [11, 10]
    if change == "inventory":
        f["inventory"] = []
    if change == "stale":
        m["timestamp"] -= 20
    if change in (None, "farmer_name"):
        probe.unchanged(intent, f, m)
    else:
        with pytest.raises(ValueError):
            probe.unchanged(intent, f, m)


@pytest.mark.parametrize(
    "case",
    [
        "success",
        "cancelled",
        "expired",
        "moved",
        "hover",
        "after_identity",
        "after_stock",
    ],
)
def test_cancel_click_is_guarded_and_result_must_reconcile(monkeypatch, tmp_path, case):
    from conquest import desktop_runtime, foreground

    intent, f, m = participants()
    state = {"character": "Spiritual", "intent": intent}
    clock = [10.0]
    presses = []
    saved = []
    window = {"name": "###Confirm", "geometry": [1, 2, 300, 200]}
    control_state = {"enabled": False, "revision": 5}
    ui = NS(
        closed=False,
        app=NS(closing=False, control=NS(snapshot=lambda: dict(control_state))),
        coordinator=NS(check=lambda: None, lease=lambda name: nullcontext()),
        calibrating=set(),
        calibration_cancel={},
    )

    def hovered(w, label):
        assert label == "Cancel"
        if case == "hover":
            raise ValueError("Wrong hover")

    driver = NS(
        target=NS(snapshot=lambda: {"client_size": [1000, 800]}),
        memory=NS(gui=NS(viewport_size=lambda: [1000, 800], assert_hovered=hovered)),
    )
    ui.runtime = NS(
        enabled=lambda name: False, controllers={"Spiritual": NS(driver=driver)}
    )
    monkeypatch.setattr(probe, "pair", lambda *args: (deepcopy(f), deepcopy(m)))
    monkeypatch.setattr(probe, "control", lambda d, s: (deepcopy(window), (100, 100)))
    monkeypatch.setattr(probe, "write_json", lambda p, s: saved.append(deepcopy(s)))
    monkeypatch.setattr(probe.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(probe.ctypes.windll.user32, "GetAsyncKeyState", lambda key: 0)
    monkeypatch.setattr(desktop_runtime, "physical_coordinates", nullcontext)
    monkeypatch.setattr(probe, "wait_hover_validation", lambda guard, check: guard())

    def click(target, x, y, size, **kwargs):
        assert saved[-1]["phase"] == "cancel_submitted"
        if case == "cancelled":
            ui.calibration_cancel["Spiritual"].set()
        if case == "expired":
            clock[0] += 16
        if case == "moved":
            window["geometry"][0] += 1
        kwargs["before_press"]()
        presses.append((x, y))
        m["request"] = None
        if case == "after_identity":
            m["identity"] = {"pid": 99}
        if case == "after_stock":
            f["inventory"] = []

    monkeypatch.setattr(foreground, "foreground_click", click)
    if case == "success":
        probe.run(ui, state)
        assert state["phase"] == "cancel_verified" and presses == [(100, 122)]
    else:
        with pytest.raises((ValueError, CaptureUnavailable)):
            probe.run(ui, state)
        assert len(presses) == (1 if case.startswith("after_") else 0)
        assert state["phase"] != "cancel_verified"
    assert not ui.calibrating


def test_replaced_request_reconciliation_preserves_foreign_request():
    from conquest.merchants.cancel_reserved_request import prove_request_replaced

    intent, f, m = participants()
    with pytest.raises(ValueError):
        prove_request_replaced(intent, f, m)
    m["request"] = {
        "participant": "OtherPlayer",
        "message": "OtherPlayer wishes to trade with you.",
    }
    assert (
        prove_request_replaced(intent, f, m)["outcome"]
        == "original_request_absent_no_transfer"
    )
    assert m["request"]["participant"] == "OtherPlayer"
    f["inventory"] = []
    with pytest.raises(ValueError):
        prove_request_replaced(intent, f, m)
