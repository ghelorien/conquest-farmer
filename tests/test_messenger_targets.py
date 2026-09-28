"""Messengers are killed with left clicks, not kept at a distance.

Alex 2026-09-28 17:37, with Toxic's and Suicide's gear upgraded: "if there
ever is a messenger version of the monster just kill it with left clicks".
The RatMessenger (2,000 HP) and ElfMessenger (3,600 HP) join their fields'
families as targets; Aides and Kings stay bosses. Every other target keeps
jump-Scatter ("no more left clicks", level 23).
"""

import pytest
import yaml

from conquest.farmer_profile import CombatSpeed
from conquest.routes import RouteLibrary, boss_name, messenger, route_monster_names
from conquest.trial import TrialConfig, choose_target, messenger_target, scatter_attack_mode
from conquest.vision import Target


def config(**update):
    base = TrialConfig.model_validate(
        yaml.safe_load(open("profiles/desktop-foreground.example.yaml"))
    )
    return base.model_copy(
        update={
            "monster": "Ratling",
            "monster_variants": ("FireRatL38", "RatMessenger"),
            "boundary": (500, 500, 700, 700),
            "attack_range_tiles": 8,
            "single_attack_range_tiles": 12,
            "jump_scatter": True,
            **update,
        }
    )


def test_messengers_are_targets_on_their_routes_and_aides_stay_bosses():
    assert messenger("RatMessenger") and messenger("ElfMessenger")
    assert not messenger("RatAide") and not messenger("Ratling")
    assert not boss_name("RatMessenger") and not boss_name("ElfMessenger")
    assert boss_name("RatAide") and boss_name("ElfAide") and boss_name("ElfBoss")
    assert "RatMessenger" in route_monster_names(RouteLibrary().load("ratling"))
    assert "ElfMessenger" in route_monster_names(RouteLibrary().load("firespirit"))


def test_a_messenger_is_shot_with_left_clicks_even_with_forced_jump_scatter():
    speed = CombatSpeed(force_jump_scatter=True)
    cfg = config()
    assert scatter_attack_mode(cfg, speed, None, False, "RatMessenger") == "left"
    assert scatter_attack_mode(cfg, speed, None, False, "Ratling") == "right"


def test_a_messenger_in_range_is_focused_before_the_nearer_pack():
    cfg = config()
    ratling = Target("Ratling", 600, 400, 1, 1, 1000, (602, 600), 900)
    far_messenger = Target("RatMessenger", 700, 400, 1, 2, 2000, (611, 600), 2000)
    # 11 tiles: beyond Scatter's 8, inside the 12-tile left-click reach.
    assert choose_target([ratling, far_messenger], 1, (600, 600), cfg, 0) == ratling
    assert messenger_target([ratling, far_messenger], 1, (600, 600), cfg, 0) == far_messenger
    # 13 tiles: out of reach, so the pack keeps the Scatter.
    too_far = Target("RatMessenger", 700, 400, 1, 3, 3000, (613, 600), 2000)
    assert messenger_target([ratling, too_far], 1, (600, 600), cfg, 0) is None


@pytest.mark.parametrize("route_id,kind", [("ratling", 8103), ("firespirit", 8104)])
def test_the_route_sends_the_messenger_type_to_the_combat_targets(route_id, kind):
    assert kind in RouteLibrary().load(route_id).monster_type_ids
