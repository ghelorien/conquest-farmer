"""Supply rates are learned from the hunt's arrival in its field, not from town.

2026-09-30 23:31-23:56 (Toxic, Love Canyon): 19 of 25 minutes went on a
King-blocked passage on the way in. end_hunt measured from the restock, so the
walk halved the arrow rate and tripled the potions (heals while waiting), and
the next restock bought 30 potions and one arrow pack instead of five.

Failure modes, written before the change:
1. The walk in still counts as hunting.
2. A second arrival in the same hunt (a stray step out and back) moves the
   start again, or another route's hunt is moved.
3. The hunt loop never marks the arrival, or marks it before the farmer is in
   its field.
"""

from types import SimpleNamespace as NS

import pytest

from conquest import overnight, supply_plan
from conquest.overnight import OvernightLoop
from conquest.routes import RouteLibrary


def test_the_hunt_is_learned_from_its_arrival():
    # 1
    supply_plan.begin_hunt("snakeman-south", {"potions": 30, "arrows": 2000}, now=0)
    # Nineteen minutes at the blocked passage: 8 potions, no arrow.
    assert supply_plan.arrived("snakeman-south", {"potions": 22, "arrows": 2000}, now=19 * 60)
    learned = supply_plan.end_hunt(
        "snakeman-south", {"potions": 21, "arrows": 0}, now=(19 + 17) * 60
    )
    assert learned["arrows_per_min"] == pytest.approx(2000 / 17)
    assert learned["potions_per_min"] == pytest.approx(1 / 17)


def test_an_arrival_moves_only_its_own_hunt_and_only_once():
    # 2
    supply_plan.begin_hunt("snakeman-south", {"potions": 30, "arrows": 2000}, now=0)
    assert supply_plan.arrived("snakeman-south", {"potions": 22, "arrows": 2000}, now=60)
    assert not supply_plan.arrived("snakeman-south", {"potions": 20, "arrows": 1500}, now=120)
    assert supply_plan.read_json(supply_plan.RATES)["current"]["arrows"] == 2000
    supply_plan.begin_hunt("thunderape-west", {"potions": 10, "arrows": 1000}, now=0)
    assert not supply_plan.arrived("snakeman-south", {"potions": 9, "arrows": 900}, now=60)
    assert not supply_plan.arrived("snakeman-south", {"potions": 9, "arrows": 900}, now=60)
    supply_plan.write_json(supply_plan.RATES, {})
    assert not supply_plan.arrived("snakeman-south", {"potions": 9, "arrows": 900})


def test_the_hunt_loop_marks_the_arrival_once_inside_the_field(monkeypatch):
    # 3: two reads on the way in, two in the field, then out of arrows.
    from conquest import city_travel, conductress_shortcut, savings, world_travel
    from conquest import merchant_loop_acceptance
    from conquest.merchants import handoff

    route = RouteLibrary().load("snakeman-south")
    left, top, right, bottom = route.hunting_boundary
    field = ((left + right) // 2, (top + bottom) // 2)
    steps = [
        ((554, 545), 2000, 30),  # town
        ((431, 315), 2000, 22),  # the passage, healing while it waits
        (field, 2000, 22),  # arrived
        (field, 1200, 21),
        (field, 0, 21),  # out of arrows: back to town
    ]
    at = {"i": 0}
    clock = [0.0]

    def health():
        position = steps[min(at["i"], len(steps) - 1)][0]
        return {
            "embedded_controls": {
                "control": {"enabled": True, "execution_state": "farming"},
                "life": {
                    "position": position,
                    "map_id": route.map_id,
                    "dead_candidate": False,
                    "current_hp": 900,
                    "max_hp": 969,
                },
            }
        }

    def town(action, **fields):
        _, arrows, potions = steps[min(at["i"], len(steps) - 1)]
        at["i"] += 1
        clock[0] += 60
        items = [{"type_id": route.supplies.healing_type, "amount": 1, "uid": n} for n in range(potions)]
        items.append({"type_id": route.supplies.arrow_type, "amount": arrows, "uid": 999})
        return {"items": items, "equipped_ammo": None, "silver": 1000, "capacity": 40}

    events = []
    loop = OvernightLoop.__new__(OvernightLoop)
    loop.route, loop.info, loop.cycles, loop.first_hunt_seconds = route, "unused", 1, None
    loop.living = lambda: health()
    loop.health = health
    loop.focus = lambda h: None
    loop.town = town
    loop.record = lambda event, **fields: events.append(event)
    loop.stop_farm = lambda: None
    loop.select_level_route = lambda h=None: False
    loop.heavy_burn = lambda potions: False
    loop.boss_chase = lambda: False
    for target, name, value in [
        (world_travel, "travel_to_map", lambda loop, map_id: None),
        (city_travel, "ensure_city_visit", lambda loop, **kw: None),
        (conductress_shortcut, "ride", lambda loop: None),
        (overnight, "select_app_route", lambda loop: None),
        (overnight, "request", lambda *a, **k: None),
        (overnight, "read_status", lambda path: {}),
        (merchant_loop_acceptance, "observe_hunting", lambda loop, h: False),
        (savings, "progress", lambda loop, silver: False),
        (handoff, "service_window", lambda loop, **kw: None),
    ]:
        monkeypatch.setattr(target, name, value)
    monkeypatch.setattr(overnight.time, "sleep", lambda s: None)
    monkeypatch.setattr(supply_plan.time, "time", lambda: clock[0])
    supply_plan.begin_hunt(route.id, {"potions": 30, "arrows": 2000}, now=0)
    loop.hunt()
    assert events[-1] == "return_required"
    current = supply_plan.read_json(supply_plan.RATES)["current"]
    # Moved once, at the first read inside the field: the passage's heals are
    # not hunting use.
    assert current["arrived"] is True and current["potions"] == 22 and current["at"] == 180
