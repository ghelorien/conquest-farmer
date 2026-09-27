"""A restarted route resumes on the map the character stands on when its
level route is there, instead of walking back to the old route's map.

Live 2026-09-27 17:46 (Toxic, level 27, Scatter): the level change from the
WingedSnakes (Phoenix) to the Poltergeists (Twin City) failed after the
character had crossed Phoenix's west gate. The restarted route was still the
WingedSnake one and turned round for Phoenix: a Conductress fare it could
not pay, planned on Phoenix's terrain from a Twin City tile.
"""

from types import SimpleNamespace as NS

import pytest

from conquest.overnight import OvernightLoop


def loop_on(map_id, route_map, level_route_map=None):
    calls = []
    loop = OvernightLoop.__new__(OvernightLoop)
    loop.route = NS(id="wingedsnake", map_id=route_map)
    loop.living = lambda: {"embedded_controls": {"life": {"map_id": map_id}}}

    def select_level_route():
        calls.append("select_level_route")
        if level_route_map is not None:
            loop.route = NS(id="poltergeist", map_id=level_route_map)

    loop.select_level_route = select_level_route
    loop.return_to_route_map = lambda: calls.append("return_to_route_map")
    return loop, calls


@pytest.mark.parametrize(
    "where, route_map, level_route_map, expected",
    [
        # The live case: across the gate, the level route is on this map.
        (1002, 1011, 1002, ["select_level_route"]),
        # A revive in another town: the level route does not live here.
        (1002, 1011, None, ["select_level_route", "return_to_route_map"]),
        # Already on the route's map: the ordinary start is unchanged.
        (1011, 1011, 1002, []),
    ],
)
def test_resume_on_route_map(where, route_map, level_route_map, expected):
    loop, calls = loop_on(where, route_map, level_route_map)
    loop.resume_on_route_map()
    assert calls == expected


def test_travel_plans_on_the_map_it_stands_on(monkeypatch):
    """Banking for the fare and the walk to the Conductress use this map's
    terrain, not the one the route process started with."""
    from conquest import world_travel as w, city_travel, conductress

    here = NS(source_sha256="tc", portals=((963, 557, 7),), map_id=1002)
    there = NS(source_sha256="phoenix", portals=(), map_id=1011)
    life = {"map_id": 1002}
    loop = NS(living=lambda: {"embedded_controls": {"life": life}}, terrain=there)
    edge = dict(
        source_map=1002,
        destination_map=1011,
        portal_id=7,
        portal_position=[963, 557],
        source_terrain_sha256="tc",
        destination_terrain_sha256="phoenix",
    )
    monkeypatch.setattr(w, "connection_path", lambda *args: [edge])
    monkeypatch.setattr(w, "read_terrain", lambda root, map_id: here if map_id == 1002 else there)
    monkeypatch.setattr(city_travel, "city_for", lambda *args: None)
    seen = []

    def trip(loop, destination):
        seen.append(loop.terrain.map_id)
        raise ValueError("stop here")

    monkeypatch.setattr(conductress, "take_saved_trip", trip)
    with pytest.raises(ValueError, match="stop here"):
        w.travel_to_map(loop, 1011)
    assert seen == [1002]
