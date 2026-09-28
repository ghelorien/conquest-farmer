"""A restock reads a TwinCityGate only when its shops are in Twin City.

The scroll always lands in Twin City and may be read on Phoenix Castle
(return_scroll.SCROLL_SOURCES), so a WingedSnake restock in Phoenix must walk
its 40 seconds instead of scrolling away and paying the Conductress back.
"""

from types import SimpleNamespace as NS

import pytest

from conquest import return_scroll
from conquest.overnight import OvernightLoop


@pytest.mark.parametrize("restock_map, expected", [(1002, ["scroll"]), (1011, [])])
def test_restock_scrolls_only_to_a_twin_city_restock(monkeypatch, restock_map, expected):
    calls = []
    monkeypatch.setattr(return_scroll, "return_to_town", lambda loop: calls.append("scroll") or True)
    loop = OvernightLoop.__new__(OvernightLoop)
    loop.route = NS(restock_map_id=restock_map)
    assert loop.scroll_to_restock_town() is bool(expected)
    assert calls == expected
