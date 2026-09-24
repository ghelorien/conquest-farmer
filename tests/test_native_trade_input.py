from types import SimpleNamespace as NS

import pytest

from conquest.merchants import native_trade_input as native


@pytest.mark.parametrize(
    "accepted,other,count", [(True, False, 0), (False, True, 0), (False, False, 20)]
)
def test_native_drop_rejects_locked_or_full_trade(monkeypatch, accepted, other, count):
    monkeypatch.setattr(native, "trade_grid", lambda *a: (None, {}))
    monkeypatch.setattr(
        native, "cell", lambda *a: pytest.fail("No drop cell may be selected")
    )
    driver = NS(memory=NS(gui=None))
    with pytest.raises(ValueError):
        native.locate(
            driver,
            {
                "trade": dict(
                    accepted=accepted, other_accepted=other, own_items=[{}] * count
                )
            },
            "native_trade_drop",
        )


def test_native_trade_buttons_use_the_correct_hover_scope(monkeypatch):
    seen = []
    window = {"address": 7}
    gui = NS(assert_hovered=lambda w, label, **kw: seen.append((w, label, kw)))
    driver = NS(memory=NS(gui=gui))
    monkeypatch.setattr(native, "confirm_control", lambda *a: (window, (100, 200), 123))
    monkeypatch.setattr(native, "request_control", lambda *a: (window, (300, 400)))
    native.hover(driver, {}, "native_trade_confirm")
    native.hover(driver, {}, "native_trade_request")
    assert seen == [
        (window, "Accept Trade", {"seeds": [123]}),
        (window, "Accept", {"seeds": None}),
    ]
    with pytest.raises(ValueError, match="Unknown"):
        native.locate(driver, {}, "unqualified_control")


@pytest.mark.parametrize(
    "point,valid",
    [((400, 300), True), ((0, 300), False), ((800, 300), False), ((400, 600), False)],
)
def test_native_point_scales_only_visible_gui_coordinates(monkeypatch, point, valid):
    monkeypatch.setattr(native, "locate", lambda *a: ({}, point, None, None))
    driver = NS(
        memory=NS(gui=NS(viewport_size=lambda: (800, 600))),
        target=NS(snapshot=lambda: {"client_size": (1600, 1200)}),
    )
    if valid:
        assert native.point(driver, {}, "native_trade_drop") == (800, 600)
    else:
        with pytest.raises(ValueError, match="outside"):
            native.point(driver, {}, "native_trade_drop")


@pytest.mark.parametrize("changed_spacing", [False, True])
def test_listing_preflight_allows_resize_but_rejects_changed_table_schema(
    changed_spacing,
):
    from conquest.merchants.driver import MerchantDriver

    controls = {
        "inventory_item": {
            "window": "Inventory",
            "size": [400, 200],
            "table": "Items",
            "columns": 2,
            "stride": [40, 40],
            "cell_offset": [20, 20],
        },
        "remove_listing": {
            "window": "BoothChild",
            "size": [400, 200],
            "table": "BoothTable",
            "columns": 2,
            "stride": [80, 64],
            "cell_offset": [60, 39],
        },
        "booth_drop": {"window": "Booth", "size": [800, 466], "offset": [400, 233]},
    }
    snapshot = {
        "windows": [
            {"name": "Inventory", "geometry": [0, 0, 620, 300]},
            {"name": "BoothChild", "geometry": [0, 0, 700, 350]},
            {"name": "Booth", "geometry": [0, 0, 1000, 600]},
        ]
    }

    def table(window, label):
        stride = (
            41
            if changed_spacing and label == "Items"
            else 40
            if label == "Items"
            else 80
        )
        return {
            "columns": [{"content_x": 10}, {"content_x": 10 + stride}],
            "row_height": 40 if label == "Items" else 64,
            "outer": (0, 0, 500, 300),
            "clip": (0, 0, 500, 300),
        }

    driver = NS(
        require_qualified=lambda capability: {"controls": controls},
        read=lambda: snapshot,
        memory=NS(gui=NS(table=table)),
    )
    driver._control_layout = lambda snapshot, control, spec: (
        MerchantDriver._control_layout(driver, snapshot, control, spec)
    )
    if changed_spacing:
        with pytest.raises(ValueError, match="spacing or schema"):
            MerchantDriver.verify_listing_layout(driver)
    else:
        MerchantDriver.verify_listing_layout(driver)


def test_resized_booth_drop_uses_current_panel_center_and_current_scale(tmp_path):
    from conquest.merchants.driver import MerchantDriver

    profile = tmp_path / "qualification.json"
    profile.write_text(
        __import__("json").dumps(
            {
                "client_size": [1200, 800],
                "gui_size": [1200, 800],
                "controls": {
                    "booth_drop": {
                        "window": "Booth",
                        "size": [800, 466],
                        "offset": [400, 233],
                    }
                },
            }
        )
    )
    driver = NS(
        qualification=profile,
        memory=NS(gui=NS(viewport_size=lambda: (1600, 900))),
        target=NS(snapshot=lambda: {"client_size": [2400, 1350]}),
    )
    driver._control_layout = lambda snapshot, control, spec: (
        MerchantDriver._control_layout(driver, snapshot, control, spec)
    )
    snapshot = {
        "windows": [
            {"name": "Booth", "geometry": [66, 27, 1000, 500], "scroll": [0, 0]}
        ]
    }
    assert MerchantDriver.point(driver, snapshot, "booth_drop") == (849, 416)


def test_booth_capability_survives_ordinary_client_resize(tmp_path):
    from conquest.merchants.driver import MerchantDriver

    profile = tmp_path / "qualification.json"
    profile.write_text(
        __import__("json").dumps(
            {
                "client_sha256": "build",
                "character": "Dutch",
                "server": "America",
                "client_size": [1200, 800],
                "gui_size": [1200, 800],
                "capabilities": {"booth_input": True},
                "evidence": "booth-control-evidence.json",
            }
        )
    )
    driver = NS(
        qualification=profile,
        observer=NS(character="Dutch", adapter=NS(expected_sha256="build")),
        target=NS(snapshot=lambda: {"client_size": [1600, 1000]}),
        memory=NS(gui=NS(viewport_size=lambda: [1600, 1000])),
    )
    assert MerchantDriver.require_qualified(driver, "booth_input")["client_size"] == [
        1200,
        800,
    ]
