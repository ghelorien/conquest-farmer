from types import SimpleNamespace as NS
import pytest
from conquest import town_trade as t


@pytest.mark.parametrize("closes", [True, False])
def test_panel_close_waits_for_memory_without_repeating_click(monkeypatch, closes):
    now = [0]
    clicks = []
    monkeypatch.setattr(t.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(t.time, "sleep", lambda dt: now.__setitem__(0, now[0] + dt))
    panel = NS(position=(50, 50), size=(200, 300))

    def read(name):
        if closes and now[0] >= 0.4:
            raise ValueError("Window absent")
        return panel

    trade = t.TownTrade.__new__(t.TownTrade)
    trade.observer = NS(adapter=NS(assert_identity=lambda: None))
    trade.shop = NS(gui=NS(read=read))
    trade.click = lambda p: clicks.append(p)
    # The retry guard compares the open panel set before and after (afee29d).
    monkeypatch.setattr(
        "conquest.merchants.memory.GuiReader.for_session",
        lambda session: NS(windows=lambda: []),
    )

    def click_close(trade, name, *, validate=None, before_mouse_down=None):
        if validate is not None:
            validate()
        if before_mouse_down is not None:
            before_mouse_down()
        trade.click((100, 100))

    monkeypatch.setattr("conquest.panel_close.click_close", click_close)
    if closes:
        assert trade.execute({"action": "close", "window": "Inventory"}) == {
            "closed": True
        }
    else:
        with pytest.raises(ValueError, match="not verified"):
            trade.execute({"action": "close", "window": "Inventory"})
    # A slow close is waited for, not re-clicked. Since afee29d an unverified
    # close is retried exactly once after memory proves the same panel is open.
    assert len(clicks) == (1 if closes else 2) and now[0] >= 0.4
