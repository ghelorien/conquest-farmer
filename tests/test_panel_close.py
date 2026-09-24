from types import SimpleNamespace as NS
import pytest
from conquest import panel_close as p


@pytest.mark.parametrize("race_at", [1, 2])
def test_registry_race_before_close_is_retryable_without_press(monkeypatch, race_at):
    from conquest.merchants.memory import GuiObservationChanged
    from conquest.town_trade import TownObservationUnavailable

    calls = [0]
    pressed = []

    def windows():
        calls[0] += 1
        if calls[0] == race_at:
            raise GuiObservationChanged("GUI registry changed")
        return [{"name": "Warehouse", "address": 1, "geometry": [50, 50, 200, 300]}]

    monkeypatch.setattr(
        p, "GuiReader", lambda a: NS(windows=windows, assert_hovered=lambda *args: None)
    )
    monkeypatch.setattr(p, "wait_hover_validation", lambda guard, check: guard())

    def click(point, *, before_press):
        before_press()
        pressed.append(point)

    trade = NS(observer=NS(adapter=object()), click=click, input_attempted=True)
    with pytest.raises(TownObservationUnavailable, match="no button pressed"):
        p.click_close(trade, "Warehouse")
    assert not pressed


def test_post_press_failure_is_not_reclassified(monkeypatch):
    from conquest.merchants.memory import GuiObservationChanged

    monkeypatch.setattr(
        p,
        "GuiReader",
        lambda a: NS(
            windows=lambda: [
                {"name": "Warehouse", "address": 1, "geometry": [50, 50, 200, 300]}
            ],
            assert_hovered=lambda *args: None,
        ),
    )
    monkeypatch.setattr(p, "wait_hover_validation", lambda guard, check: guard())
    pressed = []

    def click(point, *, before_press):
        before_press()
        pressed.append(point)
        raise GuiObservationChanged("After button press")

    with pytest.raises(GuiObservationChanged):
        p.click_close(NS(observer=NS(adapter=object()), click=click), "Warehouse")
    assert len(pressed) == 1


@pytest.mark.parametrize("case", ["success", "moved", "hover"])
def test_exact_close_widget_must_be_verified_before_press(monkeypatch, case):
    calls = []
    pressed = []
    w = {"name": "Inventory", "address": 1, "geometry": [50, 50, 200, 300]}

    def windows():
        calls.append("read")
        return [{**w, "address": 2 if case == "moved" and len(calls) > 1 else 1}]

    def hover(window, label):
        assert label == "#CLOSE"
        if case == "hover":
            raise ValueError("Wrong hover")

    monkeypatch.setattr(
        p, "GuiReader", lambda a: NS(windows=windows, assert_hovered=hover)
    )
    monkeypatch.setattr(p, "wait_hover_validation", lambda guard, check: guard())

    def click(point, *, before_press):
        before_press()
        pressed.append(point)

    trade = NS(observer=NS(adapter=object()), click=click)
    if case == "success":
        p.click_close(trade, "Inventory")
        assert pressed == [(226, 62)]
    else:
        with pytest.raises(ValueError):
            p.click_close(trade, "Inventory")
        assert not pressed
