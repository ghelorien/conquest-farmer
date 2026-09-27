"""The worker's "sell-scroll" action sells a TwinCityGate and nothing else.

Runs the real TownTrade sale against test_arrow_remnant_sale_e2e's fake shop.
"""

import pytest

from test_arrow_remnant_sale_e2e import LUCKY, Game, _trade

SCROLL = 1060020
PROTECTED = "Selected item is absent or protected from automatic sale"
UNVERIFIED = "Sale was not verified; no further sale issued"


@pytest.mark.parametrize(
    "action, kind, change, expected",
    [
        ("sell-scroll", SCROLL, 66, "sold"),
        ("sell-scroll", SCROLL, 0, UNVERIFIED),  # Taken for nothing: never proof.
        ("sell-scroll", LUCKY, 200, PROTECTED),  # Only a TwinCityGate.
        ("sell", SCROLL, 66, PROTECTED),  # Junk sales still keep scrolls.
    ],
)
def test_sell_scroll_only_sells_a_verified_twincitygate(
    monkeypatch, action, kind, change, expected
):
    game = Game(kind, 1, True, change)
    trade = _trade(game, monkeypatch)
    body = {"action": action, "vendor_type": 5, "uid": 7001}
    if expected == "sold":
        assert trade.execute(body) == {
            "sold": 7001,
            "type_id": SCROLL,
            "plus": None,
            "silver_gained": 66,
        }
        assert game.drags == 1 and game.silver == 466
    else:
        with pytest.raises(ValueError, match=expected):
            trade.execute(body)
        assert game.drags == (1 if expected == UNVERIFIED else 0)
