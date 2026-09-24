import numpy as np
import pytest

from conquest.navigation import TerrainMap, clear_segment
from conquest.safe_reload import nearby_escape
from conquest.viewport import clear_scene


def terrain():
    return TerrainMap(1011, 100, 100, np.zeros((100, 100), dtype=bool), "", (), ())


def test_escape_does_not_cross_previous_failed_landing():
    ground = terrain()
    source = (50, 50)
    avoid = {(54, 50)}
    step = nearby_escape(
        ground, source, [{"position": [49, 49], "alive": True}], avoid=avoid
    )
    # Previously returned (62,50), crossing the same failed landing again.
    assert step is not None and clear_segment(ground, source, step, avoid=avoid)


def test_escape_uses_visible_checked_diagonal_on_open_terrain():
    ground = terrain()
    source = (50, 50)
    anchor = (518, 396)
    step = nearby_escape(
        ground, source, [{"position": [49, 49], "alive": None}], anchor=anchor
    )
    assert step[0] != source[0] and step[1] != source[1]
    assert clear_segment(ground, source, step)
    dx, dy = step[0] - source[0], step[1] - source[1]
    assert max(abs(dx), abs(dy)) <= 12
    assert clear_scene((anchor[0] + (dx - dy) * 32, anchor[1] + (dx + dy) * 16))


@pytest.mark.parametrize("anchor", [(518, 396), (925, 396), (150, 250)])
def test_escape_preserves_solid_corner_and_actual_camera_guards(anchor):
    ground = terrain()
    source = (50, 50)
    ground.blocked[50, 51] = True
    ground.blocked[51, 50] = True
    step = nearby_escape(
        ground, source, [{"position": [49, 49], "alive": True}], anchor=anchor
    )
    assert step is not None and clear_segment(ground, source, step)
    dx, dy = step[0] - source[0], step[1] - source[1]
    assert clear_scene((anchor[0] + (dx - dy) * 32, anchor[1] + (dx + dy) * 16))


def test_enclosing_failed_landings_do_not_allow_jumping_across_them():
    source = (50, 50)
    avoid = {
        (source[0] + dx, source[1] + dy)
        for dx in (-1, 0, 1)
        for dy in (-1, 0, 1)
        if dx or dy
    }
    assert (
        nearby_escape(
            terrain(), source, [{"position": [49, 49], "alive": True}], avoid=avoid
        )
        is None
    )


def test_one_unprojectable_candidate_does_not_discard_other_checked_escapes(
    monkeypatch,
):
    from conquest import navigation

    original = navigation.travel_waypoint
    calls = []

    def waypoint(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise ValueError("No visible route landing point")
        return original(*args, **kwargs)

    monkeypatch.setattr(navigation, "travel_waypoint", waypoint)
    ground = terrain()
    step = nearby_escape(ground, (50, 50), [{"position": [49, 49], "alive": True}])
    assert len(calls) > 1 and step is not None and clear_segment(ground, (50, 50), step)
