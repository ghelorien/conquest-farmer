"""snakeman-canyon: Snakemen in Love Canyon, entered through the Desert.

Alex 2026-09-30 21:2x chose "Snakemen, Love Canyon". The client's help puts
Snakemen (L62/63) at Love Canyon (232, 461), and region.json names map 1020
(Ape Mountain) "LoveCanyon". The canyon is walled off from Ape City: the walk
is ~1,285 tiles through the GiantApe and ThunderApe bosses. The route enters
instead through the Desert (GeneralPeace's "I see." lands at (971, 666)) and
its east portal 1, expected to land by the canyon's portal 4 (11, 377).

Failure modes, written before the change:
1. The route targets anything but the Snakeman family, or its anchors,
   patrol or connectors are off the box or unwalkable on Ape Mountain.
2. The entry region misses the box or the arrival corner, or takes in Ape
   City town, so the farmer walks the 1,285 tiles or skips the entry.
3. The entry walks to Desert City (map travel's town visit), or counts a
   portal that landed elsewhere, or travels while already in the canyon.
4. The hunt and the return to the route map ignore the entry and walk.
5. A gate home from the canyon gives up for want of a quiet spot and walks.
"""

from types import SimpleNamespace as NS

import pytest

from conquest.routes import RouteLibrary, route_monster_names
from test_ratling_route import _segment

ROOT = r"C:\Program Files\Classic Conquer 2.0"


@pytest.fixture(scope="module")
def route():
    return RouteLibrary().load("snakeman-canyon")


@pytest.fixture(scope="module")
def terrain():
    from conquest.navigation import read_terrain

    try:
        return read_terrain(ROOT, 1020)
    except (OSError, ValueError) as error:
        pytest.skip(f"installed maps unavailable: {error}")


def test_the_route_targets_snakemen_on_walkable_canyon_ground(route, terrain):
    # 1
    assert route.map_id == route.restock_map_id == 1020
    assert route_monster_names(route) == ("Snakeman", "SnakemanL63")
    assert terrain.source_sha256 == route.terrain_sha256
    for path in (route.outbound_waypoints, route.return_waypoints):
        for a, b in zip(path, path[1:]):
            assert all(terrain.walkable(t) for t in _segment(a, b)), (a, b)
    left, top, right, bottom = route.hunting_boundary
    for point in (route.hunting_anchor, *route.patrol):
        assert left <= point[0] <= right and top <= point[1] <= bottom
        assert terrain.walkable(point)
        terrain.path(route.hunting_anchor, point)


def test_the_entry_region_holds_the_canyon_not_ape_city(route, terrain):
    # 2
    from conquest.city_travel import city_for
    from conquest.world_travel import inside

    entry = route.entry
    assert (entry.map_id, entry.portal_id) == (1000, 1)
    x0, y0, x1, y1 = route.hunting_boundary
    assert inside(entry.region, (x0, y0)) and inside(entry.region, (x1, y1))
    assert inside(entry.region, route.town_anchor)
    assert (11, 377, 4) in terrain.portals
    assert max(abs(a - b) for a, b in zip(route.town_anchor, (11, 377))) <= 12
    town = city_for(1020)
    a, b, c, d = town["town_boundary"]
    for corner in ((a, b), (c, b), (a, d), (c, d), tuple(town["town_anchor"])):
        assert not inside(entry.region, corner)
    # Walled off: from town the walk in is the long way round.
    assert len(terrain.path(tuple(town["town_anchor"]), route.hunting_anchor)) > 1000


class Loop:
    def __init__(self, route, map_id, position):
        self.route = route
        self.life = {"map_id": map_id, "position": list(position)}
        self.events, self.terrain = [], None

    def living(self):
        return {"embedded_controls": {"life": dict(self.life)}}

    def record(self, event, **fields):
        self.events.append(event)


def test_the_entry_skips_desert_city_and_checks_the_landing(route, monkeypatch):
    # 3
    from conquest import world_travel

    calls = []
    landing = {"tile": [14, 378]}

    def travel(loop, destination, visit_town=True):
        calls.append(("travel", destination, visit_town))
        loop.life.update(map_id=1000, position=[971, 666])

    def cross(loop, portal_id, expected_map=None):
        calls.append(("portal", portal_id, expected_map))
        loop.life.update(map_id=1020, position=list(landing["tile"]))
        return {"destination_map": 1020}

    monkeypatch.setattr(world_travel, "travel_to_map", travel)
    monkeypatch.setattr(world_travel, "cross_portal", cross)
    monkeypatch.setattr(world_travel, "read_terrain", lambda root, map_id: NS(map_id=map_id))
    loop = Loop(route, 1020, (565, 562))  # Ape City town after a restock
    assert world_travel.enter_route_area(loop) is True
    assert calls == [("travel", 1000, False), ("portal", 1, 1020)]
    assert "route_entry_crossed" in loop.events
    # Already in the canyon: nothing to do.
    calls.clear()
    assert world_travel.enter_route_area(Loop(route, 1020, (232, 461))) is False
    assert calls == []
    # Restarted in the Desert: straight to the portal (map travel is a no-op).
    loop = Loop(route, 1000, (971, 666))
    assert world_travel.enter_route_area(loop) is True
    # A portal that lands outside the canyon is not counted.
    landing["tile"] = [565, 562]
    with pytest.raises(ValueError, match="landed outside"):
        world_travel.enter_route_area(Loop(route, 1020, (565, 562)))


def test_the_hunt_and_the_route_return_take_the_entry(route, monkeypatch):
    # 4
    from conquest import city_travel, overnight, world_travel

    calls = []
    monkeypatch.setattr(world_travel, "enter_route_area", lambda loop: calls.append("entry"))
    monkeypatch.setattr(
        world_travel, "travel_to_map", lambda loop, d, visit_town=True: calls.append(("walk", d))
    )
    monkeypatch.setattr(city_travel, "ensure_city_visit", lambda *a, **k: calls.append("town"))

    class Stop(Exception):
        pass

    loop = overnight.OvernightLoop.__new__(overnight.OvernightLoop)
    loop.route = route
    loop.info = "worker"
    loop.record = lambda *a, **k: None
    loop.living = lambda: None
    loop.stop_farm = lambda: None

    def stop(*a, **k):
        raise Stop

    monkeypatch.setattr("conquest.conductress_shortcut.ride", stop)
    with pytest.raises(Stop):
        loop.hunt()
    assert calls == ["entry"]
    calls.clear()
    monkeypatch.setattr(overnight, "request", lambda *a, **k: None)
    monkeypatch.setattr(overnight.time, "sleep", lambda s: None)
    loop.return_to_route_map()
    assert calls == ["entry"]


def test_a_gate_home_from_the_canyon_is_read_without_a_quiet_spot(route, monkeypatch):
    # 5
    from conquest import overnight, return_scroll

    reads, quiet = [], []
    monkeypatch.setattr(return_scroll, "read_gate", lambda loop, home: reads.append(home) or True)
    loop = overnight.OvernightLoop.__new__(overnight.OvernightLoop)
    loop.route = route
    loop.record = lambda *a, **k: None
    loop.living = lambda: {"embedded_controls": {"life": {"map_id": 1020, "position": [232, 461]}}}
    loop.quiet_for_gate = lambda: quiet.append(1) or False
    assert loop.gate_home_from_afar() is True
    assert len(quiet) == overnight.WALLED_GATE_TRIES and reads == [1020]
    # Outside a walled field the old rule stands: no quiet spot, walk home.
    reads.clear()
    quiet.clear()
    loop.route = route.model_copy(update={"entry": None})
    assert loop.gate_home_from_afar() is False
    assert len(quiet) == 1 and reads == []
