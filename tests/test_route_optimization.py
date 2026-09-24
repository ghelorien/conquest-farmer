import pytest

from conquest.route_optimization import (
    comparison_plan,
    fixed_route_windows,
    queue_area,
    rank_candidates,
)
from conquest.routes import RouteLibrary


def sample(rid, index, kills, seconds=900, **changes):
    return dict(
        sample_id=f"{rid}-{index}",
        route_id=rid,
        verified_kills=kills,
        elapsed_seconds=seconds,
        memory_verified=True,
        comparable=True,
        deaths=0,
        **changes,
    )


@pytest.mark.parametrize("count,minutes", [(2, 75), (3, 105), (4, 120)])
def test_plan_repeats_every_route_within_one_to_two_hours(count, minutes):
    ids = [f"route-{i}" for i in range(count)]
    plan = comparison_plan(ids)
    stages = [s["route_id"] for s in plan["stages"] if s["kind"] == "sample"]
    assert plan["minutes"] == minutes
    assert all(stages.count(rid) == 2 for rid in ids)
    assert all(a != b for a, b in zip(stages, stages[1:]))


def test_queue_does_not_restart_comparison_for_alternative_same_area(tmp_path):
    route = RouteLibrary().load("bandit")
    path = tmp_path / "state.json"
    first = queue_area(route, path=path, now=1)
    second = queue_area(route.model_copy(update={"id": "alternate"}), path=path, now=2)
    assert first == second
    other = queue_area(RouteLibrary().load("turtledove"), path=path, now=3)
    assert other["area_key"] != first["area_key"]
    assert other["phase"] == "needs_survey"


def test_long_downtime_is_included_in_pooled_rate():
    rows = [
        sample("a", 1, 300),
        sample("a", 2, 300, 1800),
        sample("b", 1, 240),
        sample("b", 2, 240),
    ]
    result = rank_candidates(rows, ["a", "b"])
    assert result["winner"] == "b"
    assert result["ranking"][0]["kills_per_hour"] == 960
    assert result["ranking"][1]["kills_per_hour"] == 800


def test_short_burst_and_missing_comparison_cannot_select_winner():
    rows = [
        sample("a", 1, 500),
        sample("a", 2, 500),
        sample("b", 1, 0),
        sample("b", 2, 100, 300),
    ]
    result = rank_candidates(rows, ["a", "b"])
    assert result["winner"] is None
    assert result["pending_candidates"] == ["b"]


def test_unsafe_fast_route_is_rejected_even_when_sample_interrupted():
    rows = [
        sample("a", 1, 100),
        sample("a", 2, 100),
        sample("b", 1, 500, 300, unsafe=True),
    ]
    result = rank_candidates(rows, ["a", "b"])
    assert result["winner"] == "a"
    assert result["rejected"] == [
        {"route_id": "b", "eligible": False, "reason": "unsafe"}
    ]


def test_duplicate_or_unverified_results_cannot_qualify():
    rows = [
        sample("a", 1, 500),
        sample("a", 2, 500),
        sample("b", 1, 0),
        sample("b", 2, 0),
    ]
    rows[1]["memory_verified"] = False
    assert rank_candidates(rows, ["a", "b"])["winner"] is None
    with pytest.raises(ValueError, match="Duplicate"):
        rank_candidates(rows + [rows[0]], ["a", "b"])


def test_zero_kill_windows_are_kept():
    rows = [sample("a", 1, 0), sample("a", 2, 0), sample("b", 1, 1), sample("b", 2, 0)]
    result = rank_candidates(rows, ["a", "b"])
    assert result["winner"] == "b"
    assert result["ranking"][1]["kills_per_hour"] == 0


def test_fixed_windows_keep_downtime_and_do_not_count_partial_or_boundary_twice():
    kills = [(100, 10), (999, 2), (1000, 3), (2800, 20)]
    windows = fixed_route_windows(kills, started_at=100, now=2900)
    assert [w["kills"] for w in windows] == [12, 3, 0]
    assert [w["seconds"] for w in windows] == [900, 900, 900]
    assert windows[0]["kills_per_minute"] == 0.8
    assert fixed_route_windows(kills, started_at=100, now=999) == []


def test_fixed_windows_keep_latest_complete_intervals_and_verified_increments_only():
    kills = [(0, 4), (1800, 5), (1801, True), (1802, 33), (1803, -1), (1804, 2.5)]
    windows = fixed_route_windows(kills, started_at=0, now=2700, retain=2)
    assert [w["start"] for w in windows] == [900, 1800]
    assert [w["kills"] for w in windows] == [0, 5]
