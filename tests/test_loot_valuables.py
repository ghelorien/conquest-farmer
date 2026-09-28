"""Valuables keep their loot turn while an overdue Scatter makes silver wait,
and a walk toward one may go a little past the hunting boundary.

Toxic 2026-09-28: a Super MeteorEarring 23 tiles off was lost at 14:51 while
whole loot turns were skipped for the Scatter, and 8 Uniques at x 363-384,
past the WingedSnake boundary's 352, were deferred as "Loot path leaves
hunting boundary".
"""

from types import SimpleNamespace

from conquest import native_farm
from conquest.memory_ground import GroundItem
from test_native_farm import meteor_field, setup

BAG = SimpleNamespace(silver=0, items=(), capacity=40)


def test_an_overdue_scatter_skips_silver_but_still_takes_a_valuable(monkeypatch):
    supervisor, _, _, _ = setup(monkeypatch)
    silver = GroundItem(2, 2000, 1090010, (11, 10), spawn_tick=1)
    meteor = GroundItem(1, 1000, 1088001, (12, 10))
    supervisor.ground_items = lambda: (silver, meteor)
    clicks = []
    assert supervisor.loot_step(
        BAG, (10, 10), lambda point, **kw: clicks.append(kw["drop"]), valuables_only=True
    )
    assert clicks == [meteor]


def test_an_overdue_scatter_neither_walks_to_silver_nor_waits_for_drops(monkeypatch):
    supervisor, _, _, _ = setup(monkeypatch)
    silver = GroundItem(2, 2000, 1090010, (11, 10), spawn_tick=1)
    supervisor.ground_items = lambda: (silver,)
    supervisor.loot_wait_until = native_farm.time.monotonic() + 10
    no_click = lambda *a, **kw: (_ for _ in ()).throw(AssertionError("no silver click"))
    assert supervisor.loot_step(BAG, (10, 10), no_click, valuables_only=True) is False
    supervisor.ground_items = lambda: ()
    # Without an overdue cast the kill-drop wait still holds the turn.
    assert supervisor.loot_step(BAG, (10, 10), no_click) is True


def test_a_pending_silver_walk_does_not_hold_back_a_valuable(monkeypatch):
    supervisor, _, _, _ = setup(monkeypatch)
    silver = GroundItem(2, 2000, 1090010, (14, 10), spawn_tick=1)
    meteor = GroundItem(1, 1000, 1088001, (12, 10))
    supervisor.ground_items = lambda: (silver, meteor)
    supervisor.pending_loot = (silver, BAG, native_farm.time.monotonic())
    clicks = []
    assert supervisor.loot_step(
        BAG, (10, 10), lambda point, **kw: clicks.append(kw["drop"]), valuables_only=True
    )
    assert clicks == [meteor]


def test_a_valuable_walk_may_pass_the_hunting_boundary_by_the_slack(monkeypatch):
    # 5 tiles past the boundary's bottom edge: walked to.
    supervisor, notes, clicks, step = meteor_field(monkeypatch, (50, 63))
    supervisor.loot_boundary = (40, 40, 60, 58)
    assert step()
    assert any(event == "memory_pickup_approach" for event, _ in notes)
    # 22 tiles past it: still deferred.
    supervisor, notes, clicks, step = meteor_field(monkeypatch, (50, 80))
    supervisor.loot_boundary = (40, 40, 60, 58)
    step()
    assert not any(event == "memory_pickup_approach" for event, _ in notes)
    assert any(
        fields.get("detail") == "Loot path leaves hunting boundary"
        for event, fields in notes
        if event == "memory_pickup_deferred"
    )
