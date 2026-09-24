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
    trade.shop = NS(gui=NS(read=read))
    trade.click = lambda p: clicks.append(p)
    monkeypatch.setattr(
        "conquest.panel_close.click_close", lambda trade, name: trade.click((100, 100))
    )
    if closes:
        assert trade.execute({"action": "close", "window": "Inventory"}) == {
            "closed": True
        }
    else:
        with pytest.raises(ValueError, match="not verified"):
            trade.execute({"action": "close", "window": "Inventory"})
    assert len(clicks) == 1 and now[0] >= 0.4
