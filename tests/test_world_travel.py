from conquest import world_travel as w
import json
from pathlib import Path
from dataclasses import dataclass
from types import SimpleNamespace
import numpy as np
import pytest
from conquest.navigation import TerrainMap
from conquest.world_travel import connection_path, approach_portal


def test_only_verified_connections_form_a_returnable_path():
    edges = [
        {"source_map": 1002, "destination_map": 1011, "verified": True},
        {"source_map": 1011, "destination_map": 1002, "verified": True},
        {"source_map": 1002, "destination_map": 1020, "verified": False},
    ]
    assert connection_path(1011, 1002, edges) == [edges[1]]
    assert connection_path(1011, 1011, edges) == []
    with pytest.raises(ValueError):
        connection_path(1011, 1020, edges)


def test_portal_guard_is_only_entered_by_an_explicit_known_portal_action(monkeypatch):
    from conquest import route_input

    @dataclass
    class Life:
        position: tuple = (7, 10)
        map_id: int = 1002
        dead_candidate: bool = False

    calls = []
    monkeypatch.setattr(
        route_input.EmbeddedRecoveryInput,
        "send",
        lambda self, *args: calls.append(args),
    )
    terrain = TerrainMap(
        1002, 20, 20, np.zeros((20, 20), dtype=bool), "", ((10, 10, 1),), ()
    )
    terrain.blocked[9:12, 9:12] = True
    send = route_input.RouteJumpInput(
        SimpleNamespace(
            adapter=None,
            health_layout=None,
            character="Parasite",
            read_life=lambda: Life(),
        ),
        terrain,
    )
    body = {
        "source": [7, 10],
        "destination": [10, 10],
        "map_id": 1002,
        "expires_at": 999,
    }
    with pytest.raises(ValueError):
        send(body)
    assert send({**body, "portal_id": 1})["movement"] == "run"
    with pytest.raises(ValueError):
        send({**body, "portal_id": 2})
    assert len(calls) == 1
    point, portal = approach_portal(terrain, (7, 10), 1)
    assert terrain.walkable(point) and portal == (10, 10, 1)


def test_level_transition_requires_return_path_then_selects_new_route(monkeypatch):
    from conquest import overnight, world_travel, leveling_routes
    from conquest.routes import RouteLibrary

    old = RouteLibrary().load("poltergeist")
    new = RouteLibrary().load("wingedsnake").model_copy(update={"restock_map_id": 1002})
    loop = overnight.OvernightLoop.__new__(overnight.OvernightLoop)
    loop.route = old
    loop.auto_level = True
    loop.next_level_check = 0
    loop.last_level = 0
    loop.info = "worker"
    state = {"embedded_controls": {"life": {"map_id": 1002}}}
    loop.health = lambda: state
    events = []
    calls = []
    loop.record = lambda event, **fields: events.append(event)
    loop.stop_farm = lambda: calls.append("stop")
    # No gate carried: a gate home would count as the return (the Desert).
    loop.town = lambda action, **fields: {"items": [], "silver": 0}
    monkeypatch.setattr(leveling_routes, "read_level", lambda *args: 29)
    monkeypatch.setattr(
        leveling_routes,
        "desired_route",
        lambda level: (
            new,
            {"id": "wingedsnake", "levels": [27, 31], "name": "WingedSnake route"},
        ),
    )

    def missing(source, destination):
        if source == 1011:
            raise ValueError("Missing return")
        return []

    monkeypatch.setattr(world_travel, "connection_path", missing)
    assert loop.select_level_route(state) is False and not calls
    assert events[-1] == "level_route_pending"
    loop.next_level_check = 0
    monkeypatch.setattr(world_travel, "connection_path", lambda *args: [])
    monkeypatch.setattr(
        world_travel,
        "travel_to_map",
        lambda loop, destination: calls.append(destination),
    )
    monkeypatch.setattr(overnight, "request", lambda *args: calls.append(args[-1]))
    monkeypatch.setattr(
        overnight, "read_status", lambda path: {"selected_route": "wingedsnake"}
    )
    monkeypatch.setattr(overnight, "read_terrain", lambda *args: None)
    assert loop.select_level_route(state) is True
    assert calls == ["stop", 1011, {"route_id": "wingedsnake"}]
    assert loop.route == new and events[-1] == "level_route_changed"


def test_identical_pending_route_events_are_limited_without_staling_status(
    tmp_path, monkeypatch
):
    from conquest import overnight

    loop = overnight.OvernightLoop.__new__(overnight.OvernightLoop)
    loop.phase = "hunting"
    loop.cycles = 0
    loop.state = {}
    loop.output = tmp_path
    loop.route = SimpleNamespace(id="bandit")
    now = [100.0]
    monkeypatch.setattr(overnight.time, "time", lambda: now[0])
    monkeypatch.setattr(overnight.time, "monotonic", lambda: now[0])
    fields = {
        "level": 73,
        "next_route": "Next zone",
        "activity": "Waiting for a verified connection",
    }
    loop.record("level_route_pending", **fields)
    now[0] = 105
    loop.record("level_route_pending", **fields)
    assert len((tmp_path / "events.jsonl").read_text().splitlines()) == 1
    assert json.loads((tmp_path / "status.json").read_text())["updated_at"] == 105
    now[0] = 160
    loop.record("level_route_pending", **fields)
    assert len((tmp_path / "events.jsonl").read_text().splitlines()) == 2
    now[0] = 161
    loop.record("level_route_pending", **{**fields, "next_route": "Changed zone"})
    assert len((tmp_path / "events.jsonl").read_text().splitlines()) == 3


def test_teleport_checks_town_before_payment_and_visits_it_after_arrival(monkeypatch):
    from conquest import world_travel as w, city_travel, conductress

    calls = []
    life = {"map_id": 1002}
    loop = SimpleNamespace(living=lambda: {"embedded_controls": {"life": life}})
    terrain = SimpleNamespace(source_sha256="same", portals=((963, 557, 7),))
    edge = dict(
        source_map=1002,
        destination_map=1011,
        portal_id=7,
        portal_position=[963, 557],
        source_terrain_sha256="same",
        destination_terrain_sha256="same",
    )
    monkeypatch.setattr(w, "connection_path", lambda *args: [edge])
    monkeypatch.setattr(w, "read_terrain", lambda *args: terrain)
    monkeypatch.setattr(
        city_travel, "city_for", lambda map_id: calls.append("town_checked")
    )
    monkeypatch.setattr(
        conductress, "take_saved_trip", lambda *args: calls.append("teleport") or True
    )

    def cross(*args):
        calls.append("portal")
        life["map_id"] = 1011

    monkeypatch.setattr(w, "cross_portal", cross)
    monkeypatch.setattr(
        city_travel,
        "ensure_city_visit",
        lambda *args, **kw: calls.append(("town", kw["new_arrival"])),
    )
    w.travel_to_map(loop, 1011)
    assert calls == ["town_checked", "teleport", "portal", ("town", True)]
    assert loop.terrain is terrain


def test_leaving_twin_city_never_falls_back_to_walking_without_verified_conductress(
    monkeypatch,
):
    from conquest import world_travel as w, city_travel, conductress

    life = {"map_id": 1002}
    calls = []
    loop = SimpleNamespace(living=lambda: {"embedded_controls": {"life": life}})
    terrain = SimpleNamespace(source_sha256="same", portals=((963, 557, 7),))
    edge = dict(
        source_map=1002,
        destination_map=1011,
        portal_id=7,
        portal_position=[963, 557],
        source_terrain_sha256="same",
        destination_terrain_sha256="same",
    )
    monkeypatch.setattr(w, "connection_path", lambda *args: [edge])
    monkeypatch.setattr(w, "read_terrain", lambda *args: terrain)
    monkeypatch.setattr(city_travel, "city_for", lambda *args: None)
    monkeypatch.setattr(
        conductress, "take_saved_trip", lambda loop, d: calls.append(d) or False
    )
    monkeypatch.setattr(
        w, "cross_portal", lambda *args: pytest.fail("No walking substitute")
    )
    with pytest.raises(ValueError, match="Conductress"):
        w.travel_to_map(loop, 1011)
    assert calls == [1011]


def test_return_to_twin_city_walks_the_verified_portal_without_a_conductress_trip(
    monkeypatch,
):
    """Live 2026-09-27 17:32: Toxic in Phoenix City with 60 silver and no
    scroll; no Conductress trip leads back and the route change failed on
    every retry."""
    from conquest import world_travel as w, city_travel, conductress

    life = {"map_id": 1011}
    calls = []
    # No scroll to read and no silver to buy one: the Conductress rule holds.
    loop = SimpleNamespace(
        living=lambda: {"embedded_controls": {"life": life}},
        town=lambda action, **kw: {"items": [], "silver": 0},
    )
    terrain = SimpleNamespace(source_sha256="same", portals=((5, 376, 0),))
    edge = dict(
        source_map=1011,
        destination_map=1002,
        portal_id=0,
        portal_position=[5, 376],
        source_terrain_sha256="same",
        destination_terrain_sha256="same",
    )
    monkeypatch.setattr(w, "connection_path", lambda *args: [edge])
    monkeypatch.setattr(w, "read_terrain", lambda *args: terrain)
    monkeypatch.setattr(city_travel, "city_for", lambda *args: None)
    monkeypatch.setattr(
        conductress, "take_saved_trip", lambda loop, d: calls.append(d) or False
    )

    def cross(loop, portal_id, expected_map):
        calls.append(("portal", portal_id, expected_map))
        life["map_id"] = 1002

    monkeypatch.setattr(w, "cross_portal", cross)
    monkeypatch.setattr(
        city_travel,
        "ensure_city_visit",
        lambda *args, **kw: calls.append(("town", kw["new_arrival"])),
    )
    w.travel_to_map(loop, 1002)
    assert calls == [1002, ("portal", 0, 1002), ("town", True)]


def test_market_start_uses_saved_return_before_city_visit(tmp_path, monkeypatch):
    from types import SimpleNamespace as NS
    from conquest import meteor_banking, city_travel
    from conquest.discord_notify import read_json

    monkeypatch.chdir(tmp_path)
    plan = {"verified": True, "source_map": 1036, "destination_map": 1011}
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({"origins": {"1011": {"return": plan}}}))
    monkeypatch.setattr(meteor_banking, "POLICY", policy)
    calls = []
    loop = NS(
        route=NS(restock_map_id=1011),
        town=lambda *a: {"items": []},
        living=lambda: {"target": {"pid": 1}},
        record=lambda *a, **k: None,
    )
    monkeypatch.setattr(
        meteor_banking,
        "trip",
        lambda l, p, **kw: (kw["before_submit"](), calls.append(("trip", p))),
    )
    monkeypatch.setattr(
        city_travel, "ensure_city_visit", lambda l, **k: calls.append(("city", k))
    )
    w.return_from_market(loop, 1011)
    assert calls == [("trip", plan), ("city", {"new_arrival": True})]
    assert read_json(".runtime/market-route-departure.json")["phase"] == "complete"


def test_market_departure_keeps_valuables_and_uncertain_transfer_safe(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace as NS
    from conquest import meteor_banking

    monkeypatch.chdir(tmp_path)
    policy = tmp_path / "policy.json"
    policy.write_text(
        json.dumps(
            {
                "origins": {
                    "1011": {
                        "return": {
                            "verified": True,
                            "source_map": 1036,
                            "destination_map": 1011,
                        }
                    }
                }
            }
        )
    )
    monkeypatch.setattr(meteor_banking, "POLICY", policy)
    monkeypatch.setattr(
        meteor_banking, "trip", lambda *a: pytest.fail("Unsafe departure")
    )
    bag = [{"uid": 1, "type_id": 1088000, "slot": 0, "plus": 0}]
    loop = NS(route=NS(restock_map_id=1011), town=lambda *a: {"items": bag})
    # An unfinished Meteor trip stores in Market through its own journal.
    meteor_banking.JOURNAL.write_text('{"phase": "stored_in_market"}')
    with pytest.raises(ValueError, match="store protected"):
        w.return_from_market(loop, 1011)
    meteor_banking.JOURNAL.unlink()
    bag.clear()
    Path(".runtime").mkdir()
    Path(".runtime/market-route-departure.json").write_text('{"phase":"submitted"}')
    with pytest.raises(ValueError, match="uncertain"):
        w.return_from_market(loop, 1011)


@pytest.mark.parametrize("deposit_works", [True, False])
def test_valuables_carried_into_the_market_are_banked_there_before_leaving(
    tmp_path, monkeypatch, deposit_works
):
    # Suicide, 2026-10-03 00:58: brought into the Market by hand with a +1 ring
    # and a unique MaskBag, it refused to leave and never banked them.
    from types import SimpleNamespace as NS
    from conquest import banking, meteor_banking

    monkeypatch.chdir(tmp_path)
    plan = {"verified": True, "source_map": 1036, "destination_map": 1002}
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({"origins": {"1002": {"return": plan}}}))
    monkeypatch.setattr(meteor_banking, "POLICY", policy)
    bag = [
        {"uid": 7, "type_id": 150005, "slot": 20, "plus": 1},
        {"uid": 8, "type_id": 1050001, "slot": 3, "plus": 0},
    ]
    calls = []

    def deposit(loop, item):
        calls.append(("deposit", item["uid"]))
        if not deposit_works:
            return {"verified_in_warehouse": False}
        bag.remove(item)
        return {"stored": item["uid"], "verified_in_warehouse": True}

    monkeypatch.setattr(banking, "deposit_item", deposit)
    monkeypatch.setattr(banking, "open_warehouse", lambda loop: calls.append("open"))
    monkeypatch.setattr(banking, "close_warehouse", lambda loop: calls.append("close"))
    monkeypatch.setattr(
        meteor_banking, "approach_market_warehouse", lambda loop, why: calls.append("walk")
    )
    monkeypatch.setattr(
        meteor_banking, "trip", lambda l, p, **kw: (kw["before_submit"](), calls.append("trip"))
    )
    from conquest import city_travel

    monkeypatch.setattr(city_travel, "ensure_city_visit", lambda l, **k: calls.append("city"))
    events = []
    loop = NS(
        route=NS(restock_map_id=1002),
        town=lambda *a, **k: {"items": list(bag)},
        living=lambda: {"target": {"pid": 1}},
        record=lambda name, **fields: events.append(name),
    )
    if deposit_works:
        w.return_from_market(loop, 1002)
        assert calls == ["walk", "open", ("deposit", 7), "close", "trip", "city"]
        assert events.count("valuable_stored") == 1
    else:
        with pytest.raises(ValueError, match="receipt missing"):
            w.return_from_market(loop, 1002)
        assert "trip" not in calls and calls[-1] == "close"


def test_a_carried_gate_home_counts_as_the_way_back(monkeypatch):
    # The Desert (2026-09-30): nobody has crossed its portals, so its way home
    # to Ape City is the ApeCityGate the farmer carries.
    from conquest import return_scroll, world_travel as w

    def saved(source, destination, edges=None):
        if source == 1000:
            raise ValueError("No memory-verified map connection")
        return [] if source == destination else [None]

    monkeypatch.setattr(w, "connection_path", saved)
    held = []
    monkeypatch.setattr(return_scroll, "carried", lambda loop, kind: kind in held)
    loop = object()
    assert w.reachable(loop, 1000, 1000)
    assert not w.reachable(loop, 1000, 1020) and w.gate_towns(loop, 1000, 1020) == []
    held.append(1060022)
    assert w.reachable(loop, 1000, 1020)
    held.append(1060020)
    # Ape City's own gate first (no hop), then Twin City's.
    assert w.gate_towns(loop, 1000, 1020) == [1020, 1002]
    # A gate is never read to the map the farmer stands on.
    assert w.gate_towns(loop, 1020, 1000) == [1002]
