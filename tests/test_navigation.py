import json
import struct

import numpy as np
import pytest

from conquest.navigation import TerrainMap, read_terrain, straight_waypoints


def test_shortest_route_goes_through_gap_without_crossing_wall():
    blocked = np.zeros((7, 7), dtype=bool)
    blocked[0:6, 3] = True
    terrain = TerrainMap(1002, 7, 7, blocked, "", (), ())
    path = terrain.path((1, 1), (5, 1))
    assert len(path) - 1 == 14
    assert all(terrain.walkable(p) for p in path)
    assert (3, 6) in path
    assert (3, 6) not in terrain.path((1, 1), (2, 1))


def test_direct_travel_uses_diagonal_instead_of_two_sides_of_rectangle():
    from conquest.navigation import travel_waypoint, clear_segment

    terrain = TerrainMap(1011, 80, 80, np.zeros((80, 80), dtype=bool), "", (), ())
    path = terrain.travel_path((10, 10), (50, 30))
    assert len(path) == 41
    target = travel_waypoint(terrain, path)
    assert target == (20, 15)  # Longer landing would be below the clear scene.
    assert clear_segment(terrain, (10, 10), target)


def test_diagonal_travel_does_not_cut_blocked_corners_or_guarded_tiles():
    from conquest.navigation import clear_segment, travel_waypoint

    blocked = np.zeros((30, 30), dtype=bool)
    blocked[:20, 15] = True
    terrain = TerrainMap(1011, 30, 30, blocked, "", (), ())
    assert not clear_segment(terrain, (14, 19), (15, 20))
    path = terrain.travel_path((10, 10), (20, 10), avoid={(14, 20)})
    assert any(y >= 20 for x, y in path)
    assert all(
        clear_segment(terrain, a, b, avoid={(14, 20)}) for a, b in zip(path, path[1:])
    )
    target = travel_waypoint(terrain, path, avoid={(14, 20)})
    assert clear_segment(terrain, path[0], target, avoid={(14, 20)})


def test_blocked_goal_unreachable_and_search_budget_fail_explicitly():
    terrain = TerrainMap(1002, 5, 5, np.zeros((5, 5), dtype=bool), "", (), ())
    with pytest.raises(ValueError, match="budget"):
        terrain.path((0, 0), (4, 4), limit=1)
    terrain.blocked[:, 2] = True
    with pytest.raises(ValueError, match="No traversable"):
        terrain.path((0, 0), (4, 4))
    with pytest.raises(ValueError, match="endpoint"):
        terrain.path((0, 0), (2, 2))


def test_compression_preserves_corners_and_step_limit():
    path = [(0, 0), (1, 0), (2, 0), (3, 0), (3, 1), (3, 2)]
    assert straight_waypoints(path, 2) == [(0, 0), (2, 0), (3, 0), (3, 2)]
    with pytest.raises(ValueError, match="non-adjacent"):
        straight_waypoints([(0, 0), (2, 0)])


def test_long_jumps_preserve_short_corner_segments_and_visible_landing_points():
    from conquest.trial import visible_movement_delta

    path = [(x, 0) for x in range(12)] + [(11, y) for y in range(1, 10)]
    points = straight_waypoints(path, 8)
    assert points == [(0, 0), (8, 0), (11, 0), (11, 8), (11, 9)]
    for dx, dy in ((8, 0), (-8, 0), (0, 8), (0, -8), (8, 8), (-8, 8)):
        x, y = visible_movement_delta(dx, dy, horizontal_limit=380, vertical_limit=144)
        px, py = 518 + (x - y) * 32, 396 + (x + y) * 16
        assert 100 < px < 1036 and 140 < py < 550


def test_twelve_tile_jumps_shorten_only_when_chat_would_intercept():
    from conquest.navigation import native_movement_delta

    for delta in ((12, 0), (-12, 0), (0, -12)):
        assert native_movement_delta(*delta) == delta
    assert native_movement_delta(0, 12) == (0, 9)
    assert native_movement_delta(3, 0) == (3, 0)


def test_installed_map_format_blocks_portals_and_solid_cells(tmp_path):
    (tmp_path / "ini").mkdir()
    (tmp_path / "ini/GameMap.json").write_text(
        json.dumps([{"DocumentId": 1002, "FileName": "test.DMap"}])
    )
    header = struct.pack("<II", 1003, 0) + bytes(260) + struct.pack("<II", 5, 5)
    cells = b"".join(
        b"".join(struct.pack("<HHh", int(x == 0), 3, 0) for x in range(5)) + bytes(4)
        for y in range(5)
    )
    tail = (
        struct.pack("<I", 1) + struct.pack("<III", 4, 4, 7) + struct.pack("<II", 0, 0)
    )
    (tmp_path / "test.DMap").write_bytes(header + cells + tail)
    terrain = read_terrain(tmp_path, 1002)
    assert terrain.walkable((2, 2)) and not terrain.walkable((0, 2))
    assert not terrain.walkable((4, 4)) and not terrain.walkable((3, 3))
    assert terrain.portals == ((4, 4, 7),)
    (tmp_path / "test.DMap").write_bytes(header + cells[:20])
    with pytest.raises(ValueError, match="Truncated"):
        read_terrain(tmp_path, 1002)


def test_return_boundary_includes_terrain_detours_and_departure():
    from conquest.navigation import path_boundary

    path = [(452, 335), (714, 496), (644, 570)]
    boundary = path_boundary(path, (1024, 1024))
    assert boundary == (440, 323, 726, 582)
    assert all(
        boundary[0] <= x <= boundary[2] and boundary[1] <= y <= boundary[3]
        for x, y in path
    )
    assert path_boundary([(0, 0), (9, 9)], (10, 10)) == (0, 0, 9, 9)


def test_boundary_excursion_plans_traversable_return_to_saved_spot():
    from conquest.navigation import TerrainMap, plan_hunting_return
    import numpy as np

    blocked = np.zeros((30, 30), dtype=bool)
    blocked[:15, 15] = True
    terrain = TerrainMap(1002, 30, 30, blocked, "test", (), ())
    waypoints, boundary = plan_hunting_return(
        terrain, (20, 10), (10, 10), (5, 5, 12, 12)
    )
    assert waypoints[-1] == (10, 10)
    assert any(y >= 15 for x, y in waypoints)  # Required detour around the wall.
    assert all(terrain.walkable(p) for p in waypoints)
    assert all(
        boundary[0] <= x <= boundary[2] and boundary[1] <= y <= boundary[3]
        for x, y in waypoints
    )
    assert (
        max(
            max(abs(b[0] - a[0]), abs(b[1] - a[1]))
            for a, b in zip(((20, 10), *waypoints), waypoints)
        )
        <= 12
    )


def test_boundary_return_rejects_unreachable_or_wrong_hunting_spot():
    from conquest.navigation import TerrainMap, plan_hunting_return
    import numpy as np, pytest

    blocked = np.zeros((30, 30), dtype=bool)
    blocked[:, 15] = True
    terrain = TerrainMap(1002, 30, 30, blocked, "test", (), ())
    with pytest.raises(ValueError, match="No traversable route"):
        plan_hunting_return(terrain, (20, 10), (10, 10), (5, 5, 12, 12))
    with pytest.raises(ValueError, match="outside the hunting boundary"):
        plan_hunting_return(terrain, (20, 10), (20, 10), (5, 5, 12, 12))


@pytest.mark.parametrize("portal", [False, True])
def test_scene_collision_connects_bridge_and_keeps_portals_blocked(tmp_path, portal):
    (tmp_path / "ini").mkdir()
    (tmp_path / "ini/GameMap.json").write_text(
        json.dumps([{"DocumentId": 1002, "FileName": "test.DMap"}])
    )
    header = struct.pack("<II", 1003, 0) + bytes(260) + struct.pack("<II", 10, 10)
    cells = b"".join(
        b"".join(struct.pack("<HHh", int(x == 5), 0, 0) for x in range(10)) + bytes(4)
        for y in range(10)
    )
    part = bytearray(356)
    struct.pack_into("<II", part, 332, 3, 2)
    struct.pack_into("<ii", part, 344, -1, -2)
    # Anchor (7,7) plus (-1,-2) minus (width-1,height-1) => (4,4).
    scene = (
        struct.pack("<I", 1)
        + part
        + b"".join(struct.pack("<III", a, 0, 0) for a in [1, 1, 1, 0, 0, 0])
    )
    (tmp_path / "bridge.scene").write_bytes(scene)
    tail = struct.pack("<I", int(portal)) + (
        struct.pack("<III", 5, 5, 7) if portal else b""
    )
    tail += (
        struct.pack("<II", 1, 1)
        + b"bridge.scene".ljust(260, b"\0")
        + struct.pack("<II", 7, 7)
    )
    (tmp_path / "test.DMap").write_bytes(header + cells + tail)
    terrain = read_terrain(tmp_path, 1002)
    assert not terrain.walkable((5, 4))
    assert bool(terrain.walkable((5, 5))) is not portal
    if not portal:
        assert (5, 5) in terrain.path((3, 5), (7, 5))
    (tmp_path / "bridge.scene").write_bytes(scene[:-1])
    with pytest.raises(ValueError, match="Truncated terrain scene cells"):
        read_terrain(tmp_path, 1002)
    struct.pack_into("<ii", part, 344, -20, 0)
    (tmp_path / "bridge.scene").write_bytes(struct.pack("<I", 1) + part + scene[360:])
    with pytest.raises(ValueError, match="leave the map"):
        read_terrain(tmp_path, 1002)


def test_short_corner_uses_running_destination_clear_of_player_sprite():
    from conquest.navigation import native_waypoint

    path = [(10, 10), (11, 10), (11, 11), (12, 11), (13, 11), (13, 12)]
    assert native_waypoint(path) == (13, 11)
    assert native_waypoint(path[:2]) == (11, 10)
