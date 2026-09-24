from pathlib import Path

import pytest
import yaml

from conquest.progression import LevelMonitor, review_plan


def plan():
    return yaml.safe_load(Path("profiles/leveling-plan.yaml").read_text())


def test_skipped_levels_include_all_pending_reviews_without_qualifying_routes():
    result = review_plan(15, plan())
    assert result["monster_review"] == "Robin"
    assert not result["monster_route_qualified"]
    assert {r["id"] for r in result["due_reviews"]} == {
        "bow_proficiency",
        "dress",
        "archer_promotion",
        "hunting_bow",
    }
    assert "Bamboo Bow" in result["completed_upgrades"][0]
    assert result["next_reviews"][0]["id"] == "scatter"


def test_level_fault_staleness_and_decrease_do_not_display_old_level():
    now, value = [10.0], [9]
    monitor = LevelMonitor(lambda: value[0], plan(), clock=lambda: now[0])
    monitor.sample()
    assert monitor.snapshot()["character_level"] == 9
    value[0] = 8
    monitor.sample()
    assert monitor.snapshot()["character_level"] is None
    value[0] = 10
    monitor.sample()
    assert monitor.snapshot()["character_level"] == 10
    now[0] += 5.1
    assert not monitor.snapshot()["level_valid"]
    value[0] = 999
    monitor.sample()
    assert monitor.snapshot()["character_level"] is None


@pytest.mark.parametrize("level", [0, 141, True, 9.5])
def test_invalid_level_has_no_progression_decision(level):
    with pytest.raises(ValueError):
        review_plan(level, plan())
