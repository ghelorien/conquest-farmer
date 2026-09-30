"""Under fire with no ordinary landing, the archer still jumps clear.

Suicide 2026-09-30 14:32:16 on thunderape-nw [300,242,348,318]: a jump left it
at (299, 312), 1 tile outside the west edge, with ThunderApes at 1-3 tiles
and a King near. Landings west lay outside the box, those north-east in the
King's reach, and the boss flight only runs for a boss inside its clearance,
so no escape was tried for 14 s while it healed and died.

Failure modes, written before the fix:
1. Damaged, with every in-box landing ruled out, ranged_escape returns None.
2. A flight lands inside a boss's reach or still within the hitters' reach.
3. Without damage, or with a qualifying landing, the old choice changes.
"""

from types import SimpleNamespace as NS

BOX = (300, 242, 348, 318)
HERE = (299, 312)
APES = [(299, 309), (298, 313), (303, 321), (306, 308), (301, 311)]
KING = (318, 296)  # north-east: in reach of every landing that way


def supervisor_at(monkeypatch, monsters, *, damaged, walkable=None):
    from test_native_farm import setup
    from conquest import native_farm

    supervisor, _, _, _ = setup(monkeypatch)
    monkeypatch.setattr(native_farm.time, "monotonic", lambda: 100.0)
    supervisor.scene_timestamp = 100.0
    supervisor.recovery.terrain = NS(walkable=walkable or (lambda p: True))
    supervisor.king_clearance = 15
    supervisor.elite_clearance = 13
    supervisor.escape_monsters = tuple(monsters)
    if damaged:
        supervisor.last_damage_at = 100.0
        supervisor.escape_damage_consumed_at = 0.0
    else:
        supervisor.last_damage_at = -1e9
    return supervisor, native_farm


def scene():
    return [NS(name="ThunderApeKing", position=KING)] + [
        NS(name="ThunderApe", position=p) for p in APES
    ]


def cheb(a, b):
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def escape(supervisor, native_farm):
    return supervisor.ranged_escape(
        HERE, BOX, adjacent_trigger=1, reach=native_farm.JUMP_SCATTER_REACH, scatter_range=8
    )


def test_only_a_flight_past_the_boundary_is_left_when_the_box_is_all_ruled_out(monkeypatch):
    # 1: make every in-box tile unwalkable beyond the farmer's own column, so
    # only landings west (outside the box) remain; the old code returned None.
    def walkable(p):
        return p[0] <= 300

    supervisor, native_farm = supervisor_at(monkeypatch, scene(), damaged=True, walkable=walkable)
    landing = escape(supervisor, native_farm)
    assert landing is not None and landing[0] < BOX[0]
    assert supervisor.escape_context["reason"] == "damage_flight"
    assert cheb(landing, KING) > 15
    assert min(cheb(landing, a) for a in APES) > native_farm.JUMP_SCATTER_REACH


def test_no_flight_without_damage(monkeypatch):
    # 3: the same scene unhurt keeps the old behaviour.
    def walkable(p):
        return p[0] <= 300

    supervisor, native_farm = supervisor_at(monkeypatch, scene(), damaged=False, walkable=walkable)
    assert escape(supervisor, native_farm) is None


def test_no_flight_into_a_boss_or_the_crowd(monkeypatch):
    # 2: west is walkable but a second King holds it; nothing else is walkable.
    def walkable(p):
        return p[0] <= 300

    monsters = scene() + [NS(name="ThunderApeKing", position=(284, 312))]
    supervisor, native_farm = supervisor_at(monkeypatch, monsters, damaged=True, walkable=walkable)
    landing = escape(supervisor, native_farm)
    assert landing is None or (
        cheb(landing, (284, 312)) > 15 and cheb(landing, KING) > 15
    )
