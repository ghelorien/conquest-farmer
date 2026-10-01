"""Twin City as a Market banking origin (profiles/meteor-banking.json 1002).

Alex 2026-10-01 ~14:50, after Twin City's warehouse filled with loose Meteors
and Suicide's Twin City fields moved their restocks to Phoenix: "can you go to
market warehouse instead?" Meteor packing and warehouse overflow need a
verified Market round trip from the origin town, and only Phoenix had one.
Twin City's Conductress rides through her own memory-identified path
(conductress.TWIN_CONDUCTRESS); the Market exit is the Phoenix leg's.
"""

import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from conquest import conductress as c
from conquest import meteor_banking as m


def origins():
    return json.loads(Path("profiles/meteor-banking.json").read_text(encoding="utf-8"))["origins"]


def test_the_twin_city_origin_rides_her_market_choice_and_the_phoenix_exit():
    tc, phoenix = origins()["1002"], origins()["1011"]
    out = tc["outbound"]
    assert (out["via"], out["source_map"], out["destination_map"]) == ("twin_conductress", 1002, 1036)
    assert (out["option"], out["fare"], out["approach"], out["verified"]) == ("Market", 100, [438, 444], True)
    assert tuple(c.TWIN_CONDUCTRESS.position) == (435, 440)
    back = tc["return"]
    assert (back["source_map"], back["destination_map"], back["verified"]) == (1036, 1002, True)
    for key in ("npc", "identity", "approach", "dialogs", "waypoints", "fare"):
        assert back[key] == phoenix["return"][key]


def test_market_is_one_of_her_checked_choices():
    records = [
        {"kind": 0, "option": 0, "text": "Where are you heading? I can teleport you for a price of 100 silver."}
    ] + [
        {"kind": 1, "option": i, "text": name}
        for i, name in enumerate(
            ["Phoenix Castle", "Desert City", "Ape Mountain", "Bird Island.", "Mine Cave", "Market", "Just passing by."]
        )
    ]
    c.validate_destination({"records": records}, "Market")
    with pytest.raises(ValueError, match="Unsupported"):
        c.validate_destination({"records": records}, "Mine Cave")
    records[6]["text"] = "Market Place"
    with pytest.raises(ValueError, match="differ"):
        c.validate_destination({"records": records}, "Market")


@pytest.fixture
def twin_city(monkeypatch):
    calls, world = [], {"map": 1002, "silver": 1000, "fare": 100}

    def town(action, **fields):
        calls.append((action, fields))
        if action == "supplies":
            return {"items": [], "silver": world["silver"]}
        if action == "conductress-travel":
            world["map"], world["silver"] = 1036, world["silver"] - world["fare"]
            return {"destination_selected": fields["destination"]}
        return {}

    def health():
        return {
            "embedded_controls": {
                "observed_at": m.time.time(),
                "life": {
                    "map_id": world["map"],
                    "object_address": 7,
                    "dead_candidate": False,
                    "position": [211, 196] if world["map"] == 1036 else [438, 444],
                },
            }
        }

    monkeypatch.setattr(c, "prepare_destination", lambda loop, option: calls.append(("prepare", option)))
    monkeypatch.setattr(
        "conquest.merchants.service_visit.MarketVisit",
        lambda: NS(departed=lambda map_id: calls.append(("departed", map_id))),
    )
    monkeypatch.setattr("conquest.navigation.read_terrain", lambda root, map_id: NS(map_id=map_id))
    monkeypatch.setattr(m.time, "sleep", lambda seconds: None)
    loop = NS(
        living=health,
        health=health,
        town=town,
        travel=lambda point, **kw: calls.append(("travel", tuple(point))),
        record=lambda *a, **kw: None,
        check_stop=lambda: None,
        terrain=NS(map_id=1002),
    )
    return loop, calls, world


def test_a_twin_city_market_trip_pays_her_fare_once_and_arrives(twin_city):
    loop, calls, world = twin_city
    m.trip(loop, origins()["1002"]["outbound"], before_submit=lambda: calls.append(("submit",)))
    names = [call[0] for call in calls]
    assert calls[0] == ("travel", (438, 444))
    assert names.index("conductress-open") < names.index("prepare") < names.index("submit")
    assert calls[names.index("prepare")] == ("prepare", "Market")
    assert names.index("submit") < names.index("conductress-travel")
    assert calls[names.index("conductress-travel")] == ("conductress-travel", {"destination": "Market"})
    assert names.count("conductress-travel") == 1 and ("departed", 1036) in calls
    # The generic saved-service path (exact records) is not used for her.
    assert not {"service-locate", "service-open", "service-select"} & set(names)


def test_a_twin_city_market_trip_with_a_wrong_fare_is_never_repaid(twin_city):
    loop, calls, world = twin_city
    world["fare"] = 200
    with pytest.raises(ValueError, match="fare was not verified"):
        m.trip(loop, origins()["1002"]["outbound"])
    assert [call[0] for call in calls].count("conductress-travel") == 1
