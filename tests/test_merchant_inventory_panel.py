import copy
from types import SimpleNamespace as NS
import pytest
from conquest.merchants.inventory_panel import verify_inventory_panel


@pytest.mark.parametrize(
    "mode",
    [
        "open",
        "closed",
        "no_response",
        "stock_changed",
        "manual_stop",
        "geometry_changed",
    ],
)
def test_inventory_recovery_qualification_requires_real_transition(monkeypatch, mode):
    from conquest.merchants import (
        inventory_panel as module,
        connect_market,
        return_driver,
    )

    state = dict(
        identity={"pid": 1},
        map_id=1036,
        position=[264, 206],
        own_booth_uid=99,
        inventory=[],
        booth=[],
        silver=100,
        booth_open=True,
        trade=None,
        request=None,
        windows=[] if mode == "closed" else [{"name": "Inventory"}],
    )
    calls = []
    evidence = []
    clock = [0.0]
    driver = NS(read=lambda: copy.deepcopy(state), observer=NS(adapter=object()))
    monkeypatch.setattr("conquest.memory_shop.MemoryGui", lambda s: object())
    monkeypatch.setattr(
        "conquest.discard_loot.inventory_button", lambda gui: (400, 600)
    )
    dimensions = {"client_size": [1000, 800], "gui_size": [1000, 800]}
    monkeypatch.setattr(
        connect_market,
        "geometry",
        lambda d: {} if mode == "geometry_changed" and calls else dimensions,
    )
    monkeypatch.setattr(
        connect_market, "record_capability", lambda *a, **kw: evidence.append((a, kw))
    )
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        module.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds)
    )

    class Travel:
        def __init__(self, d):
            pass

        def click(self, point, check, *, before_press):
            check()
            before_press()
            calls.append(point)
            if mode == "stock_changed":
                state["silver"] += 1
            if mode != "no_response":
                state["windows"] = [] if state["windows"] else [{"name": "Inventory"}]

    monkeypatch.setattr(return_driver, "ReturnDriver", Travel)

    def check():
        if mode == "manual_stop" and calls:
            raise ValueError("Manual stop")

    if mode in ("open", "closed"):
        result = verify_inventory_panel(driver, check)
        assert result["closed_to_open_verified"] and state["windows"]
        assert len(calls) == (2 if mode == "open" else 1)
        assert len(evidence) == 1 and evidence[0][0][1] == "inventory_panel"
    else:
        with pytest.raises(ValueError):
            verify_inventory_panel(driver, check)
        assert len(calls) == 1 and not evidence
