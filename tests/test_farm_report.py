"""scripts/farm_report.py splits route events into trips and prices them."""

import importlib.util
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("farm_report", REPO / "scripts" / "farm_report.py")
farm_report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(farm_report)


def hunting(t, arrows, potions, silver, kills, level, xp, required=244190):
    return {
        "time": t,
        "event": "hunting",
        "supplies": {"arrows": arrows, "potions": potions, "silver": silver},
        "kills": kills,
        "experience": {"level": level, "experience_candidate": xp, "experience_required": required},
    }


def test_a_bandit_trip_on_ironarrows(tmp_path):
    rows = [
        {"time": 0, "event": "purchase", "receipt": {"bought": 1050001, "price": 4800}},
        {"time": 1, "event": "purchase", "receipt": {"bought": 1000020, "price": 60}},
        {"time": 2, "event": "arrows_upgraded", "receipt": {"type_id": 1050001}},
        {"time": 3, "event": "restock_complete", "supplies": {"arrows": 1000, "potions": 20, "silver": 200}},
        {"time": 4, "event": "level_bracket_checked", "level_bracket": "bandit"},
        hunting(60, 1000, 20, 200, 0, 32, 240000),
        # A level-up: the rest of level 32 (4,190) plus 5,000 into level 33.
        hunting(600, 460, 18, 1641, 82, 33, 5000),
        {"time": 700, "event": "travel_revive"},
        {"time": 800, "event": "restock_complete", "supplies": {"arrows": 1000, "potions": 20, "silver": 200}},
    ]
    path = tmp_path / "reports" / "overnight" / "events.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    monkey_now = rows[-1]["time"] + 60
    report = [
        farm_report.summarize(trip, farm_report.prices(rows)) for trip in farm_report.trips(rows)
    ]
    assert len(report) == 1
    trip = report[0]
    assert trip["route"] == "bandit" and trip["level"] == "32-33"
    assert trip["kills"] == 82 and trip["deaths"] == 1
    minutes = (600 - 3) / 60
    assert trip["xp_per_min"] == round((4190 + 5000) / minutes)
    # 540 IronArrows at 4.8 and 2 potions at 60 against 1,441 picked up.
    assert trip["supply_cost"] == round(540 * 4.8 + 2 * 60)
    assert trip["net_per_min"] == round((1441 - (540 * 4.8 + 120)) / minutes)


def test_a_held_route_is_not_relabelled_by_the_next_bracket():
    # Live 2026-09-28: Toxic reached 36 under a Bandit route hold; the bracket
    # check named Ratlings, which it never hunted.
    rows = [
        {"time": 0, "event": "supply_plan", "route": "bandit"},
        {"time": 1, "event": "restock_complete", "supplies": {"arrows": 1000, "potions": 21, "silver": 200}},
        hunting(60, 1000, 21, 200, 0, 35, 240000),
        {"time": 100, "event": "route_hold_active", "route_hold": {"mode": "hold_route", "route_id": "bandit"}},
        {"time": 200, "event": "level_bracket_checked", "level_bracket": "ratling"},
        hunting(600, 500, 19, 1400, 60, 36, 5000),
        {"time": 700, "event": "restock_complete", "supplies": {"arrows": 1000, "potions": 21, "silver": 200}},
        hunting(760, 1000, 21, 200, 60, 36, 6000),
        {"time": 800, "event": "level_route_changed", "route": "ratling"},
        hunting(900, 900, 21, 300, 70, 36, 9000),
    ]
    routes = [trip["route"] for trip in farm_report.trips(rows)]
    assert routes == ["bandit", "bandit>ratling"]
