from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest
from pydantic import ValidationError

from conquest.control import FarmingControl
from conquest.desktop_app import DesktopApp
from conquest.memory_inventory import InventorySnapshot, Item
from conquest.navigation import TerrainMap
from conquest.routes import RouteLibrary, SavedRoute, plan_travel


def definition():
    return SavedRoute(
        id="test",
        name="Test route",
        map_id=1002,
        monster_type_ids=(1,),
        recommended_levels=(1, 7),
        town_anchor=(1, 1),
        hunting_anchor=(5, 5),
        hunting_boundary=(4, 4, 6, 6),
        patrol=((4, 4), (5, 5)),
        outbound_waypoints=((1, 1), (1, 5), (5, 5)),
        return_waypoints=((5, 5), (1, 5), (1, 1)),
        terrain_sha256="a" * 64,
        tasks=(
            "travel_to_hunt",
            "hunt_and_loot",
            "return_to_town",
            "restock",
            "check_equipment",
            "resume_hunt",
        ),
    )


def test_saved_template_round_trip_and_explicit_replacement(tmp_path):
    library = RouteLibrary(tmp_path)
    route = definition()
    path = library.save(route)
    assert RouteLibrary(tmp_path).load("test") == route
    with pytest.raises(ValueError, match="already exists"):
        library.save(route)
    changed = route.model_dump()
    changed["supplies"]["arrows_return_below"] = 300
    library.save(changed, replace=True)
    assert library.load("test").supplies.arrows_return_below == 300
    assert list(tmp_path.iterdir()) == [path]
    with pytest.raises(ValueError, match="name already exists"):
        library.save({**route.model_dump(), "id": "other"})


@pytest.mark.parametrize(
    "changes",
    [
        {"id": "../outside"},
        {"recommended_levels": (12, 7)},
        {"patrol": ((50, 50),)},
        {"outbound_waypoints": ((2, 2), (5, 5))},
        {"supplies": {"arrows_return_below": 2000, "arrows_restock_to": 1000}},
    ],
)
def test_invalid_routes_cannot_be_saved(tmp_path, changes):
    with pytest.raises(ValidationError):
        RouteLibrary(tmp_path).save({**definition().model_dump(), **changes})
    assert not list(tmp_path.iterdir())


def test_travel_replans_from_live_position_and_checks_map_version():
    route = definition()
    terrain = TerrainMap(1002, 8, 8, np.zeros((8, 8), dtype=bool), "a" * 64, (), ())
    terrain.blocked[3, 2:7] = True
    first = plan_travel(route, terrain, (2, 2))
    second = plan_travel(route, terrain, (4, 4))
    assert first["waypoints"][0] == (2, 2) and second["waypoints"][0] == (4, 4)
    assert first["waypoints"][-1] == second["waypoints"][-1] == (5, 5)
    assert first["tile_steps"] > second["tile_steps"]
    assert plan_travel(route, terrain, (4, 4), phase="return")["destination"] == [1, 1]
    assert plan_travel(route, terrain, (5, 5), phase="patrol", patrol_index=2)[
        "destination"
    ] == [4, 4]
    with pytest.raises(ValueError, match="terrain differs"):
        plan_travel(route, replace(terrain, source_sha256="b" * 64), (2, 2))
    assert route == definition()  # No per-run progress leaks into the saved route.


def test_supply_return_counts_equipped_arrows_and_all_spare_stacks():
    route = definition()
    inventory = InventorySnapshot(
        0,
        1,
        (
            Item(1, 1050000, 100, 200, 0),
            Item(2, 1050000, 5, 200, 1),
            Item(3, 1000000, 3, 99, 2),
        ),
        Item(4, 1050000, 95, 200, None),
        500,
        40,
    )
    assert route.return_reasons(inventory) == []
    assert route.return_reasons(replace(inventory, equipped_ammo=None)) == []
    assert route.return_reasons(replace(inventory, items=(), equipped_ammo=None)) == [
        "arrows_low",
        "healing_supplies_low",
    ]
    assert route.return_reasons(replace(inventory, capacity=6)) == []
    assert route.return_reasons(replace(inventory, capacity=3)) == ["inventory_full"]


class Text:
    def __init__(self):
        self.value = ""

    def set(self, value):
        self.value = value


def test_ui_route_selection_persists_group_but_never_starts_farming(tmp_path):
    from conquest import session_plan

    session_plan.write_json(
        session_plan.PLAN,
        {
            "active": True,
            "mode": "hold_route",
            "route_id": "pheasant",
            "upgrade_maps": [1002],
            "started_at": 123,
        },
    )
    app = DesktopApp.__new__(DesktopApp)
    app.route_library = RouteLibrary(tmp_path / "routes")
    app.route_library.save(definition())
    app.saved_routes = app.route_library.all()
    app.route_picker = SimpleNamespace(current=lambda: 0)
    app.control = FarmingControl(tmp_path / "controls.json")
    app.control.update({"enabled": True, "target_ids": [123], "target_type_ids": [2]})
    app.route_selection_path = tmp_path / "selected-route.json"
    app.route_text, app.route_note = Text(), Text()
    app.selected_route = None
    app.thread = None
    app.record = lambda: None
    app.select_route()
    selected = app.control.snapshot()
    assert not selected["enabled"] and selected["target_ids"] == []
    assert selected["target_type_ids"] == [1]
    held = session_plan.read_json(session_plan.PLAN)
    assert (
        held["active"] and held["mode"] == "hold_route" and held["route_id"] == "test"
    )
    assert held["upgrade_maps"] == [app.selected_route.restock_map_id]
    app.selected_route = None
    app.restore_route()
    assert app.selected_route.id == "test"
    app.selected_route = None
    app.control.update({"target_type_ids": [2]})
    app.restore_route()
    assert app.selected_route is None


def test_shipped_routes_are_distinct_complete_templates():
    routes = RouteLibrary().all()
    assert {r.id for r in routes} >= {"pheasant", "turtledove"}
    assert len({r.name for r in routes}) == len(routes)
    assert all(r.tasks == definition().tasks for r in routes)


def test_apparition_route_targets_level_18_and_exact_monster_group():
    from conquest.routes import route_monster_name

    route = RouteLibrary().load("apparition")
    assert route.recommended_levels[0] <= 18 <= route.recommended_levels[1]
    assert route.monster_type_ids == (4,)
    assert route_monster_name(route) == "Apparition"
    with pytest.raises(ValueError, match="one supported"):
        route_monster_name(route.model_copy(update={"monster_type_ids": (2, 4)}))


def test_macaque_family_is_available_for_saved_route_selection():
    from conquest.routes import MONSTER_NAMES, route_monster_names, monster_family
    from conquest.trial import TrialConfig

    assert MONSTER_NAMES[10] == "Macaque"
    assert tuple(m["type_id"] for m in monster_family(10)) == (10, 69)
    route = RouteLibrary().load("macaque")
    assert route.map_id == route.restock_map_id == 1020
    assert route.qualification == "planned"
    assert route.recommended_levels == (47, 51)
    assert route_monster_names(route) == ("Macaque", "MacaqueL48")
    assert route.supplies.healing_threshold == 0.85
    assert route.hunting_boundary == (600, 612, 688, 670)
    config = TrialConfig(
        character="Kilhiam",
        player_profile="player.yaml",
        inventory_profile="inventory.yaml",
        template="template.png",
        client_size=(1036, 793),
        boundary=(600, 580, 688, 670),
        monster="Macaque",
    )
    assert config.monster == "Macaque"


def test_macaque_town_services_use_surveyed_npc_identities():
    from conquest.city_travel import city_for
    from conquest.memory_npcs import vendor_identity, TOWN_VENDORS

    city = city_for(1020)
    assert city["services"]["pharmacist"] == [550, 547]
    assert city["services"]["blacksmith"] == [560, 513]
    assert city["services"]["equipment"] == []
    assert vendor_identity(1020, 3).position == (550, 542)
    assert vendor_identity(1020, 5).position == (560, 508)
    assert any(
        v.map_id == 1020 and v.name == "Warehouseman" and v.position == (576, 542)
        for v in TOWN_VENDORS
    )


def test_ape_city_is_an_allowed_town_trade_map(monkeypatch):
    from conquest import town_trade

    monkeypatch.setattr(town_trade, "login_screen", lambda hwnd: False)
    life = SimpleNamespace(
        dead_candidate=False, map_id=1020, current_hp=100, max_hp=100
    )
    observer = SimpleNamespace(
        operations=SimpleNamespace(target=SimpleNamespace(hwnd=1)),
        read_life=lambda: life,
    )
    trade = town_trade.TownTrade.__new__(town_trade.TownTrade)
    trade.observer = observer
    assert trade.life() is life


def test_apparition_expansion_stays_clear_of_failed_western_edge():
    route = RouteLibrary().load("apparition")
    reach = route.patrol_search.expansion_tiles * route.patrol_search.maximum_expansions
    assert route.hunting_boundary[0] - reach >= 240
    assert route.hunting_boundary[1] - reach >= 550
    assert route.hunting_boundary[3] + reach <= 660


def test_route_families_include_nearby_level_variants_but_never_bosses_or_far_higher_bandits():
    from conquest.routes import route_monster_names, monster_family

    route = RouteLibrary().load("bandit")
    assert route.monster_type_ids == (7, 66)
    assert route_monster_names(route) == ("Bandit", "BanditL33")
    assert RouteLibrary().load("wingedsnake").monster_type_ids == (6, 65)
    assert RouteLibrary().load("firespirit").monster_type_ids == (9, 68)
    for kind in (8302, 1401, 55, 79, 8102, 8202):
        with pytest.raises(ValueError):
            route_monster_names(
                route.model_copy(update={"monster_type_ids": (7, kind)})
            )
    assert {m["type_id"] for m in monster_family(55)} == {55, 79}
