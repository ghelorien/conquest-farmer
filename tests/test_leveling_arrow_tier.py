"""A leveling archer carries the best arrow tier its wallet can pay for.

Live 2026-09-27 21:34-21:45 (Toxic, level 32, Bandits): IronArrows (4,800 per
1,000) nearly doubled damage per Scatter but cost ~260 silver a minute against
~135 picked up. For a while five packs were required; then Alex (22:36):
"Use iron arrows from now on." One affordable pack is enough, and a wallet
that cannot pay for it refills LuckyArrows instead of stranding in town.
"""

from types import SimpleNamespace as NS

from conquest import arrow_upgrades as a

PRICES = {1050000: 200, 1050001: 4800, 1050002: 34000}


def test_the_wallet_decides_the_tier(monkeypatch):
    # Alex 22:36: "Use iron arrows from now on" - one affordable pack.
    monkeypatch.setattr(a, "arrow_pack_price", PRICES.get)
    assert a.LEVELING_TIER_PACKS == 1
    assert a.leveling_tier(32, 10_017) == 1050001  # the 21:32 restock's wallet
    assert a.leveling_tier(32, 4_799) == 1050000  # cannot pay for one pack
    assert a.leveling_tier(31, 1_000_000) == 1050000  # Iron needs level 32
    assert a.leveling_tier(80, 33_999) == 1050001
    assert a.leveling_tier(80, 34_000) == 1050002


def test_arrow_pack_prices_come_from_the_recorded_catalog():
    assert a.arrow_pack_price(1050000) == 200
    assert a.arrow_pack_price(1050001) == 4800
    assert a.arrow_pack_price(424242) is None


def test_the_review_keeps_a_leveling_archer_that_cannot_pay_on_luckyarrows(monkeypatch):
    monkeypatch.setattr(a, "arrow_pack_price", PRICES.get)
    monkeypatch.setattr("conquest.equipment.leveling_archer", lambda: True)
    bought = []
    loop = NS(
        town=lambda action, **kw: bought.append(kw) if action == "buy" else {"items": []},
        record=lambda *args, **kw: None,
    )
    products = [
        {"type_id": 1050000, "name": "LuckyArrow", "price": 200, "level": 1,
         "profession": 40, "attack_min": 10, "attack_max": 10},
        {"type_id": 1050001, "name": "IronArrow", "price": 4800, "level": 32,
         "profession": 40, "attack_min": 50, "attack_max": 50},
    ]
    state = {"level": 32, "equipment": {"arrows": {"type_id": 1050000, "attack_min": 10, "attack_max": 10}}}
    a.review_arrows(loop, products, state, 4_000)
    assert bought == []
