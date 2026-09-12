import pytest

from conquest.background_mouse_lab import INVALID_POSITION, _pair_count, model_frame


def position(point):
    return {'type': 'mouse_position', 'position': list(point)}


def test_character_barrier_preserves_one_modeled_hover_frame_then_drains():
    queue = [position((60, 80)), {'type': 'text', 'value': 1}, position(INVALID_POSITION)]
    first = model_frame(queue)
    assert first['position'] == [60, 80]
    assert first['processed'] == 1
    assert first['inserted_characters'] == []
    second = model_frame(first['remaining'], tuple(first['position']))
    assert second['position'] == list(INVALID_POSITION)
    assert second['remaining'] == []
    assert second['inserted_characters'] == []


def test_leave_before_barrier_cannot_be_misreported_as_hover_success():
    queue = [position((60, 80)), position(INVALID_POSITION), {'type': 'text', 'value': 1}]
    assert model_frame(queue)['position'] == list(INVALID_POSITION)


def test_character_filter_model_rejects_control_one():
    result = model_frame([{'type': 'text', 'value': 1}, {'type': 'text', 'value': ord('A')}])
    assert result['inserted_characters'] == [ord('A')]


def test_alternating_adjacent_points_preserve_each_modeled_frame_without_text():
    queue = []
    for index in range(12):
        queue.extend([position((60 + index % 2, 80)), {'type': 'text', 'value': 1}])
    queue.append(position(INVALID_POSITION))
    current = INVALID_POSITION
    frames = []
    while queue:
        result = model_frame(queue, current)
        assert result['processed'] > 0
        assert result['inserted_characters'] == []
        frames.append(result['position'])
        current = tuple(result['position'])
        queue = result['remaining']
    assert frames[:-1] == [[60 + index % 2, 80] for index in range(12)]
    assert frames[-1] == list(INVALID_POSITION)


@pytest.mark.parametrize('pairs', [0, 13, True, 1.5, '12'])
def test_pair_limit_rejects_invalid_input(pairs):
    with pytest.raises(ValueError):
        _pair_count(pairs)


def test_model_does_not_accept_button_actions():
    with pytest.raises(ValueError):
        model_frame([{'type': 'mouse_button', 'down': True}])
