from types import SimpleNamespace
import json
import pytest
from conquest import city_travel as c


@pytest.fixture
def visit(monkeypatch, tmp_path):
    monkeypatch.setattr("conquest.session_plan.active_plan", lambda: None)
    city = dict(
        map_id=1011,
        name="PhoenixCity",
        town_anchor=[210, 195],
        town_boundary=[111, 111, 254, 258],
        terrain_sha256="abc",
    )
    monkeypatch.setattr(c, "VISIT", tmp_path / "visit.json")
    monkeypatch.setattr(c, "city_for", lambda map_id: city)
    monkeypatch.setattr(c, "read_terrain", lambda *args: None)
    monkeypatch.setattr(c.time, "time", lambda: 10)
    health = {
        "target": {"pid": 1},
        "embedded_controls": {
            "observed_at": 10,
            "life": dict(
                object_address=99, map_id=1011, position=[11, 376], dead_candidate=False
            ),
        },
    }
    calls = []

    def travel(point, **kwargs):
        calls.append(("travel", point))
        health["embedded_controls"]["life"]["position"] = list(point)

    loop = SimpleNamespace(
        route=SimpleNamespace(id="bandit"),
        living=lambda: health,
        phase="hunting",
        stop_farm=lambda: calls.append(("stop",)),
        record=lambda *a, **kw: None,
        travel=travel,
    )
    return loop, health, calls


def test_explicit_prepared_hunt_skips_initial_visit_but_not_new_city_arrival(
    visit, monkeypatch
):
    loop, health, calls = visit
    monkeypatch.setattr(
        "conquest.session_plan.active_plan",
        lambda: {
            "route_id": "bandit",
            "start_at_hunt": {"identity": health["target"], "map_id": 1011},
        },
    )
    assert not c.ensure_city_visit(loop)
    assert not calls and not c.VISIT.exists()
    assert c.ensure_city_visit(loop, new_arrival=True)
    assert calls == [("stop",), ("travel", (210, 195))]


def test_arrival_visits_destination_town_once_and_persists_across_restart(visit):
    loop, health, calls = visit
    assert c.ensure_city_visit(loop)
    assert calls == [("stop",), ("travel", (210, 195))]
    assert json.loads(c.VISIT.read_text())["completed"]
    assert not c.ensure_city_visit(loop)
    assert len(calls) == 2
    assert c.ensure_city_visit(loop, new_arrival=True)
    assert len(calls) == 3 and loop.phase == "hunting"


def test_process_restart_rechecks_town_arrival(visit):
    loop, health, calls = visit
    c.ensure_city_visit(loop)
    health["target"] = {"pid": 2}
    assert c.ensure_city_visit(loop)
    assert len(calls) == 3


def test_already_at_town_warehouse_does_not_detour_to_centre(visit):
    loop, health, calls = visit
    health["embedded_controls"]["life"]["position"] = [227, 246]
    assert c.ensure_city_visit(loop, new_arrival=True)
    assert calls == [("stop",)]
    assert json.loads(c.VISIT.read_text())["position"] == [227, 246]


@pytest.mark.parametrize("change", ["map", "dead", "stale", "outside", "actor"])
def test_town_arrival_needs_fresh_living_identity_and_town_position(visit, change):
    loop, health, calls = visit

    def travel(*args, **kwargs):
        life = health["embedded_controls"]["life"]
        life["position"] = [210, 195]
        if change == "map":
            life["map_id"] = 1002
        if change == "dead":
            life["dead_candidate"] = True
        if change == "stale":
            health["embedded_controls"]["observed_at"] = 8
        if change == "outside":
            life["position"] = [317, 77]
        if change == "actor":
            life["object_address"] = 100

    loop.travel = travel
    with pytest.raises(ValueError, match="not confirmed"):
        c.ensure_city_visit(loop)
    assert not json.loads(c.VISIT.read_text())["completed"]


def test_vendor_stop_lookup_includes_phoenix_armorer_and_blacksmith():
    assert c.service_role(1011, (204, 245)) == 4
    assert c.service_role(1011, (199, 229)) == 5
    assert c.service_role(1011, (191, 250)) == 3
    assert c.service_role(1002, (415, 358)) == 1
    assert c.service_role(1011, (300, 300)) is None
