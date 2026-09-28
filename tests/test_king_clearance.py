"""A route can keep King-tier bosses further off than Aides and Messengers.

The RatKing roamed the Ratling field and trailed Suicide at 13-16 tiles,
twice closing to 3 (2026-09-28 12:35-12:52); RatAides and RatMessengers are
too many for a wide berth around each.
"""

from types import SimpleNamespace as NS

import numpy as np
import pytest

from conquest.routes import (
    BOSS_CLEARANCE,
    BOSS_ROOM,
    boss_clearance,
    boss_room,
    boss_zone,
    king_tier,
    near_boss,
)


def test_only_the_king_tier_takes_the_route_clearance():
    assert king_tier("RatKing") and king_tier("WingedSnakeKing")
    assert not king_tier("RatAide") and not king_tier("RatMessenger")
    assert not king_tier("Ratling") and not king_tier("HawKing")  # a leveling family
    assert boss_clearance("RatKing", 15) == 15
    assert boss_clearance("RatAide", 15) == BOSS_CLEARANCE
    assert boss_clearance("RatKing") == BOSS_CLEARANCE  # routes default to 9
    assert boss_clearance("RatKing", 5) == BOSS_CLEARANCE  # never below 9


def test_near_boss_and_room_follow_each_bosses_clearance():
    king = NS(name="RatKing", position=(20, 20))
    aide = NS(name="RatAide", position=(60, 60))
    at_12 = (32, 20)
    assert not near_boss(at_12, (king,))
    assert near_boss(at_12, (king,), king_clearance=15)
    assert not near_boss((72, 60), (aide,), king_clearance=15)  # 12 from an Aide
    assert near_boss(at_12, (king,), clearance=12)  # an explicit reach still wins
    assert boss_room((20 + BOSS_ROOM, 20), (king,))
    assert not boss_room((20 + BOSS_ROOM, 20), (king,), king_clearance=15)
    assert boss_room((20 + 15 + BOSS_ROOM - BOSS_CLEARANCE, 20), (king,), king_clearance=15)


def test_route_travel_zone_widens_for_kings_only():
    king = {"name": "RatKing", "position": [50, 50]}
    aide = {"name": "RatAide", "position": [90, 50]}
    zone = boss_zone((0, 0), (200, 200), [king, aide], king_clearance=15)
    assert (50 + 15, 50) in zone and (50 + 16, 50) not in zone
    assert (90 + BOSS_CLEARANCE, 50) in zone and (90 + BOSS_CLEARANCE + 1, 50) not in zone
    assert (50 + 12, 50) not in boss_zone((0, 0), (200, 200), [king])


def test_saved_route_defaults_to_the_ordinary_clearance():
    from conquest.routes import RouteLibrary

    route = RouteLibrary().load("wingedsnake")
    assert route.king_clearance == BOSS_CLEARANCE
    with pytest.raises(ValueError):
        route.model_validate({**route.model_dump(), "king_clearance": 5})
    # The Ratling field's King roams and follows: a wider berth there only.
    assert RouteLibrary().load("ratling").king_clearance == 15


def test_escape_leaves_a_king_inside_the_route_clearance(monkeypatch):
    from test_native_farm import setup
    from conquest import native_farm

    supervisor, _, _, _ = setup(monkeypatch)
    monkeypatch.setattr(native_farm.time, "monotonic", lambda: 100.0)
    supervisor.scene_timestamp = 100.0
    supervisor.recovery.terrain = NS(walkable=lambda p: True)
    kwargs = dict(adjacent_trigger=1, reach=native_farm.JUMP_SCATTER_REACH, scatter_range=8)
    supervisor.escape_monsters = (NS(position=(32, 20), name="RatKing"),)
    # 12 tiles: fine on an ordinary route...
    assert supervisor.ranged_escape((20, 20), (0, 0, 60, 60), **kwargs) is None
    # ...but inside a 15-tile King clearance, and never landing within it.
    supervisor.king_clearance = 15
    landing = supervisor.ranged_escape((20, 20), (0, 0, 60, 60), **kwargs)
    assert landing is not None
    assert max(abs(landing[0] - 32), abs(landing[1] - 20)) > 15
    assert supervisor.escape_context["reason"] == "boss_nearby"
    # An Aide at the same distance keeps the ordinary clearance.
    supervisor.escape_monsters = (NS(position=(32, 20), name="RatAide"),)
    assert supervisor.ranged_escape((20, 20), (0, 0, 60, 60), **kwargs) is None


def test_scatter_landing_keeps_the_route_clearance_from_a_king():
    from conquest.navigation import TerrainMap
    from conquest.scatter_movement import scatter_landing

    terrain = TerrainMap(1011, 100, 100, np.zeros((100, 100), dtype=bool), "", (), ())
    king = NS(name="RatKing", position=(70, 50))
    supervisor = NS(
        recovery=NS(terrain=terrain), escape_monsters=(king,), king_clearance=15
    )
    targets = [
        NS(world_position=(x, y), current_hp=100)
        for x, y in ((62, 50), (63, 51), (62, 49), (38, 50))
    ]
    supervisor.king_clearance = BOSS_CLEARANCE
    ordinary = scatter_landing(supervisor, targets, (50, 50), (20, 20, 80, 80), 10)
    assert BOSS_CLEARANCE < max(abs(ordinary[0] - 70), abs(ordinary[1] - 50)) <= 15
    supervisor.king_clearance = 15
    landing = scatter_landing(supervisor, targets, (50, 50), (20, 20, 80, 80), 10)
    assert landing is not None and landing != ordinary
    assert max(abs(landing[0] - 70), abs(landing[1] - 50)) > 15
