import pytest
from conquest.foreground import require_click_position


def test_expected_pointer_and_geometry_allow_click():
    require_click_position(dict(foreground=7, minimized=False, client_size=[100, 100], cursor=[50, 60]), 7, (100, 100), (50, 60))


@pytest.mark.parametrize("change", [dict(foreground=9), dict(minimized=True), dict(client_size=[120, 100]), dict(cursor=[80, 60])])
def test_cursor_interference_and_window_changes_reject_click(change):
    state = dict(foreground=7, minimized=False, client_size=[100, 100], cursor=[50, 60])
    state.update(change)
    with pytest.raises(ValueError, match="no button pressed"):
        require_click_position(state, 7, (100, 100), (50, 60))
