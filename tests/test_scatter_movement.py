from types import SimpleNamespace
import numpy as np
from conquest.navigation import TerrainMap
from conquest.scatter_movement import scatter_landing, clear_jump
import pytest


def target(x, y, hp=100):
    return SimpleNamespace(world_position=(x, y), current_hp=hp)


def test_landing_prefers_dense_group_and_only_long_clear_segments():
    terrain = TerrainMap(1011, 100, 100, np.zeros((100, 100), dtype=bool), "", (), ())
    supervisor = SimpleNamespace(recovery=SimpleNamespace(terrain=terrain))
    targets = [target(61, 50), target(62, 50), target(63, 51), target(38, 50)]
    landing = scatter_landing(supervisor, targets, (50, 50), (20, 20, 80, 80), 10)
    assert landing[0] > 50  # the dense group's side, not the lone (38, 50)
    assert (
        sum(
            max(
                abs(p.world_position[0] - landing[0]),
                abs(p.world_position[1] - landing[1]),
            )
            <= 10
            for p in targets
        )
        >= 3
    )
    assert 8 <= max(abs(landing[0] - 50), abs(landing[1] - 50)) <= 12
    terrain.blocked[50, 53] = True
    landing = scatter_landing(supervisor, targets, (50, 50), (20, 20, 80, 80), 10)
    assert landing is None or clear_jump(terrain, (50, 50), landing)


def test_a_landing_keeps_a_tile_inside_the_boundary():
    # A jump planned onto the edge tile left Suicide a tile outside
    # thunderape-nw at (299, 312) (2026-09-30 14:32): landings keep
    # LANDING_EDGE_MARGIN inside it.
    from conquest.scatter_movement import LANDING_EDGE_MARGIN

    terrain = TerrainMap(1011, 100, 100, np.zeros((100, 100), dtype=bool), "", (), ())
    supervisor = SimpleNamespace(recovery=SimpleNamespace(terrain=terrain))
    boundary = (40, 20, 80, 80)
    landings = []
    # Groups at every depth beside the west edge, the farmer 8-12 tiles east.
    for gx in range(41, 50):
        for farmer_x in range(48, 53):
            targets = [target(gx, 50), target(gx, 53), target(gx + 1, 47)]
            landing = scatter_landing(supervisor, targets, (farmer_x, 50), boundary, 10)
            if landing is not None:
                landings.append(landing)
    assert landings
    for x, y in landings:
        assert boundary[0] + LANDING_EDGE_MARGIN <= x <= boundary[2] - LANDING_EDGE_MARGIN
        assert boundary[1] + LANDING_EDGE_MARGIN <= y <= boundary[3] - LANDING_EDGE_MARGIN


def test_no_short_jump_or_dead_target_chasing():
    terrain = TerrainMap(1011, 100, 100, np.zeros((100, 100), dtype=bool), "", (), ())
    supervisor = SimpleNamespace(recovery=SimpleNamespace(terrain=terrain))
    assert (
        scatter_landing(supervisor, [target(51, 50)], (50, 50), (45, 45, 55, 55), 10)
        is None
    )
    assert (
        scatter_landing(supervisor, [target(60, 50, 0)], (50, 50), (20, 20, 80, 80), 10)
        is None
    )


def test_outside_hunt_groups_cannot_trap_jumps_along_the_boundary():
    terrain = TerrainMap(1011, 100, 100, np.zeros((100, 100), dtype=bool), "", (), ())
    outside = [target(71, 50 + i) for i in range(8)]
    supervisor = SimpleNamespace(
        recovery=SimpleNamespace(terrain=terrain), scatter_scene_targets=outside
    )
    assert scatter_landing(supervisor, outside, (70, 50), (20, 20, 70, 80), 10) is None
    supervisor.scatter_scene_targets = outside + [target(55, 50)]
    landing = scatter_landing(supervisor, [], (70, 50), (20, 20, 70, 80), 5)
    assert landing is not None and max(abs(landing[0] - 55), abs(landing[1] - 50)) <= 5


def test_fewer_turns_still_uses_shortest_walkable_path():
    blocked = np.zeros((30, 30), dtype=bool)
    blocked[:20, 14] = True
    terrain = TerrainMap(1011, 30, 30, blocked, "", (), ())
    before = terrain.path((3, 3), (25, 25))
    after = terrain.straight_path((3, 3), (25, 25))
    turns = lambda p: sum(
        (b[0] - a[0], b[1] - a[1]) != (c[0] - b[0], c[1] - b[1])
        for a, b, c in zip(p, p[1:], p[2:])
    )
    assert len(before) == len(after) and turns(after) <= turns(before)
    assert all(terrain.walkable(p) for p in after)
    assert all(
        abs(a[0] - b[0]) + abs(a[1] - b[1]) == 1 for a, b in zip(after, after[1:])
    )
    assert (3, 4) not in terrain.straight_path((3, 3), (25, 25), avoid=[(3, 4)])


def test_full_memory_scene_steers_toward_larger_group_behind_current_hud():
    terrain = TerrainMap(1011, 100, 100, np.zeros((100, 100), dtype=bool), "", (), ())
    scene = [
        target(60, 57),
        target(61, 57),
        target(61, 58),
        target(63, 58),
        target(60, 59),
    ]
    supervisor = SimpleNamespace(
        recovery=SimpleNamespace(terrain=terrain), scatter_scene_targets=scene
    )
    landing = scatter_landing(
        supervisor, [target(42, 50)], (50, 50), (20, 20, 80, 80), 5, minimum_count=3
    )
    assert landing is not None and landing[0] > 50 and landing[1] > 50
    assert 8 <= max(abs(a - b) for a, b in zip(landing, (50, 50))) <= 12
    assert clear_jump(terrain, (50, 50), landing)


def test_diagonal_jump_cannot_cut_blocked_corner():
    terrain = TerrainMap(1011, 30, 30, np.zeros((30, 30), dtype=bool), "", (), ())
    assert clear_jump(terrain, (10, 10), (18, 14))
    terrain.blocked[10, 11] = True
    assert not clear_jump(terrain, (10, 10), (18, 14))


@pytest.mark.parametrize("anchor", [(510, 313), (510, 250), (400, 500)])
def test_flying_camera_anchor_never_selects_a_jump_the_input_guard_rejects(anchor):
    terrain = TerrainMap(1011, 100, 100, np.zeros((100, 100), dtype=bool), "", (), ())
    supervisor = SimpleNamespace(recovery=SimpleNamespace(terrain=terrain))
    targets = [target(40, 40), target(41, 39)]
    landing = scatter_landing(
        supervisor, targets, (50, 50), (20, 20, 80, 80), 10, anchor=anchor
    )
    if landing:
        dx, dy = landing[0] - 50, landing[1] - 50
        px, py = anchor[0] + (dx - dy) * 32, anchor[1] + (dx + dy) * 16
        assert 80 < px < 956 and 140 < py < 667
        assert not (px < 615 and (py > 550 or py < 170))
    if anchor == (510, 313):
        assert landing is not None


def test_recently_failed_landing_is_not_reselected(monkeypatch):
    from conquest import scatter_movement as sm

    monkeypatch.setattr(sm.time, "monotonic", lambda: 100.0)
    terrain = TerrainMap(1011, 100, 100, np.zeros((100, 100), dtype=bool), "", (), ())
    supervisor = SimpleNamespace(recovery=SimpleNamespace(terrain=terrain))
    targets = [target(61, 50), target(62, 50), target(63, 51)]
    first = scatter_landing(supervisor, targets, (50, 50), (20, 20, 80, 80), 10)
    supervisor.movement_obstructions = {(1011, first): 130.0}
    assert scatter_landing(supervisor, targets, (50, 50), (20, 20, 80, 80), 10) != first


def test_dense_pack_landing_avoids_actor_tiles_and_click_boxes():
    terrain = TerrainMap(1011, 100, 100, np.zeros((100, 100), dtype=bool), "", (), ())
    supervisor = SimpleNamespace(recovery=SimpleNamespace(terrain=terrain))
    targets = [target(60, 50), target(61, 50), target(60, 51)]
    for t in targets:
        dx, dy = t.world_position[0] - 50, t.world_position[1] - 50
        t.x = 518 + (dx - dy) * 32
        t.y = 396 + (dx + dy) * 16
    landing = scatter_landing(supervisor, targets, (50, 50), (20, 20, 80, 80), 10)
    assert landing is not None and landing not in [t.world_position for t in targets]
    dx, dy = landing[0] - 50, landing[1] - 50
    px, py = 518 + (dx - dy) * 32, 396 + (dx + dy) * 16
    assert not any(abs(px - t.x) <= 24 and -40 <= py - t.y <= 10 for t in targets)


def test_jump_scatter_never_lands_within_a_monsters_reach():
    # Alex: "you can't let enemies ever attack you, if enemies are within 1
    # tile of you you gotta jump scatter".
    terrain = TerrainMap(1011, 100, 100, np.zeros((100, 100), dtype=bool), "", (), ())
    supervisor = SimpleNamespace(recovery=SimpleNamespace(terrain=terrain))
    # A scattered pack 8-12 tiles out: many landings sit beside a monster.
    targets = [target(58 + dx, 44 + dy) for dx in (0, 3, 6) for dy in (0, 3, 6, 9)]
    landing = scatter_landing(supervisor, targets, (50, 50), (20, 20, 80, 80), 10)
    assert landing is not None
    assert all(
        max(abs(t.world_position[0] - landing[0]), abs(t.world_position[1] - landing[1]))
        >= 2
        for t in targets
    )


def test_equal_reach_landing_keeps_the_pack_beyond_five_tiles():
    # Monsters close in during a jump: with more of them planned within five
    # tiles the next move was more often an escape than a cast (13% -> 51%).
    terrain = TerrainMap(1011, 100, 100, np.zeros((100, 100), dtype=bool), "", (), ())
    supervisor = SimpleNamespace(recovery=SimpleNamespace(terrain=terrain))
    targets = [target(66, 50), target(67, 50), target(66, 51)]
    landing = scatter_landing(supervisor, targets, (50, 50), (20, 20, 80, 80), 10)
    distances = [
        max(abs(t.world_position[0] - landing[0]), abs(t.world_position[1] - landing[1]))
        for t in targets
    ]
    # Every target still in reach, but none within five tiles of the landing
    # (centrality alone picked a landing four tiles from the pack).
    assert max(distances) <= 10 and min(distances) >= 6
    assert supervisor.scatter_plan["nearby_targets_5"] == 0


def test_landing_keeps_room_from_a_roaming_boss_before_counting_targets():
    # The WingedSnakeKing roams the middle of the field: a landing just past
    # BOSS_CLEARANCE was another boss escape moments later (Toxic 2026-09-28).
    from conquest.routes import BOSS_ROOM

    terrain = TerrainMap(1011, 100, 100, np.zeros((100, 100), dtype=bool), "", (), ())
    king = SimpleNamespace(name="WingedSnakeKing", position=(43, 30))
    supervisor = SimpleNamespace(recovery=SimpleNamespace(terrain=terrain))
    targets = [target(62, 50), target(63, 51), target(62, 49), target(38, 50)]
    # Unaware of the King, the best landing sits 12 tiles from it.
    plain = scatter_landing(supervisor, targets, (50, 50), (20, 20, 80, 80), 10)
    assert max(abs(plain[0] - 43), abs(plain[1] - 30)) < BOSS_ROOM
    supervisor.escape_monsters = (king,)
    landing = scatter_landing(supervisor, targets, (50, 50), (20, 20, 80, 80), 10)
    assert landing is not None
    assert max(abs(landing[0] - 43), abs(landing[1] - 30)) >= BOSS_ROOM
    assert supervisor.scatter_plan["boss_room"] is True
    assert (
        sum(
            max(abs(t.world_position[0] - landing[0]), abs(t.world_position[1] - landing[1]))
            <= 10
            for t in targets
        )
        >= 3
    )


def test_finish_wounded_group_requires_fresh_same_identities_in_range(monkeypatch):
    from conquest import scatter_movement as sm

    now = [100.0]
    monkeypatch.setattr(sm.time, "monotonic", lambda: now[0])
    supervisor = SimpleNamespace()
    targets = [
        SimpleNamespace(
            entity_id=i,
            object_address=1000 + i,
            world_position=(52 + i, 50),
            current_hp=800,
        )
        for i in (1, 2)
    ]
    sm.remember_scatter(supervisor, targets)
    assert not sm.wounded_group_in_range(supervisor, targets, (50, 50), 10)
    for t in targets:
        t.current_hp = 180
    assert sm.wounded_group_in_range(supervisor, targets, (50, 50), 10)
    targets[0].object_address = 9999
    assert not sm.wounded_group_in_range(supervisor, targets, (50, 50), 10)
    targets[0].object_address = 1001
    assert not sm.wounded_group_in_range(supervisor, targets, (30, 30), 10)
    now[0] = 104
    assert not sm.wounded_group_in_range(supervisor, targets, (50, 50), 10)


@pytest.mark.parametrize("seed", range(30))
def test_fast_planning_preserves_exact_landing_with_obstacles_and_ties(
    seed, monkeypatch
):
    from conquest import scatter_movement as sm
    from conquest.farmer_profile import CombatSpeed

    monkeypatch.setattr(sm.time, "monotonic", lambda: 100.0)
    rng = np.random.default_rng(seed)
    blocked = np.zeros((100, 100), dtype=bool)
    for y, x in rng.integers(35, 66, size=(seed % 8, 2)):
        blocked[y, x] = True
    terrain = TerrainMap(1011, 100, 100, blocked, "", (), ())
    targets = [
        target(int(x), int(y)) for x, y in rng.integers(30, 71, size=(1 + seed, 2))
    ]
    common = dict(
        recovery=SimpleNamespace(terrain=terrain),
        scatter_landings=[((58, 50), 95.0)],
        movement_obstructions={(1011, (42, 50)): 120.0},
        escape_monsters=[SimpleNamespace(name="BanditKing", position=(60, 50))],
    )
    old = SimpleNamespace(**common, combat_speed=CombatSpeed())
    fast = SimpleNamespace(
        **common, combat_speed=CombatSpeed(fast_scatter_planning=True)
    )
    expected = scatter_landing(old, targets, (50, 50), (20, 20, 80, 80), 12)
    assert scatter_landing(fast, targets, (50, 50), (20, 20, 80, 80), 12) == expected
    assert fast.scatter_landings == old.scatter_landings


def test_fast_planning_avoids_checking_lower_ranked_terrain(monkeypatch):
    from conquest import scatter_movement as sm
    from conquest.farmer_profile import CombatSpeed

    original = sm.clear_jump
    calls = []

    def counted(*args):
        calls.append(args[-1])
        return original(*args)

    monkeypatch.setattr(sm, "clear_jump", counted)
    terrain = TerrainMap(1011, 100, 100, np.zeros((100, 100), dtype=bool), "", (), ())
    # Spaced 4 tiles apart: since e7e665a landings inside a dense contact
    # group are excluded before terrain checks, which would thin the ranking.
    targets = [target(60 + 4 * (i % 5), 50 + 4 * (i // 5)) for i in range(25)]
    old = SimpleNamespace(recovery=SimpleNamespace(terrain=terrain))
    expected = scatter_landing(old, targets, (50, 50), (20, 20, 80, 80), 12)
    original_count = len(calls)
    calls.clear()
    fast = SimpleNamespace(
        recovery=SimpleNamespace(terrain=terrain),
        combat_speed=CombatSpeed(fast_scatter_planning=True),
    )
    assert scatter_landing(fast, targets, (50, 50), (20, 20, 80, 80), 12) == expected
    # Landings within a monster's reach are excluded before terrain checks too.
    assert len(calls) == 1 and original_count > 40


@pytest.mark.parametrize("enabled", [False, True])
def test_region_edge_can_approach_live_group_only_inside_overall_hunt(enabled):
    from conquest.farmer_profile import CombatSpeed

    terrain = TerrainMap(1011, 100, 100, np.zeros((100, 100), dtype=bool), "", (), ())
    group = [target(43, 50), target(44, 51), target(45, 50)]
    supervisor = SimpleNamespace(
        recovery=SimpleNamespace(terrain=terrain),
        combat_speed=CombatSpeed(cross_region_scatter=enabled),
        scatter_scene_targets=group,
    )
    region = (50, 20, 80, 80)
    overall = (20, 20, 80, 80)
    result = scatter_landing(
        supervisor, [], (60, 50), region, 10, minimum_count=3, hunting_boundary=overall
    )
    if not enabled:
        assert result is None
    else:
        assert result is not None and clear_jump(terrain, (60, 50), result)
        assert 8 <= max(abs(a - b) for a, b in zip((60, 50), result)) <= 12
        assert all(
            max(abs(a - b) for a, b in zip(result, t.world_position)) <= 10
            for t in group
        )
    supervisor.scatter_scene_targets = [target(15, 50), target(16, 50), target(17, 50)]
    assert (
        scatter_landing(supervisor, [], (28, 50), region, 10, hunting_boundary=overall)
        is None
    )
    supervisor.scatter_scene_targets = [target(43, 50, 0), target(44, 50, 0)]
    assert (
        scatter_landing(supervisor, [], (60, 50), region, 10, hunting_boundary=overall)
        is None
    )


@pytest.mark.parametrize("enabled", [False, True])
def test_cluster_lookahead_prefers_dense_group_over_isolated_target(enabled):
    from conquest.farmer_profile import CombatSpeed

    terrain = TerrainMap(1011, 150, 150, np.zeros((150, 150), dtype=bool), "", (), ())
    nearby = [target(40, 50)]
    dense = [target(82 + i % 3, 49 + i // 3) for i in range(8)]
    supervisor = SimpleNamespace(
        recovery=SimpleNamespace(terrain=terrain),
        combat_speed=CombatSpeed(cluster_lookahead=enabled, fast_scatter_planning=True),
        scatter_scene_targets=nearby + dense,
    )
    landing = scatter_landing(supervisor, nearby, (50, 50), (20, 20, 130, 130), 10)
    assert landing is not None and clear_jump(terrain, (50, 50), landing)
    if enabled:
        assert landing[0] >= 58
    else:
        assert max(abs(a - b) for a, b in zip(landing, nearby[0].world_position)) <= 10


def test_cluster_lookahead_preserves_nearby_groups_and_hard_bounds():
    from conquest.farmer_profile import CombatSpeed

    terrain = TerrainMap(1011, 150, 150, np.zeros((150, 150), dtype=bool), "", (), ())
    nearby = [target(49, 49), target(50, 50), target(51, 51)]
    dense = [target(82 + i % 3, 49 + i // 3) for i in range(8)]
    supervisor = SimpleNamespace(
        recovery=SimpleNamespace(terrain=terrain),
        combat_speed=CombatSpeed(cluster_lookahead=True, fast_scatter_planning=True),
        scatter_scene_targets=nearby + dense,
    )
    landing = scatter_landing(supervisor, nearby, (50, 50), (20, 20, 130, 130), 10)
    assert (
        sum(
            max(abs(a - b) for a, b in zip(landing, t.world_position)) <= 10
            for t in nearby
        )
        == 3
    )
    supervisor.scatter_scene_targets = [target(82, 50, 0), *dense]
    assert scatter_landing(supervisor, [], (50, 50), (20, 20, 70, 70), 10) is None
    supervisor.scatter_scene_targets = [
        target(110 + i % 3, 49 + i // 3) for i in range(8)
    ]
    assert scatter_landing(supervisor, [], (50, 50), (20, 20, 130, 130), 10) is None


def test_cluster_lookahead_does_not_cross_wall_to_chase_dense_group():
    from conquest.farmer_profile import CombatSpeed

    terrain = TerrainMap(1011, 150, 150, np.zeros((150, 150), dtype=bool), "", (), ())
    terrain.blocked[:, 55] = True
    nearby = [target(40, 50)]
    dense = [target(82 + i % 3, 49 + i // 3) for i in range(8)]
    supervisor = SimpleNamespace(
        recovery=SimpleNamespace(terrain=terrain),
        combat_speed=CombatSpeed(cluster_lookahead=True, fast_scatter_planning=True),
        scatter_scene_targets=nearby + dense,
    )
    landing = scatter_landing(supervisor, nearby, (50, 50), (20, 20, 130, 130), 10)
    assert (
        landing is not None
        and landing[0] < 55
        and clear_jump(terrain, (50, 50), landing)
    )


def test_lookahead_does_not_lure_patrol_to_wall_beyond_first_jump():
    from conquest.farmer_profile import CombatSpeed

    terrain = TerrainMap(1011, 150, 150, np.zeros((150, 150), dtype=bool), "", (), ())
    # The first 12-tile jump is clear, but the following approach is blocked.
    terrain.blocked[:, 65] = True
    nearby = [target(40, 50)]
    dense = [target(82 + i % 3, 49 + i // 3) for i in range(8)]
    supervisor = SimpleNamespace(
        recovery=SimpleNamespace(terrain=terrain),
        combat_speed=CombatSpeed(cluster_lookahead=True, fast_scatter_planning=True),
        scatter_scene_targets=nearby + dense,
    )
    landing = scatter_landing(supervisor, nearby, (50, 50), (20, 20, 130, 130), 10)
    assert landing is not None and clear_jump(terrain, (50, 50), landing)
    assert max(abs(a - b) for a, b in zip(landing, nearby[0].world_position)) <= 10
    assert not supervisor.scatter_plan["lookahead"]
