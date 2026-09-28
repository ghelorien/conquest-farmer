"""With a boss inside its clearance and no ordinary landing, the archer still
jumps away from it instead of standing still.

Toxic 2026-09-28 14:27:18 on Ratlings: at (532,508), west of the hunting
boundary (537-580 x 445-527), every landing inside the boundary lay within
the RatKing's 15-tile clearance, so no escape was tried for 14 s while the
King closed from 8 to 2 tiles and killed it.
"""

from types import SimpleNamespace as NS


def supervisor_at(monkeypatch, monsters, *, king_clearance=15):
    from test_native_farm import setup
    from conquest import native_farm

    supervisor, _, _, _ = setup(monkeypatch)
    monkeypatch.setattr(native_farm.time, "monotonic", lambda: 100.0)
    supervisor.scene_timestamp = 100.0
    supervisor.recovery.terrain = NS(walkable=lambda p: True)
    supervisor.king_clearance = king_clearance
    supervisor.escape_monsters = tuple(monsters)
    return supervisor, native_farm


def test_flees_a_king_past_the_boundary_when_nothing_else_qualifies(monkeypatch):
    king = NS(name="RatKing", position=(534, 506))
    rats = [NS(name="FireRatL38", position=p) for p in ((540, 512), (545, 500))]
    supervisor, native_farm = supervisor_at(monkeypatch, [king, *rats])
    kwargs = dict(adjacent_trigger=1, reach=native_farm.JUMP_SCATTER_REACH, scatter_range=8)
    landing = supervisor.ranged_escape((532, 508), (537, 445, 580, 527), **kwargs)
    assert landing is not None
    before = max(abs(532 - 534), abs(508 - 506))
    after = max(abs(landing[0] - 534), abs(landing[1] - 506))
    assert after > before and after >= 8
    assert supervisor.escape_context["reason"] == "boss_flight"


def test_ordinary_escapes_are_unchanged_when_a_landing_qualifies(monkeypatch):
    # Room inside the boundary and outside the King's clearance: no flight.
    king = NS(name="RatKing", position=(560, 470))
    supervisor, native_farm = supervisor_at(monkeypatch, [king])
    kwargs = dict(adjacent_trigger=1, reach=native_farm.JUMP_SCATTER_REACH, scatter_range=8)
    landing = supervisor.ranged_escape((560, 480), (500, 445, 620, 560), **kwargs)
    assert landing is not None
    assert max(abs(landing[0] - 560), abs(landing[1] - 470)) > 15
    assert 500 <= landing[0] <= 620 and 445 <= landing[1] <= 560
    assert supervisor.escape_context["reason"] == "boss_nearby"


def test_no_flight_without_a_boss_inside_its_clearance(monkeypatch):
    # A monster pack with no landing at all stays the old behaviour (None).
    king = NS(name="RatKing", position=(560, 470))  # 38 tiles away
    supervisor, native_farm = supervisor_at(monkeypatch, [king])
    supervisor.recovery.terrain = NS(walkable=lambda p: p == (532, 508))
    kwargs = dict(adjacent_trigger=1, reach=native_farm.JUMP_SCATTER_REACH, scatter_range=8)
    supervisor.escape_monsters = (king, NS(name="FireRatL38", position=(533, 508)))
    assert supervisor.ranged_escape((532, 508), (537, 445, 580, 527), **kwargs) is None
