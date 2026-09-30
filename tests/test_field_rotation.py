"""A route hold that rotates between fields at each restock.

2026-09-29: GiantApe fields thin from ~1.5-2.4M to ~0.7-1.1M xp/h within
15-30 minutes. After one restock cycle on the south lobe, the rested north
field gave Toxic 2.39M xp/h (20:14-20:24).
"""

from conquest import session_plan as p


def hold(**extra):
    data = {
        "active": True,
        "mode": "hold_route",
        "route_id": "giantape-north",
        "upgrade_maps": [1020],
        "started_at": 123.0,
        "rotation": ["giantape-north", "giantape-south"],
        **extra,
    }
    p.write_json(p.PLAN, data)
    return data


def test_each_town_visit_moves_the_hold_to_the_next_field():
    hold()
    assert p.rotate_hold("visit-1") == "giantape-south"
    assert p.read_json(p.PLAN)["route_id"] == "giantape-south"
    assert p.active_plan()["route_id"] == "giantape-south"
    # A resumed or repeated restock of the same visit keeps its field.
    assert p.rotate_hold("visit-1") is None
    assert p.rotate_hold("visit-2") == "giantape-north"


def test_holds_without_a_rotation_never_move():
    data = hold()
    data.pop("rotation")
    p.write_json(p.PLAN, data)
    assert p.rotate_hold("visit-1") is None
    assert p.read_json(p.PLAN)["route_id"] == "giantape-north"


def test_a_rotation_across_restock_towns_is_refused():
    hold(rotation=["giantape-north", "wingedsnake-west"])
    assert p.rotate_hold("visit-1") is None
    assert p.read_json(p.PLAN)["route_id"] == "giantape-north"


def test_a_hold_off_its_own_rotation_is_left_alone():
    hold(route_id="macaque", rotation=["giantape-north", "giantape-south"])
    assert p.rotate_hold("visit-1") is None


def test_the_restock_rotates_before_its_town_work(monkeypatch):
    from types import SimpleNamespace

    from conquest.overnight import OvernightLoop
    from conquest.routes import RouteLibrary

    hold()
    events = []
    loop = OvernightLoop.__new__(OvernightLoop)
    loop.route = RouteLibrary().load("giantape-north")
    loop.identity = {"pid": 1}
    loop.town_visit = SimpleNamespace(begin=lambda *a, **k: None, active_id=lambda: "visit-9")
    loop.record = lambda event, **fields: events.append((event, fields))

    class Stop(Exception):
        pass

    def stop(*a, **k):
        raise Stop

    monkeypatch.setattr("conquest.savings.savings_plan", stop)
    try:
        loop.restock()
    except Stop:
        pass
    assert events[0][0] == "field_rotated" and events[0][1]["route"] == "giantape-south"
    assert p.read_json(p.PLAN)["rotated_for_visit"] == "visit-9"
