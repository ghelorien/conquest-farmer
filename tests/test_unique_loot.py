"""Unique and higher gear is picked up, banked and never sold; notable drops
are audited once.

Alex 2026-09-28: "You can pickup and bank any item unique and higher now."
"""

import json
from types import SimpleNamespace

import pytest

from conquest.memory_ground import GroundItem, wanted_drop
from conquest.town_trade import sale_candidate, stash_candidate


def drop(kind, plus=0, uid=1):
    return GroundItem(uid, 0x1000 + uid, kind, (10, 10), plus=plus)


@pytest.mark.parametrize(
    "kind,plus,wanted",
    [
        (150007, 0, True),  # Unique
        (150008, 0, True),  # Elite
        (150009, 0, True),  # Super
        (150006, 0, False),  # Refined
        (150005, 0, False),  # Normal
        (150005, 1, True),  # +1 Normal
        (150005, None, False),  # unreadable plus on Normal gear
        (150007, None, True),  # Unique whatever the plus
    ],
)
def test_ground_pickup_takes_unique_and_higher(kind, plus, wanted):
    assert wanted_drop(drop(kind, plus)) is wanted


# 480xxx: a family outside the always-banked designated gear (120/121/150/...).
@pytest.mark.parametrize("kind", [480007, 480008, 480009, 410017])
def test_unique_and_higher_is_banked_and_never_sold(kind):
    item = {"type_id": kind, "plus": 0, "slot": 3}
    assert stash_candidate(item)
    assert not sale_candidate(item)


def test_refined_plain_gear_is_still_sold_not_banked():
    item = {"type_id": 480006, "plus": 0, "slot": 3}
    assert not stash_candidate(item) and sale_candidate(item)


def test_notable_drops_are_audited_once(tmp_path, monkeypatch):
    from conquest import native_farm

    monkeypatch.setattr(native_farm, "state_path", lambda rel: tmp_path / rel)
    (tmp_path / "reports/desktop-farming").mkdir(parents=True)
    supervisor = SimpleNamespace(map_id=1011)
    audit = native_farm.NativeFarmSupervisor.audit_loot
    scene = [
        drop(150007, 0, uid=1),  # Unique: audited, wanted
        drop(150005, None, uid=2),  # unreadable plus: audited, not wanted
        drop(150005, 0, uid=3),  # plain Normal: not notable
        drop(1090000, 0, uid=4),  # silver: not gear
    ]
    audit(supervisor, scene)
    audit(supervisor, scene)  # the same drops again are not repeated
    lines = (tmp_path / "reports/desktop-farming/loot-audit.jsonl").read_text().splitlines()
    rows = [json.loads(line) for line in lines]
    assert [(r["uid"], r["wanted"]) for r in rows] == [(1, True), (2, False)]
