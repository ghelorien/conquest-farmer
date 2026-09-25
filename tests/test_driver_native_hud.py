import json
from types import SimpleNamespace as NS

import pytest


# Failure modes, written before converting this test to the 1078 driver path:
# - "label": a HUD spec naming anything but Trade/Items is rejected.
# - "window": a HUD spec outside ##Control is rejected.
# - "offscreen": a live memory point outside the qualified window is rejected.
# - "slot": a native HUD control never takes an inventory slot.
# - "geometry": a degenerate live window geometry is rejected.
# - "saved_size": a stale saved window size must not block the live point.
# - "resized_viewport": the live GUI/client ratio, not a saved one, scales it.
@pytest.mark.parametrize(
    "variation",
    [
        None,
        "label",
        "window",
        "saved_size",
        "resized_viewport",
        "offscreen",
        "slot",
        "geometry",
    ],
)
def test_native_hud_uses_current_memory_point_and_preserves_geometry_guards(
    tmp_path, monkeypatch, variation
):
    from conquest.merchants.driver import MerchantDriver
    from conquest.merchants import trade_driver_1078
    import conquest.memory_shop as shop

    native_point = (900, 730) if variation == "offscreen" else (820, 730)
    sessions = []
    monkeypatch.setattr(
        shop,
        "MemoryGui",
        NS(for_session=lambda adapter: sessions.append(adapter) or object()),
    )
    monkeypatch.setattr(trade_driver_1078, "trade_button", lambda gui: native_point)
    spec = {
        "mode": "native_items_trade",
        "window": "##Control",
        "label": "Trade",
        "size": [930, 102],
    }
    if variation == "label":
        spec["label"] = "Other"
    if variation == "window":
        spec["window"] = "Booth"
    if variation == "saved_size":
        spec["size"] = [930, 100]
    profile = {
        "controls": {"start_trade": spec},
        "gui_size": [1000, 800],
        "client_size": [1250, 1000],
    }
    path = tmp_path / "qualified.json"
    path.write_text(json.dumps(profile))
    driver = MerchantDriver.__new__(MerchantDriver)
    driver.trade1078 = True
    capabilities = []
    # Qualification receipts have their own tests; this one covers geometry.
    driver.require_qualified = lambda capability: capabilities.append(capability)
    driver.qualification = path
    driver.observer = NS(adapter=object())
    driver.target = NS(snapshot=lambda: {"client_size": [1500, 1200]})
    driver.memory = NS(
        gui=NS(
            viewport_size=lambda: (
                [1000, 900] if variation == "resized_viewport" else [1000, 800]
            )
        )
    )
    state = {
        "windows": [
            {"name": spec["window"], "geometry": [-50, 691, 930, 102], "scroll": [0, 0]}
        ]
    }
    if variation == "geometry":
        state["windows"][0]["geometry"] = [-50, 691, 0, 102]
    rejected = {"label", "window", "offscreen", "slot", "geometry"}
    if variation in rejected:
        with pytest.raises(ValueError):
            driver.point(state, "start_trade", 0 if variation == "slot" else None)
    else:
        expected = (1230, 973) if variation == "resized_viewport" else (1230, 1095)
        assert driver.point(state, "start_trade") == expected
        assert sessions == [driver.observer.adapter]
    assert capabilities == ["farmer_delivery"]
