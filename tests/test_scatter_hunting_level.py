"""An archer with Scatter hunts a few levels below its bracket.

Live 2026-09-27 (Toxic, level 26, fresh Scatter): Poltergeists (22-26) gave
14-16 kills a minute on 0.9 potions a minute; the bracket rule then sent it
to WingedSnakes (27-31) for 2.3 kills a minute, 187 Scatters for 12 kills,
out of arrows in five minutes.
"""

from conquest import leveling_routes, scatter_training
from conquest.discord_notify import write_json
from conquest.leveling_routes import desired_route, hunting_level, scatter_hunting_level


def route_at(level):
    return desired_route(level)[0].id


def test_scatter_keeps_poltergeists_three_levels_longer():
    assert route_at(hunting_level(26)) == "wingedsnake"  # single shots move on
    for level in (26, 27, 28):
        assert route_at(scatter_hunting_level(level)) == "poltergeist", level
    assert route_at(scatter_hunting_level(29)) == "wingedsnake"
    assert scatter_hunting_level(2) == hunting_level(1)  # never below level 1
    assert leveling_routes.SCATTER_LEVEL_OFFSET == 3


def test_scatter_farming_is_remembered_once_known(monkeypatch):
    from conquest.overnight import OvernightLoop

    loop = OvernightLoop.__new__(OvernightLoop)
    reads = []

    def learned(loop):
        reads.append(1)
        return False

    monkeypatch.setattr(scatter_training, "learned", learned)
    assert loop.scatter_farming() is False
    assert loop.scatter_farming() is False and len(reads) == 1  # once a minute
    loop.scatter_checked_until = 0
    write_json(scatter_training.STATE, {"learned_at": 1})
    assert loop.scatter_farming() is True
    write_json(scatter_training.STATE, {})
    assert loop.scatter_farming() is True  # known now: never asked again
