"""Boss and elite names: never targets, kept BOSS_CLEARANCE tiles away.

Failure modes, written before the change:
1. The elite tiers in the leveling fields are not bosses: RatAide (8203) and
   RatMessenger (8103) share the Ratling field, BanditAide (8202) the Bandit
   one; the family tests already exclude 8102/8202/8302 as bosses.
2. An ordinary leveling family is taken for a boss because of its name's
   ending (HawKing, levels 92-96, would never be hunted).
3. Kings stop being bosses.
4. A Messenger its field's family targets (Alex 2026-09-28: "if there ever
   is a messenger version of the monster just kill it with left clicks") is
   still kept at a distance, or one no family lists is not.
"""

import pytest

from conquest.routes import BOSS_CLEARANCE, boss_name, near_boss


@pytest.mark.parametrize(
    "name",
    ["RatAide", "ElfAide", "BanditAide", "BanditMessenger", "WingedSnakeKing", "BanditKing", "ElfBoss"],
)
def test_elites_and_kings_are_bosses(name):
    # 1, 3, 4
    assert boss_name(name)


@pytest.mark.parametrize(
    "name",
    [
        "HawKing",
        "Ratling",
        "FireRatL38",
        "Bandit",
        "BanditL33",
        "WingedSnakeL28",
        "RatMessenger",
        "ElfMessenger",
    ],
)
def test_ordinary_monsters_are_not_bosses(name):
    # 2, 4
    assert not boss_name(name)


def test_an_aide_gets_the_boss_clearance():
    from types import SimpleNamespace as NS

    aide = NS(name="RatAide", position=(550, 480))
    assert near_boss((550 + BOSS_CLEARANCE, 480), [aide])
    assert not near_boss((550 + BOSS_CLEARANCE + 1, 480), [aide])
    assert not near_boss((551, 480), [NS(name="Ratling", position=(550, 480))])
