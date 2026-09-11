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


@pytest.mark.parametrize('size,minimized',[([120,100],False),([100,100],True)])
def test_geometry_changes_pause_before_input(monkeypatch,size,minimized):
    from types import SimpleNamespace
    from conquest import foreground
    from conquest.capture import CaptureUnavailable
    monkeypatch.setattr(foreground,'require_idle',lambda:None)
    target=SimpleNamespace(hwnd=7,snapshot=lambda:{'foreground':7,'client_size':size,'minimized':minimized})
    with pytest.raises(CaptureUnavailable,match='no input sent'):
        foreground.foreground_click(target,50,50,(100,100),require_foreground=True)
    with pytest.raises(CaptureUnavailable,match='no input sent'):
        foreground.foreground_key(target,112,(100,100),require_foreground=True)
