import pytest

from conquest.input_probe import click_probe, key_probe


class FakeTarget:
    hwnd = 100

    def __init__(self):
        self.messages = []
        self.state = {"pid": 1, "hwnd": 100, "client_size": [800, 600],
                      "foreground": 200, "cursor": [900, 300], "minimized": False}

    def snapshot(self):
        return dict(self.state)

    def post(self, message, wparam, lparam):
        self.messages.append((message, wparam, lparam))


def test_queue_success_never_qualifies_gameplay():
    target = FakeTarget()
    report = click_probe(target, 10, 20, [800, 600], sleep=lambda _: None)
    assert report["messages_queued"]
    assert report["sampled_desktop_state_unchanged"]
    assert report["outcome"] == "awaiting_game_observation"
    assert not report["qualified"]


@pytest.mark.parametrize("key,value", [("foreground", 100), ("minimized", True), ("client_size", [1000, 600])])
def test_invalid_window_state_sends_no_input(key, value):
    target = FakeTarget()
    target.state[key] = value
    with pytest.raises(ValueError):
        click_probe(target, 10, 20, [800, 600])
    assert not target.messages


def test_expired_action_sends_no_input():
    target = FakeTarget()
    ticks = iter([0, 1])
    with pytest.raises(ValueError, match="expired"):
        click_probe(target, 10, 20, [800, 600], clock=lambda: next(ticks))
    assert not target.messages


def test_interruption_releases_button():
    target = FakeTarget()

    def interrupted(_):
        raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        click_probe(target, 10, 20, [800, 600], sleep=interrupted)
    assert target.messages[-1][0] == 0x202


def test_desktop_changes_are_not_reported_as_isolated():
    target = FakeTarget()

    def changed(_):
        target.state["foreground"] = 300

    report = click_probe(target, 10, 20, [800, 600], sleep=changed)
    assert not report["sampled_desktop_state_unchanged"]
    assert report["outcome"] == "desktop_or_window_state_changed"


def test_keyboard_messages_have_scan_transition_bits():
    target = FakeTarget()
    result = key_probe(target, 0x7A, 0x57, [800, 600], sleep=lambda _: None)
    assert target.messages == [(0x100, 0x7A, 1 | (0x57 << 16)),
                               (0x101, 0x7A, 1 | (0x57 << 16) | (3 << 30))]
    assert result["sampled_desktop_state_unchanged"] and not result["qualified"]


@pytest.mark.parametrize("key,value", [("foreground", 100), ("minimized", True), ("client_size", [1000, 600])])
def test_keyboard_rejects_incompatible_window(key, value):
    target = FakeTarget()
    target.state[key] = value
    with pytest.raises(ValueError):
        key_probe(target, 0x7A, 0x57, [800, 600])
    assert not target.messages


def test_keyboard_release_on_interruption():
    target = FakeTarget()
    def interrupt(_):
        raise KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        key_probe(target, 0x7A, 0x57, [800, 600], sleep=interrupt)
    assert target.messages[-1][0] == 0x101


def test_keyboard_rejects_emergency_stop_key():
    target = FakeTarget()
    with pytest.raises(ValueError):
        key_probe(target, 0x7B, 0x58, [800, 600])
    assert not target.messages


def test_background_control_chord_uses_only_window_messages():
    target = FakeTarget()
    result = key_probe(target, 0x7A, 0x57, [800, 600], control_scan=0x1D, sleep=lambda _: None)
    assert [(message, vk) for message, vk, _ in target.messages] == [
        (0x100, 0x11), (0x100, 0x7A), (0x101, 0x7A), (0x101, 0x11)]
    assert result["control"] and result["sampled_desktop_state_unchanged"]
