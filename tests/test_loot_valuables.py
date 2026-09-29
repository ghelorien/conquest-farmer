"""Valuables keep their loot turn while an overdue Scatter makes silver wait,
and a walk toward one may leave the hunting boundary: by the slack, and
anywhere within VALUABLE_RADIUS of the farmer.

Toxic 2026-09-28: a Super MeteorEarring 23 tiles off was lost at 14:51 while
whole loot turns were skipped for the Scatter, and 8 Uniques at x 363-384,
past the WingedSnake boundary's 352, were deferred as "Loot path leaves
hunting boundary". Alex 2026-09-29: "There should be a 25 tile radius for
valuables".
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


def test_no_loot_inside_a_boss_clearance(monkeypatch):
    # Both RatKings parked just outside the Ratling boundary (2026-09-28).
    supervisor, _, _, notes = setup(monkeypatch)
    meteor = GroundItem(1, 1000, 1088001, (12, 10))
    supervisor.ground_items = lambda: (meteor,)
    supervisor.escape_monsters = (SimpleNamespace(name="RatKing", position=(24, 10)),)
    supervisor.king_clearance = 15
    no_click = lambda *a, **kw: (_ for _ in ()).throw(AssertionError("no click"))
    supervisor.loot_step(BAG, (10, 10), no_click)
    observed = [f for e, f in notes if e == "memory_loot_observed"][-1]
    assert observed["valuable_drops"][0]["reason"] == "boss_nearby"


def test_a_loot_walk_never_passes_a_boss(monkeypatch):
    supervisor, notes, clicks, step = meteor_field(monkeypatch, (50, 63))
    # An Aide 11 tiles from the Meteor but 9 from the walk down x=50.
    supervisor.escape_monsters = (SimpleNamespace(name="RatAide", position=(41, 52)),)
    step()
    assert not any(event == "memory_pickup_approach" for event, _ in notes)
    assert any(
        fields.get("detail") == "Loot path passes a boss"
        for event, fields in notes
        if event == "memory_pickup_deferred"
    )


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


def test_a_valuable_within_25_tiles_is_walked_to_past_the_slack(monkeypatch):
    # Alex 2026-09-29: "There should be a 25 tile radius for valuables".
    # 16 tiles past the bottom edge (beyond the 12-tile slack), 24 from us.
    supervisor, notes, clicks, step = meteor_field(monkeypatch, (50, 74))
    supervisor.loot_boundary = (40, 40, 60, 58)
    assert native_farm.VALUABLE_RADIUS == 25
    assert step()
    assert any(event == "memory_pickup_approach" for event, _ in notes)
    # The trial keeps walking outside the boundary while this holds.
    assert supervisor.valuable_chase_holds((50, 50))
    assert supervisor.valuable_chase_holds((50, 66))


def test_a_valuable_chase_ends_when_the_drop_leaves_the_ground(monkeypatch):
    supervisor, notes, clicks, step = meteor_field(monkeypatch, (50, 74))
    supervisor.loot_boundary = (40, 40, 60, 58)
    assert step() and supervisor.valuable_chase_holds((50, 50))
    supervisor.ground_items = lambda: ()  # picked up by someone else, or gone
    supervisor.loot_step(BAG, (50, 50), lambda *a, **kw: None)
    assert not supervisor.valuable_chase_holds((50, 50))


def test_a_valuable_chase_lapses_without_a_step(monkeypatch):
    supervisor, notes, clicks, step = meteor_field(monkeypatch, (50, 74))
    supervisor.loot_boundary = (40, 40, 60, 58)
    assert step()
    later = native_farm.time.monotonic() + native_farm.VALUABLE_CHASE_SECONDS + 1
    monkeypatch.setattr(native_farm.time, "monotonic", lambda: later)
    assert not supervisor.valuable_chase_holds((50, 50))


def test_a_valuable_chase_holds_only_within_the_radius(monkeypatch):
    supervisor, notes, clicks, step = meteor_field(monkeypatch, (50, 74))
    supervisor.loot_boundary = (40, 40, 60, 58)
    assert step()
    # Carried more than 25 tiles from the drop (say, by an escape): the
    # boundary return takes over.
    assert not supervisor.valuable_chase_holds((50, 48))
