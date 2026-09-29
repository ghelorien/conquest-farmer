"""Ground pickups: any +N gear; otherwise Elite and Super, or Unique accessories.

Alex 2026-09-29, for both Suicide and Toxic: "from now on only elite or higher
items unless they are rings, boots, bags, bracelets, necklace", then "Wait
just to confirm I still want all +1's and +2's regardless of (unique, elite
etc)". Quality is a gear type ID's last digit (6 Refined, 7 Unique, 8 Elite,
9 Super).

Failure modes, written before the change:
1. A +1 or +2 piece of gear is left on the ground, whatever its family or
   quality.
2. An unenhanced Unique (or lower) weapon, armor, helmet or earring is still
   picked up.
3. Elite or Super gear of any family is left on the ground.
4. An unenhanced Unique ring, heavy ring, bracelet, necklace, bag or boots is
   left, or a plain one is picked up.
5. Meteors, DragonBalls or other specials stop being picked up.
6. What is already carried stops being banked (town_trade.stash_candidate).
7. Shields (900xxx, outside the 100000-599999 gear range) are never picked
   up, banked, audited or posted, even at +1 or Elite (found by Laptop1).
"""

from types import SimpleNamespace as NS

import pytest

from conquest.memory_ground import wanted_drop


def drop(type_id, plus=0):
    return NS(type_id=type_id, plus=plus)


@pytest.mark.parametrize(
    "type_id, plus",
    [
        (421023, 1),  # +1 normal backsword
        (421027, 2),  # +2 Unique backsword
        (118333, 1),  # +1 normal SteelCoronet
        (133733, 1),  # +1 normal archer coat
        (117323, 2),  # +2 normal earring
        (500053, 1),  # +1 normal SpeedBow
        (530013, 12),  # +12 normal poleaxe
        (150013, 2),  # +2 normal ring
    ],
)
def test_every_plus_item_is_picked_up(type_id, plus):
    # 1
    assert wanted_drop(drop(type_id, plus))


@pytest.mark.parametrize(
    "type_id, plus",
    [
        (421047, 0),  # Unique backsword (picked up 2026-09-28 23:18)
        (133737, 0),  # Unique archer coat
        (117327, 0),  # Unique earring
        (500077, None),  # Unique HornBow, plus unreadable
        (131626, 0),  # Refined armor
        (530013, 13),  # +13 is not a readable enhancement
    ],
)
def test_unenhanced_unique_non_accessories_are_left(type_id, plus):
    # 2
    assert not wanted_drop(drop(type_id, plus))


@pytest.mark.parametrize("type_id", [421048, 421049, 133738, 117329, 500078, 450008, 560009])
def test_elite_and_super_gear_is_picked_up(type_id):
    # 3
    assert wanted_drop(drop(type_id))


@pytest.mark.parametrize(
    "type_id, wanted",
    [
        (150017, True),  # Unique ring
        (151027, True),  # Unique heavy ring
        (152017, True),  # Unique bracelet (picked up 2026-09-28 22:33)
        (120047, True),  # Unique necklace
        (121047, True),  # Unique bag
        (160037, True),  # Unique boots
        (150015, False),  # plain ring
        (120045, False),  # plain necklace
        (160056, False),  # Refined boots
    ],
)
def test_unenhanced_accessories_from_unique_up(type_id, wanted):
    # 4
    assert wanted_drop(drop(type_id)) is wanted


@pytest.mark.parametrize("type_id", [1088001, 1088000, 720027])
def test_meteors_and_dragonballs_stay_wanted(type_id):
    # 5
    assert wanted_drop(drop(type_id))


def test_carried_unique_gear_is_still_banked():
    # 6: the pickup filter narrowed; banking what is carried did not.
    from conquest.town_trade import stash_candidate

    assert stash_candidate({"type_id": 421047, "plus": 0, "slot": 3})
    assert stash_candidate({"type_id": 421023, "plus": 1, "slot": 4})


@pytest.mark.parametrize(
    "type_id, plus, wanted",
    [
        (900005, 1, True),  # +1 Normal shield (Laptop1's case)
        (900007, 0, False),  # plain Unique shield (Laptop1's case)
        (900305, 1, True),  # +1 Normal SoftShield
        (900303, 2, True),  # +2 SoftShield
        (900307, 0, False),  # Unique SoftShield
        (900308, 0, True),  # Elite SoftShield
        (900309, 0, True),  # Super SoftShield
        (900305, 0, False),  # plain SoftShield
        (900305, None, False),  # plain SoftShield, plus unreadable
        (901008, 1, False),  # past the shield family
        (800000, 1, False),  # BladeSoulLv5, not gear
    ],
)
def test_shields_follow_the_non_accessory_rule(type_id, plus, wanted):
    # 7
    assert wanted_drop(drop(type_id, plus)) is wanted


def test_picked_up_shields_are_banked_audited_and_posted(tmp_path, monkeypatch):
    # 7: a shield the filter picks up must also reach storage and the logs.
    import json

    from conquest import native_farm
    from conquest.discord_notify import notable_drop
    from conquest.farm_telemetry import item_label
    from conquest.memory_ground import GroundItem
    from conquest.town_trade import sale_candidate, stash_candidate

    assert stash_candidate({"type_id": 900305, "plus": 1, "slot": 3})
    assert stash_candidate({"type_id": 900308, "plus": 0, "slot": 4})
    assert not stash_candidate({"type_id": 900305, "plus": 0, "slot": 5})
    assert not sale_candidate({"type_id": 900305, "plus": 0, "slot": 5})
    assert notable_drop({"type_id": 900308})
    assert notable_drop({"type_id": 900305, "plus": 1})
    assert not notable_drop({"type_id": 900305, "plus": 0})
    label = item_label({"type_id": 900308, "plus": 1})
    assert label.startswith("Elite ") and label.endswith(" +1")

    monkeypatch.setattr(native_farm, "state_path", lambda rel: tmp_path / rel)
    (tmp_path / "reports/desktop-farming").mkdir(parents=True)
    native_farm.NativeFarmSupervisor.audit_loot(
        NS(map_id=1011),
        [
            GroundItem(1, 0x1001, 900305, (10, 10), plus=1),
            GroundItem(2, 0x1002, 900305, (11, 10), plus=0),
        ],
    )
    lines = (tmp_path / "reports/desktop-farming/loot-audit.jsonl").read_text()
    assert [(r["uid"], r["wanted"]) for r in map(json.loads, lines.splitlines())] == [
        (1, True)
    ]
