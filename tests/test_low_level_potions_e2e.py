"""Low-level routes carry 20 Painkillers per trip.

User rule (2026-09-26): "It should buy 20 of the appropriate potions relative
to the total health he has." On 09-26 the level 8-11 farmer (99 max HP at
level 4) drank its 5 Painkillers within about two minutes of hunting, walked
back with none and died on the way on every trip. Painkiller is the only
potion the healing and travel code can buy and drink, and it suits these
levels; choosing a stronger potion as max HP grows is separate work.

Failure modes this module must catch (written before the implementation):

1. A low-level route (Pheasants through Winged Snakes, levels 1-31) still
   refills only 5 potions.
2. The 20-potion refill leaks into the higher routes: Bandits and its archived
   variants, Fire Spirits and Macaques keep the saved 5.
3. The saved return rule changes: the farmer heads back when its potions run
   out (not before) and when fewer than 3 arrows remain.
4. The town budget does not cover the whole refill, so the farmer leaves town
   short of potions.
5. The refill buys a potion the healing code cannot drink (not a Painkiller).
6. The Poltergeist savings plan stops pinning its low-cost 5-potion stock.

The test walks every saved route through the real route library, return rule
and town budget, and writes ``low-level-potions.json``; the scenario runs
twice in separate roots and must produce byte-identical artifacts.
"""

import json
from pathlib import Path

from conquest import banking, savings, session_plan
from conquest.overnight import needs_town, supply_counts
from conquest.routes import RouteLibrary

PAINKILLER = 1000020
PACK = {1050000: 200, 1050001: 1000, 1050002: 5000}
LOW = (
    "pheasant",
    "turtledove",
    "robin",
    "apparition",
    "poltergeist",
    "wingedsnake",
    # The WingedSnake herd's two halves (2026-09-29): same monsters and levels.
    "wingedsnake-west",
    "wingedsnake-east",
    # Twin City's fields restocked in Phoenix (2026-10-01): same levels.
    "apparition-phx",
    "poltergeist-phx",
)


def bag(route, potions, arrows=None):
    """Two full arrow packs (one equipped), so only healing is budgeted; with
    ``arrows`` the farmer carries just that many equipped arrows."""
    arrow = route.supplies.arrow_type
    pack = PACK[arrow]
    items = (
        []
        if arrows is not None
        else [{"type_id": arrow, "amount": pack, "limit": pack}]
    )
    items += [
        {"type_id": route.supplies.healing_type, "amount": 1, "limit": 1}
    ] * potions
    return {
        "items": items,
        "equipped_ammo": {
            "type_id": arrow,
            "amount": pack if arrows is None else arrows,
            "limit": pack,
        },
        "capacity": 40,
        "silver": 0,
    }


def _scenario(root, monkeypatch):
    root = Path(root)
    root.mkdir(parents=True)
    monkeypatch.setattr(session_plan, "PLAN", root / "no-plan.json")
    rows = {}
    for route in sorted(RouteLibrary().all(), key=lambda r: r.id):
        s = route.supplies
        full, empty = bag(route, s.healing_restock_to), bag(route, 0)

        def heads_back(snapshot):
            return bool(needs_town(supply_counts(snapshot, route), route))

        rows[route.id] = {
            "levels": list(route.recommended_levels),
            "healing_type": s.healing_type,
            "healing_restock_to": s.healing_restock_to,
            "healing_budget": banking.shopping_budget(route, empty)
            - banking.shopping_budget(route, full),
            "heads_back_with_1_potion": heads_back(bag(route, 1)),
            "heads_back_with_0_potions": heads_back(empty),
            "heads_back_with_2_arrows": heads_back(
                bag(route, s.healing_restock_to, arrows=2)
            ),
        }
    # Mode 6: an explicit Poltergeist savings plan keeps its low-cost stock.
    plan = root / "savings-plan.json"
    session_plan.write_json(
        plan,
        {
            "active": True,
            "mode": "save_silver",
            "route_id": "poltergeist",
            "upgrade_maps": [1002],
            "silver_target": 50000,
            "starting_silver": 961,
            "started_at": 123,
        },
    )
    monkeypatch.setattr(session_plan, "PLAN", plan)
    saving = savings.configure_route(RouteLibrary().load("poltergeist"), 5000)
    result = {
        "routes": rows,
        "poltergeist_savings_restock_to": saving.supplies.healing_restock_to,
    }
    path = root / "low-level-potions.json"
    path.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    return path


def test_low_level_potions_e2e(tmp_path, monkeypatch):
    first = _scenario(tmp_path / "pc-a", monkeypatch)
    second = _scenario(tmp_path / "pc-b", monkeypatch)
    assert first.read_bytes() == second.read_bytes()
    result = json.loads(first.read_text(encoding="utf-8"))
    routes = result["routes"]
    assert set(LOW) <= set(routes)
    for route_id, row in routes.items():
        low = route_id in LOW
        # Modes 1 and 2: 20 on the low-level routes only; levels 1-31 exactly.
        assert low == (row["levels"][1] <= 31), route_id
        assert row["healing_restock_to"] == (20 if low else 5), route_id
        # Mode 4: the town budget covers the whole refill (60 silver each).
        assert row["healing_budget"] == row["healing_restock_to"] * 60, route_id
        # Mode 5: only the Painkiller the healing code can drink.
        assert row["healing_type"] == PAINKILLER, route_id
        # Mode 3: head back when potions run out or arrows fall below 3.
        assert row["heads_back_with_0_potions"] is True, route_id
        assert row["heads_back_with_1_potion"] is False, route_id
        assert row["heads_back_with_2_arrows"] is True, route_id
    # Mode 6.
    assert result["poltergeist_savings_restock_to"] == 5
