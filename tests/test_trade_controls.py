import json
import struct
from types import SimpleNamespace as NS

import pytest

from conquest.merchants import trade_controls as controls
from conquest.memory_life import CLIENT_SHA256


@pytest.mark.parametrize(
    "failure", [None, "idle", "fingerprint", "module", "renderer", "identity"]
)
def test_native_target_mode_requires_pinned_code_and_identity(failure):
    base = 0x140000000
    reads = []

    def read(address, size):
        reads.append(address - base)
        if address == base + controls.TRADE_MODE_RVA:
            return struct.pack("<I", 16 if failure == "idle" else 19)
        result = controls.SIGNATURES[address - base]
        return bytes(size) if failure == "renderer" else result

    def identity():
        if failure == "identity":
            raise ValueError("identity changed")

    session = NS(
        expected_sha256="wrong" if failure == "fingerprint" else CLIENT_SHA256,
        modules=[]
        if failure == "module"
        else [{"name": "ImConquer.exe", "base": base, "size": 0x700000}],
        read_block=read,
        assert_identity=identity,
    )
    if failure not in (None, "idle"):
        with pytest.raises(ValueError):
            controls.targeting_state(session)
        assert controls.TRADE_MODE_RVA not in reads
    else:
        result = controls.targeting_state(session)
        assert result["rva"] == 0x699290 and result["value"] == 19
        assert result["targeting_trade"] is (failure is None)


def test_trade_button_tracks_the_verified_items_column(monkeypatch):
    from conquest import discard_loot

    monkeypatch.setattr(controls, "targeting_state", lambda session: {})
    point = [846, 867]
    monkeypatch.setattr(discard_loot, "inventory_button", lambda gui: tuple(point))
    gui = NS(session=object())
    assert controls.trade_button(gui) == (846, 887)
    point[:] = [1082, 625]
    assert controls.trade_button(gui) == (1082, 645)

    def stale(gui):
        raise ValueError("Inventory button table is not current")

    monkeypatch.setattr(discard_loot, "inventory_button", stale)
    with pytest.raises(ValueError, match="not current"):
        controls.trade_button(gui)


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
    import conquest.memory_shop as shop

    native_point = (900, 730) if variation == "offscreen" else (820, 730)
    monkeypatch.setattr(shop, "MemoryGui", lambda adapter: object())
    monkeypatch.setattr(controls, "trade_button", lambda gui: native_point)
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
