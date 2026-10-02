"""Valuables keep their loot turn while an overdue Scatter makes silver wait,
and a walk toward one may leave the hunting boundary: by the slack, and
anywhere within VALUABLE_RADIUS of the farmer or of the drop.

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


def test_a_valuable_beside_a_boss_is_taken_unless_about_to_die(monkeypatch):
    # Alex 2026-10-01 07:2x, after a +1 lay 11 s beside a Bandit boss until
    # he took it by hand: "Highest priority is always picking up valuable loot
    # over anything else. The only exception is if you are about to die."
    supervisor, _, _, notes = setup(monkeypatch)
    meteor = GroundItem(1, 1000, 1088001, (12, 10))
    supervisor.ground_items = lambda: (meteor,)
    supervisor.escape_monsters = (SimpleNamespace(name="RatKing", position=(24, 10)),)
    supervisor.king_clearance = 15
    clicks = []
    assert supervisor.loot_step(BAG, (10, 10), lambda point, **kw: clicks.append(kw["drop"]))
    assert clicks == [meteor]
    # About to die (under LOOT_FIRST_HP): the boss's clearance comes first,
    # as it did for both RatKings parked by the Ratling boundary (2026-09-28).
    supervisor, _, _, notes = setup(monkeypatch)
    supervisor.ground_items = lambda: (meteor,)
    supervisor.escape_monsters = (SimpleNamespace(name="RatKing", position=(24, 10)),)
    supervisor.king_clearance = 15
    supervisor.health_share = native_farm.LOOT_FIRST_HP - 0.01
    no_click = lambda *a, **kw: (_ for _ in ()).throw(AssertionError("no click"))
    supervisor.loot_step(BAG, (10, 10), no_click)
    observed = [f for e, f in notes if e == "memory_loot_observed"][-1]
    assert observed["valuable_drops"][0]["reason"] == "boss_nearby"


def test_silver_never_walks_into_a_boss_clearance(monkeypatch):
    supervisor, _, _, notes = setup(monkeypatch)
    silver = GroundItem(2, 2000, 1090010, (12, 10), spawn_tick=1)
    supervisor.ground_items = lambda: (silver,)
    supervisor.own_kill_drop = lambda drop: True
    monkeypatch.setattr("conquest.memory_ground.wanted_drop", lambda drop: True)
    supervisor.escape_monsters = (SimpleNamespace(name="RatKing", position=(24, 10)),)
    supervisor.king_clearance = 15
    no_click = lambda *a, **kw: (_ for _ in ()).throw(AssertionError("no click"))
    supervisor.loot_step(BAG, (10, 10), no_click)
    observed = [f for e, f in notes if e == "memory_loot_observed"][-1]
    assert observed["valuable_drops"][0]["reason"] == "boss_nearby"


def test_a_loot_walk_passes_a_boss_unless_about_to_die(monkeypatch):
    supervisor, notes, clicks, step = meteor_field(monkeypatch, (50, 63))
    # An Aide 11 tiles from the Meteor but 9 from the walk down x=50.
    supervisor.escape_monsters = (SimpleNamespace(name="RatAide", position=(41, 52)),)
    assert step()
    assert any(event == "memory_pickup_approach" for event, _ in notes)
    supervisor, notes, clicks, step = meteor_field(monkeypatch, (50, 63))
    supervisor.escape_monsters = (SimpleNamespace(name="RatAide", position=(41, 52)),)
    supervisor.health_share = native_farm.LOOT_FIRST_HP - 0.01
    step()
    assert not any(event == "memory_pickup_approach" for event, _ in notes)
    assert any(
        fields.get("detail") == "Loot path passes a boss"
        for event, fields in notes
        if event == "memory_pickup_deferred"
    )


def test_a_valuable_pickup_under_way_outranks_every_escape(monkeypatch):
    from conquest.trial import loot_first_now, loot_outranks_escape

    supervisor, notes, clicks, step = meteor_field(monkeypatch, (50, 63))
    assert not loot_outranks_escape(supervisor, (50, 50))  # nothing under way yet
    assert step() and supervisor.valuable_pending((50, 50))
    assert loot_outranks_escape(supervisor, (50, 50)) and loot_first_now(supervisor)
    # About to die: the escapes come back.
    supervisor.health_share = native_farm.LOOT_FIRST_HP - 0.01
    assert not loot_outranks_escape(supervisor, (50, 50))
    assert not loot_first_now(supervisor)
    # A silver click awaiting its receipt is no reason to skip an escape.
    supervisor, _, _, _ = setup(monkeypatch)
    silver = GroundItem(2, 2000, 1090010, (12, 10), spawn_tick=1)
    supervisor.pending_loot = (silver, BAG, native_farm.time.monotonic())
    assert not loot_outranks_escape(supervisor, (10, 10))
    # Supervisors without the loot policy (other farm modes) never skip one.
    assert not loot_outranks_escape(SimpleNamespace(), (10, 10))
    assert not loot_outranks_escape(None, (10, 10))


def test_a_valuable_walk_may_pass_the_hunting_boundary_by_the_slack(monkeypatch):
    # 5 tiles past the boundary's bottom edge: walked to.
    supervisor, notes, clicks, step = meteor_field(monkeypatch, (50, 63))
    supervisor.loot_boundary = (40, 40, 60, 58)
    assert step()
    assert any(event == "memory_pickup_approach" for event, _ in notes)


def test_a_valuable_anywhere_in_the_search_is_walked_to(monkeypatch):
    # 2026-10-01: a Meteor 38 tiles off at (546,377), 33 past the Bandit box,
    # was deferred as "Loot path leaves hunting boundary"; Alex approved
    # valuables pulling the farmer past the hunt's edge (2026-10-02). 22 tiles
    # past the edge and 30 from the farmer: every step lies within 25 of the
    # farmer or of the drop.
    supervisor, notes, clicks, step = meteor_field(monkeypatch, (50, 80))
    supervisor.loot_boundary = (40, 40, 60, 58)
    assert step()
    assert any(event == "memory_pickup_approach" for event, _ in notes)
    assert not any(
        fields.get("detail") == "Loot path leaves hunting boundary"
        for event, fields in notes
        if event == "memory_pickup_deferred"
    )
    # Past the 40-tile search: never walked to.
    supervisor, notes, clicks, step = meteor_field(monkeypatch, (50, 95))
    supervisor.loot_boundary = (40, 40, 60, 58)
    step()
    assert not any(event == "memory_pickup_approach" for event, _ in notes)
    observed = [f for e, f in notes if e == "memory_loot_observed"][-1]
    assert observed["valuable_drops"][0]["reason"] == "outside_40_tile_search"


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


def test_a_farmer_still_moving_from_its_last_step_keeps_the_chase(monkeypatch):
    # Suicide 2026-09-29: the turn after each approach step raised "Player
    # projection changed before loot input" (the farmer still moving). That
    # ended the chase, and combat walked 12+ tiles away between steps.
    from conquest.capture import CaptureUnavailable

    supervisor, notes, clicks, step = meteor_field(monkeypatch, (50, 60))
    assert step() and supervisor.valuable_chase_holds((50, 50))
    later = native_farm.time.monotonic() + 1  # past the 0.6 s step settle
    monkeypatch.setattr(native_farm.time, "monotonic", lambda: later)

    def moving(point, **kw):
        raise CaptureUnavailable("Player projection changed before loot input; reobserving")

    assert supervisor.loot_step(BAG, (50, 50), moving)  # combat does not get the turn
    assert supervisor.valuable_chase_holds((50, 50))
    assert any(
        fields.get("detail", "").endswith("changed before loot input; reobserving")
        for event, fields in notes
        if event == "memory_pickup_deferred"
    )


def test_a_lasting_approach_error_still_ends_the_chase(monkeypatch):
    supervisor, notes, clicks, step = meteor_field(monkeypatch, (50, 60))
    assert step() and supervisor.valuable_chase_holds((50, 50))
    later = native_farm.time.monotonic() + 1
    monkeypatch.setattr(native_farm.time, "monotonic", lambda: later)

    def refused(point, **kw):
        raise ValueError("Loot input refused")

    assert not supervisor.loot_step(BAG, (50, 50), refused)
    assert not supervisor.valuable_chase_holds((50, 50))


def test_unverified_valuable_clicks_keep_the_chase_then_back_off(monkeypatch):
    # After a missed click the retry from beside the drop keeps the turn, but
    # a drop that is never picked up waits VALUABLE_MISS_COOLDOWN seconds.
    supervisor, notes, clicks, step = meteor_field(monkeypatch, (50, 52))
    assert step() and supervisor.pending_loot
    meteor = supervisor.pending_loot[0]
    key = (meteor.uid, meteor.object_address)
    now = [native_farm.time.monotonic()]
    monkeypatch.setattr(native_farm.time, "monotonic", lambda: now[0])
    for miss in range(1, native_farm.VALUABLE_CLICK_MISSES + 1):
        now[0] += 10
        supervisor.pending_loot = (meteor, BAG, now[0] - 3.5)  # nothing gained
        supervisor.loot_step(BAG, (50, 50), lambda *a, **kw: None)
        assert supervisor.pending_loot is None
        if miss < native_farm.VALUABLE_CLICK_MISSES:
            assert supervisor.loot_cooldowns[key] == now[0] + 1
            assert supervisor.valuable_chase_holds((50, 50))
        else:
            assert (
                supervisor.loot_cooldowns[key]
                == now[0] + native_farm.VALUABLE_MISS_COOLDOWN
            )
            assert not supervisor.valuable_chase_holds((50, 50))
            assert key not in supervisor.loot_misses
    assert [e for e, _ in notes].count(
        "memory_pickup_unverified"
    ) == native_farm.VALUABLE_CLICK_MISSES
