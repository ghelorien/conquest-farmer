"""A restarted controller keeps a real potion reserve for the walk home.

Live 2026-09-28 08:01-08:02 (Suicide, Bandits): after a deploy the route came
fresh from bandit.yaml (healing_restock_to 5) until the next planned restock,
so the trip reserve was 5 // 4 = 1 although the hunt had left town with 18.
Suicide turned home on its last potion and died on the way.

Failure modes, written before the change:
1. A fresh route's small file fill sets the reserve below a quarter of what
   the hunt left town with.
2. A poor departure keeps more than half of its potions (the existing cap).
3. Farmers without the protections keep a reserve.
"""

from types import SimpleNamespace as NS

from conquest import level_goal
from conquest.overnight import needs_town, potion_reserve


def route(fill):
    return NS(supplies=NS(healing_restock_to=fill))


def counts(potions):
    return {"arrows": 500, "potions": potions, "free_slots": 10, "silver": 0}


def test_a_fresh_route_file_does_not_shrink_the_reserve(monkeypatch):
    # 1
    monkeypatch.setattr(level_goal, "protections", lambda: True)
    assert potion_reserve(route(5), departed=18) == 4
    assert potion_reserve(route(31), departed=31) == 5
    # The live case: out with 18, the walk home starts with 4, not 1.
    assert needs_town(counts(4), route(5), departed=18, reserve=True)
    assert not needs_town(counts(5), route(5), departed=18, reserve=True)
    # Without a known departure the file fill still decides.
    assert potion_reserve(route(5)) == 1


def test_a_poor_departure_keeps_at_most_half(monkeypatch):
    # 2
    monkeypatch.setattr(level_goal, "protections", lambda: True)
    assert potion_reserve(route(31), departed=6) == 3
    assert potion_reserve(route(5), departed=3) == 1


def test_no_reserve_without_the_protections(monkeypatch):
    # 3
    monkeypatch.setattr(level_goal, "protections", lambda: False)
    assert potion_reserve(route(31), departed=31) == 0
