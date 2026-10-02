"""Alex's gear rule and the broken-bow fixes (2026-10-02).

04:01 Toxic's HerderBow broke at 0 durability and vanished. Its gear circuit
bought an IronBow (Strength 41, Agility 141) that the client would not equip
("Equip unverified"), urgent banking stored it a minute later because every
carried bow counts as urgent, and the farm runner failed on "Pointer is
null" for 40 minutes. Alex then: "I don't want you to look at replacing your
gear unless the gear [doesn't work] anymore."
"""

import json
import struct
from types import SimpleNamespace as NS

import pytest

from conquest import equipment, level_goal, town_trade, valuables
from conquest.overnight import require_bow

HERDER = dict(type_id=500105, name="HerderBow", level=60, profession=40, sex=0,
              attack_min=270, attack_max=330, defense=0, dodge=0, price=14600)
IRON = dict(type_id=500115, name="IronBow", level=65, profession=40, sex=0,
            attack_min=287, attack_max=352, defense=0, dodge=0, price=20168)
WORN = dict(uid=1, type_id=500095, name="LongBow", level=55, plus=0, gem1=0, gem2=0,
            attack_min=200, attack_max=250, defense=0, dodge=0)
# Suicide's read at level 64 (owner record +0x3D8..+0x3F0).
SUICIDE = {"strength": 40, "spirit": 0, "agility": 139, "vitality": 20}
NEEDS = {500105: (38, 130), 500115: (41, 141), 500095: (35, 120)}


def state(bow=None, attributes=SUICIDE, level=65):
    return {
        "level": level,
        "profession": 40,
        "equipment": {} if bow is None else {"bow": bow},
        "attributes": attributes,
    }


def test_an_empty_bow_slot_gets_only_a_bow_the_character_can_equip(monkeypatch):
    monkeypatch.setattr(equipment, "client_requirements", lambda: NEEDS)
    assert equipment.upgrade_reason(IRON, state()) == "Strength or agility below the requirement"
    assert equipment.upgrade_reason(HERDER, state()) is None
    chosen = equipment.choose_upgrades([HERDER, IRON], state(), 100000, 3000)
    assert [p["name"] for p in chosen] == ["HerderBow"]
    # Without a verified attribute read the old rule stands.
    assert equipment.upgrade_reason(IRON, state(attributes=None)) is None


def test_with_upgrades_off_working_gear_stays_and_an_empty_slot_is_filled(monkeypatch):
    monkeypatch.setattr(equipment, "client_requirements", lambda: NEEDS)
    assert equipment.upgrade_reason(HERDER, state(bow=WORN)) is None  # default: upgrades
    equipment.POLICY.write_text(json.dumps({"upgrades": False}))
    assert equipment.upgrade_reason(HERDER, state(bow=WORN)) == "Keeping working gear (upgrades off)"
    assert equipment.choose_upgrades([HERDER, IRON], state(bow=WORN), 100000, 3000) == []
    assert equipment.upgrade_reason(HERDER, state()) is None


def test_level_goal_makes_no_gear_trips_with_upgrades_off():
    level_goal.start(110)
    level_goal.mark_reviewed(60)
    assert level_goal.due(66) == "gear"
    equipment.POLICY.write_text(json.dumps({"upgrades": False}))
    assert level_goal.due(66) is None
    assert level_goal.due(110) == "reached"


def test_the_attribute_read_checks_out_against_the_base_hp():
    # Strength, spirit, agility, vitality, two other fields, base max HP.
    assert equipment.parse_attributes(struct.pack("<7I", 40, 0, 139, 20, 0, 67, 1017)) == SUICIDE
    assert equipment.parse_attributes(struct.pack("<7I", 40, 0, 139, 20, 0, 67, 1000)) is None
    assert equipment.parse_attributes(struct.pack("<7I", 40, 9999, 139, 20, 0, 67, 30993)) is None


def test_gear_bought_to_wear_is_never_banked_or_sold(tmp_path, monkeypatch):
    journal = tmp_path / "equipment-upgrades.json"
    monkeypatch.setattr(equipment, "upgrades_journal", lambda: journal)
    bought = {"uid": 297638648, "type_id": 500115, "slot": 3, "plus": 0}
    looted = {"uid": 11, "type_id": 500118, "slot": 4, "plus": 0}
    assert valuables.urgent_storage(bought)  # every carried bow, as before
    journal.write_text(json.dumps([
        {"level": 65, "type_id": 500115, "uid": 297638648, "state": "deferred",
         "detail": "Equip unverified; item preserved and no repeat input issued"},
    ]))
    assert not valuables.urgent_storage(bought)
    assert not town_trade.sale_candidate(bought)
    assert valuables.urgent_storage(looted)
    assert valuables.urgent_storage({"uid": 12, "type_id": 1088000, "slot": 5})  # DragonBall


def test_a_missing_bow_shops_before_the_hunt():
    calls = []
    gear = {"equipment": {"armor": {"uid": 2}}}

    def restock():
        calls.append("restock")
        gear["equipment"]["bow"] = {"uid": 3}

    loop = NS(town=lambda action: gear, record=lambda event, **f: calls.append(event), restock=restock)
    require_bow(loop)
    assert calls == ["bow_missing", "restock"]
    calls.clear()
    require_bow(loop)
    assert calls == []
    gear["equipment"].pop("bow")
    loop.restock = lambda: None
    with pytest.raises(ValueError, match="No bow"):
        require_bow(loop)
    # An unreadable or unqualified gear read leaves the start to the runner.
    require_bow(NS(town=lambda action: {"ok": True}, record=None, restock=None))
