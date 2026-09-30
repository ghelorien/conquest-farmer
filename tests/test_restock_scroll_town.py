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


# Suicide died on the ThunderApe -> Ape City runback at (563, 432) on
# 2026-09-29 22:39: the walk crosses the bosses around the GiantApe plain.
@pytest.mark.parametrize(
    "map_id, position, expected",
    [
        (1020, (330, 300), ["gate:1020"]),  # the ThunderApe field, 245 out
        (1020, (605, 335), ["gate:1020"]),  # the GiantApe plain, 210 out
        (1020, (620, 645), []),  # the Macaque field walks home
        (1002, (381, 21), ["gate:1020"]),  # another map: the gate decides
    ],
)
def test_an_ape_city_restock_reads_its_gate_only_from_far_away(
    monkeypatch, map_id, position, expected
):
    calls = []
    monkeypatch.setattr(
        return_scroll, "read_gate", lambda loop, home: calls.append(f"gate:{home}") or True
    )
    monkeypatch.setattr(
        return_scroll, "return_to_town", lambda loop: pytest.fail("a TwinCityGate lands in Twin City")
    )
    loop = OvernightLoop.__new__(OvernightLoop)
    loop.route = NS(restock_map_id=1020)
    loop.living = lambda: {"embedded_controls": {"life": {"map_id": map_id, "position": list(position)}}}
    assert loop.scroll_to_restock_town() is bool(expected)
    assert calls == expected
