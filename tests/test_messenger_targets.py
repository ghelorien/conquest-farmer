"""Messengers are elites to dodge, never targets.

Alex 2026-09-30 07:0x, with Toxic's and Suicide's gear upgraded again: "dont
attack messengers anymore, just dodge them as much as possible and maximize
your exp per hour". This reverses 2026-09-28's "if there ever is a messenger
version of the monster just kill it with left clicks": RatMessenger,
ElfMessenger, MonkeyMessenger, GiantApeMsgr and ThunderApeMsgr leave their
families and routes, so boss_name keeps them at elite_clearance like Aides.
"""

import pytest
import yaml

from conquest.routes import RouteLibrary, boss_name, messenger, route_monster_names
from conquest.trial import TrialConfig, choose_target, messenger_target
from conquest.vision import Target


def config(**update):
    base = TrialConfig.model_validate(
        yaml.safe_load(open("profiles/desktop-foreground.example.yaml"))
    )
    route = RouteLibrary().load("ratling")
    return base.model_copy(
        update={
            "monster": "Ratling",
            "monster_variants": tuple(route_monster_names(route)[1:]),
            "boundary": (500, 500, 700, 700),
            "attack_range_tiles": 8,
            "single_attack_range_tiles": 12,
            "jump_scatter": True,
            **update,
        }
    )


def test_messengers_are_elites_and_no_route_targets_one():
    for name in ("RatMessenger", "ElfMessenger", "MonkeyMessenger", "GiantApeMsgr", "ThunderApeMsgr"):
        assert messenger(name) and boss_name(name)
    assert not messenger("RatAide") and not messenger("Ratling")
    assert boss_name("RatAide") and boss_name("ElfAide") and boss_name("ElfBoss")
    for route in RouteLibrary().all():
        assert not any(messenger(n) for n in route_monster_names(route)), route.id


@pytest.mark.parametrize("route_id,kind", [("ratling", 8103), ("firespirit", 8104),
                                           ("macaque", 8105), ("giantape-north", 8106),
                                           ("thunderape-nw", 8107)])
def test_the_route_keeps_the_messenger_type_out_of_the_combat_targets(route_id, kind):
    assert kind not in RouteLibrary().load(route_id).monster_type_ids


def test_a_messenger_in_range_is_never_chosen_or_focused():
    cfg = config()
    assert "RatMessenger" not in cfg.monster_variants
    ratling = Target("Ratling", 600, 400, 1, 1, 1000, (605, 600), 900)
    near_messenger = Target("RatMessenger", 700, 400, 1, 2, 2000, (601, 600), 2000)
    # The messenger is nearer, yet the pack keeps the Scatter.
    assert choose_target([near_messenger, ratling], 1, (600, 600), cfg, 0) == ratling
    assert messenger_target([ratling, near_messenger], 1, (600, 600), cfg, 0) is None
