"""A route can keep Aides and Messengers further off than BOSS_CLEARANCE.

On the FireSpirit field the ElfAide moves ~2 tiles/s and hit Toxic (633 HP)
for 192 from 6 tiles twice in 1.7 s (2026-09-28 16:36:44): it crossed the
9-tile clearance and fired between two escape checks. Bandits and snakes keep
the ordinary clearance.
"""

from types import SimpleNamespace as NS

import numpy as np
import pytest

from conquest.routes import (
    BOSS_CLEARANCE,
    BOSS_ROOM,
    RouteLibrary,
    boss_clearance,
    boss_room,
    boss_zone,
    near_boss,
)


def test_aides_and_messengers_take_the_elite_clearance():
    assert boss_clearance("ElfAide", 15, 13) == 13
    assert boss_clearance("ElfMessenger", 15, 13) == 13
    assert boss_clearance("ElfBoss", 15, 13) == 15  # King tier
    assert boss_clearance("ElfAide", 15) == BOSS_CLEARANCE  # routes default to 9
    assert boss_clearance("ElfAide", 15, 5) == BOSS_CLEARANCE  # never below 9
    assert boss_clearance("RatKing", 9, 13) == BOSS_CLEARANCE


def test_near_boss_room_and_travel_zone_follow_the_elite_clearance():
    aide = NS(name="ElfAide", position=(60, 60))
    at_12 = (72, 60)
    assert not near_boss(at_12, (aide,), king_clearance=15)
    assert near_boss(at_12, (aide,), king_clearance=15, elite_clearance=13)
    assert boss_room((60 + BOSS_ROOM, 60), (aide,))
    assert not boss_room((60 + BOSS_ROOM, 60), (aide,), elite_clearance=13)
    assert boss_room((60 + 13 + BOSS_ROOM - BOSS_CLEARANCE, 60), (aide,), elite_clearance=13)
    zone = boss_zone((0, 0), (200, 200), [{"name": "ElfAide", "position": [90, 50]}],
                     elite_clearance=13)
    assert (90 + 13, 50) in zone and (90 + 14, 50) not in zone


def test_only_the_fire_spirit_route_widens_it():
    route = RouteLibrary().load("firespirit")
    assert route.elite_clearance == 13
    for other in ("wingedsnake", "ratling", "bandit"):
        assert RouteLibrary().load(other).elite_clearance == BOSS_CLEARANCE
    with pytest.raises(ValueError):
        route.model_validate({**route.model_dump(), "elite_clearance": 5})


def test_the_trial_config_carries_it():
    from conquest.trial import TrialConfig

    assert TrialConfig.model_fields["elite_clearance"].default == BOSS_CLEARANCE


def test_escape_leaves_an_aide_inside_the_elite_clearance(monkeypatch):
    from test_native_farm import setup
    from conquest import native_farm

    supervisor, _, _, _ = setup(monkeypatch)
    monkeypatch.setattr(native_farm.time, "monotonic", lambda: 100.0)
    supervisor.scene_timestamp = 100.0
    supervisor.recovery.terrain = NS(walkable=lambda p: True)
    kwargs = dict(adjacent_trigger=1, reach=native_farm.JUMP_SCATTER_REACH, scatter_range=8)
    supervisor.escape_monsters = (NS(position=(32, 20), name="ElfAide"),)
    # 12 tiles: fine with the ordinary clearance...
    assert supervisor.ranged_escape((20, 20), (0, 0, 60, 60), **kwargs) is None
    # ...but inside a 13-tile elite clearance, and never landing within it.
    supervisor.elite_clearance = 13
    landing = supervisor.ranged_escape((20, 20), (0, 0, 60, 60), **kwargs)
    assert landing is not None
    assert max(abs(landing[0] - 32), abs(landing[1] - 20)) > 13
    assert supervisor.escape_context["reason"] == "boss_nearby"


def test_scatter_landing_keeps_the_elite_clearance_from_an_aide():
    from conquest.navigation import TerrainMap
    from conquest.scatter_movement import scatter_landing

    terrain = TerrainMap(1011, 100, 100, np.zeros((100, 100), dtype=bool), "", (), ())
    aide = NS(name="ElfAide", position=(42, 30))
    supervisor = NS(recovery=NS(terrain=terrain), escape_monsters=(aide,))
    targets = [
        NS(world_position=(x, y), current_hp=100)
        for x, y in ((62, 50), (63, 51), (62, 49), (38, 50))
    ]
    ordinary = scatter_landing(supervisor, targets, (50, 50), (20, 20, 80, 80), 10)
    assert BOSS_CLEARANCE < max(abs(ordinary[0] - 42), abs(ordinary[1] - 30)) <= 13
    supervisor.elite_clearance = 13
    landing = scatter_landing(supervisor, targets, (50, 50), (20, 20, 80, 80), 10)
    assert landing is not None and landing != ordinary
    assert max(abs(landing[0] - 42), abs(landing[1] - 30)) > 13
