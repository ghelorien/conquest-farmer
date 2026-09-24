import json
from pathlib import Path
import pytest
from conquest.valuables import (
    DRAGONBALL_NAMES,
    DRAGONBALL_TYPES,
    STORAGE_ONLY_TYPES,
    storage_only,
    require_marketable,
)
from conquest.memory_ground import GroundItem, wanted_drop
from conquest.banking import urgent_valuables
from conquest.town_trade import stash_candidate, sale_candidate
from conquest.discard_loot import discard_candidate
from conquest.discord_notify import notable_drop
from conquest.farm_telemetry import item_label


@pytest.mark.parametrize("kind", sorted(DRAGONBALL_TYPES))
def test_each_memory_qualified_dragonball_has_consistent_policy(kind):
    item = {"uid": 12, "type_id": kind, "slot": 0, "plus": 0, "amount": 1, "limit": 1}
    assert wanted_drop(GroundItem(12, 100000, kind, (1, 2)))
    assert urgent_valuables([item]) == [item]
    assert stash_candidate(item)
    assert not sale_candidate(item) and not discard_candidate(item)
    assert notable_drop(item)
    assert DRAGONBALL_NAMES[kind] in item_label(item)
    assert not urgent_valuables([{**item, "slot": None}])


@pytest.mark.parametrize("kind", sorted(STORAGE_ONLY_TYPES))
def test_storage_only_blocks_direct_listing_without_input(kind):
    from conquest.merchants.driver import MerchantDriver

    driver = MerchantDriver.__new__(MerchantDriver)
    item = {"type_id": kind, "name": DRAGONBALL_NAMES[kind]}
    assert storage_only(item)
    with pytest.raises(ValueError, match="storage-only"):
        driver.list_item({}, item, 10000000, lambda: pytest.fail("No input allowed"))


def test_unknown_star_name_protects_storage_but_never_authorizes_pickup():
    item = {"type_id": 2222222, "name": "8-StarDragonBall"}
    assert storage_only(item)
    assert not wanted_drop(GroundItem(1, 100000, item["type_id"], (1, 1)))
    assert not storage_only({"type_id": 2222222, "name": "Dragonball Costume"})


def test_checked_in_catalog_matches_policy():
    data = json.loads(Path("profiles/valuable-items.json").read_text())
    assert {r["type_id"]: r["name"] for r in data["definitions"]} == DRAGONBALL_NAMES
    assert {
        r["type_id"] for r in data["definitions"] if r["storage_only"]
    } == STORAGE_ONLY_TYPES


def test_previous_loot_preferences_are_preserved():
    assert wanted_drop(GroundItem(1, 100000, 1088001, (1, 1)))
    assert wanted_drop(GroundItem(1, 100000, 500009, (1, 1), plus=0))
    assert wanted_drop(GroundItem(1, 100000, 500008, (1, 1), plus=1))
    assert not wanted_drop(GroundItem(1, 100000, 500008, (1, 1), plus=0))
    assert not wanted_drop(GroundItem(1, 100000, 1090000, (1, 1)))
