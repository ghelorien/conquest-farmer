"""A failed escape jump is noticed at once and retried somewhere else.

Live 2026-09-27 11:39 on Laptop2: three Apparitions stood adjacent to
Suicide (300 max HP), together taking about 180 HP a second. The escape
jump to (340, 601) never moved it; the next escape waited out its 0.9 s
cooldown, the loop attacked instead, and Suicide died 1.8 s after a full
heal. The four straight jump lines were the only escape candidates.

Failure modes, written before the code:
1. A jump that did not move the farmer goes unnoticed and the loop attacks
   while surrounded.
2. The retry picks the same failed destination again.
3. Only the four straight lines are tried, so a surround that blocks them
   leaves no escape although a diagonal is open.
4. A jump that did move (a slow position read) is taken for a failure.
5. Every destination failing makes the farmer retry forever.
"""

from types import SimpleNamespace as NS

from conquest import native_farm
from test_native_farm import setup


def surrounded(monkeypatch, clock, *, walkable=lambda p: True):
    supervisor, _, _, _ = setup(monkeypatch)
    monkeypatch.setattr(native_farm.time, "monotonic", lambda: clock[0])
    supervisor.scene_timestamp = clock[0]
    supervisor.escape_monsters = (
        NS(position=(21, 20)),
        NS(position=(20, 21)),
        NS(position=(19, 19)),
    )
    supervisor.recovery.terrain = NS(walkable=walkable)
    return supervisor


def test_failed_jump_retries_elsewhere_and_is_bounded(monkeypatch):
    clock = [100.0]
    supervisor = surrounded(monkeypatch, clock)
    box = (0, 0, 60, 60)
    first = supervisor.ranged_escape((20, 20), box)
    assert first is not None
    supervisor.escape_sent((20, 20), first)
    # 4: a position read 0.2 s later that has not caught up yet is not a failure.
    clock[0] += 0.2
    assert supervisor.escape_result((20, 20)) is None
    # 1: 0.45 s after the jump the farmer still stands at the source.
    clock[0] += 0.3
    assert supervisor.escape_result((20, 20)) == "failed"
    supervisor.scene_timestamp = clock[0]
    second = supervisor.ranged_escape((20, 20), box)
    # 2: the retry is allowed at once and never reuses the failed landing.
    assert second is not None and second != first
    supervisor.escape_sent((20, 20), second)
    clock[0] += 0.3
    assert supervisor.escape_result((20 + (second[0] - 20) // 2, second[1])) == "moved"
    assert supervisor.escape_failures == 0
    # 5: after repeated failures the quick retry stops.
    for _ in range(native_farm.ESCAPE_QUICK_RETRIES + 1):
        supervisor.scene_timestamp = clock[0]
        landing = supervisor.ranged_escape((20, 20), box)
        if landing is None:
            break
        supervisor.escape_sent((20, 20), landing)
        clock[0] += 0.5
        supervisor.escape_result((20, 20))
    assert not supervisor.escape_quick_retry()


def test_diagonal_escape_when_straight_lines_are_blocked(monkeypatch):
    # 3: walls on every straight line, open ground on a diagonal.
    clock = [100.0]

    def walkable(p):
        x, y = p
        return not (x == 20 or y == 20) or (x, y) == (20, 20)

    supervisor = surrounded(monkeypatch, clock, walkable=walkable)
    landing = supervisor.ranged_escape((20, 20), (0, 0, 60, 60))
    assert landing is not None
    dx, dy = landing[0] - 20, landing[1] - 20
    assert dx != 0 and dy != 0 and max(abs(dx), abs(dy)) >= 6
