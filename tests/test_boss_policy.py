"""Per-character boss tiers the farmer farms through (.runtime/boss-policy.json).

Alex 2026-10-01 ~18:5x, watching Suicide (level 63) leave the Poltergeist
field to a boss: "you shouldnt be scared by kings and messengers they do 0
damage. Be bold maximize farming." Every keep-away (escapes, boss-chase
returns, travel detours, loot deferrals) asks routes.boss_name, so an ignored
tier stops counting as a boss there. Without the file nothing changes.
"""

import json

import pytest

from conquest import routes


def policy(tiers):
    routes.BOSS_POLICY.write_text(json.dumps({"ignore": tiers}), encoding="utf-8")
    routes._boss_policy_cache[:] = [-1e9, frozenset()]


def test_without_a_policy_every_boss_tier_is_still_a_boss():
    for name in ("BanditKing", "BanditMessenger", "GiantApeMsgr", "BanditAide", "CateranBoss"):
        assert routes.boss_name(name), name
    assert not routes.boss_name("Bandit") and not routes.boss_name("HawKing")
    assert routes.boss_tier("RatMessenger") == "messenger"
    assert routes.boss_tier("HumanAide") == "aide"
    assert routes.boss_tier("WingedSnakeKing") == "king"
    assert routes.boss_tier("Poltergeist") is None


def test_ignored_tiers_are_no_bosses_and_the_rest_still_are():
    policy(["king", "messenger"])
    assert not routes.boss_name("BanditKing") and not routes.boss_name("CateranBoss")
    assert not routes.boss_name("RatMessenger") and not routes.boss_name("GiantApeMsgr")
    assert routes.boss_name("BanditAide")
    assert not routes.king_tier("BanditKing")
    policy(["king", "messenger", "aide"])
    assert not any(
        routes.boss_name(n) for n in ("BanditKing", "BanditMessenger", "BanditAide", "HumanAide")
    )


@pytest.mark.parametrize("content", ["{not json", '{"ignore": "king"}', '{"ignore": ["dragon"]}'])
def test_a_malformed_or_unknown_policy_ignores_nothing(content):
    routes.BOSS_POLICY.write_text(content, encoding="utf-8")
    routes._boss_policy_cache[:] = [-1e9, frozenset()]
    assert routes.boss_name("BanditKing") and routes.boss_name("BanditMessenger")


def test_keep_away_checks_farm_through_ignored_bosses():
    from types import SimpleNamespace

    king = SimpleNamespace(name="BanditKing", position=(102, 100), level=32)
    assert routes.near_boss((100, 100), [king])
    policy(["king", "messenger", "aide"])
    assert not routes.near_boss((100, 100), [king])
