from types import SimpleNamespace as NS
import pytest
from conquest import savings as s, session_plan as p
from conquest.routes import RouteLibrary


@pytest.fixture
def plan():
    value = {
        "active": True,
        "mode": "save_silver",
        "route_id": "poltergeist",
        "upgrade_maps": [1002],
        "silver_target": 50000,
        "starting_silver": 961,
        "started_at": 123,
    }
    p.write_json(p.PLAN, value)
    return value


def test_savings_pins_poltergeists_and_uses_low_cost_stock(plan):
    from conquest.overnight import OvernightLoop

    loop = OvernightLoop("bandit")
    assert loop.route.id == "poltergeist"
    assert loop.route.supplies.arrow_type == 1050000
    assert loop.route.supplies.healing_restock_to == 5
    assert loop.route.supplies.arrows_restock_to == 2000
    assert "961 / 50,000" in p.plan_note()


@pytest.mark.parametrize(
    "kind,cash,potions,allowed",
    [
        (1000020, 60, 0, True),
        (1000020, 59, 0, False),
        (1000020, 259, 3, False),
        (1000020, 260, 3, True),
        (1050001, 50000, 6, False),
    ],
)
def test_goal_spending_covers_essentials_then_preserves_cash(
    plan, kind, cash, potions, allowed
):
    counts = {"silver": cash, "potions": potions, "arrows": 722}
    assert (
        s.affordable_supply(kind, 60, counts, RouteLibrary().load("poltergeist"))
        is allowed
    )


def test_no_equipment_purchase_or_circuit_during_savings(plan):
    from conquest.equipment import EquipmentReview

    loop = NS(
        route=RouteLibrary().load("poltergeist"),
        town=lambda *a, **k: pytest.fail("No upgrades"),
    )
    assert EquipmentReview(loop).visit(5)
    assert p.upgrade_circuit(loop) is False


def test_progress_requires_actual_balance_instead_of_gross_income(plan):
    loop = NS(record=lambda *a, **k: None)
    assert not s.progress(loop, 49999)
    assert s.progress(loop, 50000)
    assert p.active_plan()["balance"] == 50000


@pytest.mark.parametrize("balance", [49999, 50000, 100000])
def test_removed_limit_continues_above_old_goal_without_town_completion(plan, balance):
    plan["silver_target"] = None
    p.write_json(p.PLAN, plan)
    loop = NS(
        record=lambda *a, **k: None, travel=lambda *a: pytest.fail("No goal return")
    )
    assert not s.progress(loop, balance)
    assert not s.finish_in_town(loop)
    assert p.active_plan()["balance"] == balance
    assert p.active_plan()["route_id"] == "poltergeist"
    assert "continuous farming" in p.plan_note()
    assert "/ 50,000" not in p.plan_note()


@pytest.mark.parametrize("balance,completed", [(49999, False), (50000, True)])
def test_goal_only_finishes_after_town_arrival_and_balance_verification(
    plan, monkeypatch, balance, completed
):
    from conquest import city_travel

    calls = []
    monkeypatch.setattr(
        city_travel, "city_for", lambda m: {"town_boundary": [450, 320, 480, 345]}
    )
    loop = NS(
        route=RouteLibrary().load("poltergeist"),
        record=lambda *a, **k: None,
        travel=lambda point: calls.append(point),
        town=lambda *a, **k: {"silver": balance},
        living=lambda: {
            "embedded_controls": {
                "life": {
                    "map_id": 1002,
                    "position": [466, 333],
                    "dead_candidate": False,
                }
            }
        },
    )
    assert s.finish_in_town(loop) is completed
    assert calls == [(466, 333)]
    assert bool(p.active_plan().get("completed")) is completed


@pytest.mark.parametrize("balance,arrows,potions", [(4999, 2000, 5), (5000, 2000, 5)])
def test_funded_goal_stocks_more_cheap_supplies_to_reduce_town_trips(
    plan, balance, arrows, potions
):
    route = s.configure_route(RouteLibrary().load("poltergeist"), balance)
    assert route.supplies.arrow_type == 1050000
    assert route.supplies.arrows_restock_to == arrows
    assert route.supplies.healing_restock_to == potions


@pytest.mark.parametrize("cash,allowed", [(7799, False), (7800, True)])
def test_authorized_iron_arrows_preserve_three_thousand(plan, cash, allowed):
    plan["allow_iron_arrows"] = True
    p.write_json(p.PLAN, plan)
    route = RouteLibrary().load("poltergeist")
    assert (
        s.affordable_supply(
            1050001, 4800, {"silver": cash, "arrows": 0, "potions": 10}, route
        )
        is allowed
    )
    assert not s.affordable_supply(1050002, 4800, {"silver": cash}, route)
    iron = route.model_copy(
        update={"supplies": route.supplies.model_copy(update={"arrow_type": 1050001})}
    )
    assert s.configure_route(iron, cash).supplies.arrow_type == 1050001
    assert s.configure_route(route, cash).supplies.arrow_type == 1050000


def test_authorized_review_only_passes_iron_arrows_to_verified_upgrade(
    plan, monkeypatch
):
    from conquest.equipment import EquipmentReview
    from conquest import arrow_upgrades

    plan["allow_iron_arrows"] = True
    p.write_json(p.PLAN, plan)
    calls = []
    products = [{"type_id": kind} for kind in (1050000, 1050001, 1050002, 500000)]
    snapshots = {
        "shop": {"products": products},
        "gear": {"level": 38},
        "supplies": {"silver": 9000},
    }
    loop = NS(town=lambda action, **kw: snapshots[action])
    monkeypatch.setattr(
        arrow_upgrades, "review_arrows", lambda *args: calls.append(args)
    )
    assert EquipmentReview(loop).visit(4)
    assert not calls
    assert EquipmentReview(loop).visit(5)
    assert calls == [(loop, [{"type_id": 1050001}], {"level": 38}, 9000)]


def test_continuous_banked_funds_support_longer_hunts_and_return_reserve(
    plan, monkeypatch
):
    from conquest import banking

    plan.update(silver_target=None, balance=200)
    p.write_json(p.PLAN, plan)
    monkeypatch.setattr(banking, "policy", lambda: {"enabled": True})
    p.write_json(banking.STATUS, {"stored_silver": 14021})
    route = s.configure_route(RouteLibrary().load("poltergeist"))
    assert route.supplies.healing_restock_to == 5
    assert route.supplies.healing_return_below == 1
    low = s.configure_route(route, 1000)
    assert low.supplies.healing_restock_to == 5
    assert low.supplies.healing_return_below == 1


@pytest.mark.parametrize(
    "target,balance,reached", [(None, 60000, False), (50000, 50000, True)]
)
def test_progress_file_lock_cannot_stop_farming_or_change_goal(
    plan, monkeypatch, target, balance, reached
):
    plan["silver_target"] = target
    p.write_json(p.PLAN, plan)

    def locked(*a):
        raise PermissionError("File temporarily locked")

    monkeypatch.setattr(s, "write_json", locked)
    loop = NS(record=lambda *a, **k: None)
    assert s.progress(loop, balance) is reached
    assert loop.savings_report_error == "File temporarily locked"
    assert p.active_plan()["active"] and p.active_plan()["silver_target"] == target
