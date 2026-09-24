import pytest

from conquest.background_keyboard_lab import exercise


class Api:
    def __init__(self):
        self.table = bytes(256)
        self.attached = False
        self.calls = []

    def thread_id(self):
        return 20

    def attach(self, first, second, attached):
        assert (first, second) == (20, 30)
        self.calls.append(("attach", attached))
        self.attached = attached

    def keyboard(self):
        return self.table

    def set_keyboard(self, table):
        assert self.attached
        self.table = bytes(table)
        self.calls.append(("state", self.table[0x11]))


def test_releases_and_detaches_when_target_acknowledgment_fails():
    api = Api()

    def sample(stage):
        if stage == "held":
            raise TimeoutError("target stalled")
        return {}

    with pytest.raises(TimeoutError):
        exercise(api, 30, sample, lambda: None)
    assert not api.attached
    assert api.table == bytes(256)
    assert api.calls[-2:] == [("state", 0), ("attach", False)]


def test_detaches_even_if_state_restoration_fails():
    api = Api()
    original = api.set_keyboard

    def setter(table):
        if api.calls and api.calls[-1] == ("state", 128):
            raise OSError("restore failed")
        original(table)

    api.set_keyboard = setter
    with pytest.raises(OSError):
        exercise(api, 30, lambda stage: {}, lambda: None)
    assert not api.attached


def test_report_does_not_assume_target_observes_shared_state():
    api = Api()
    result = exercise(api, 30, lambda stage: {"control": False}, lambda: None)
    assert result["held"] == {"control": False}
    assert not api.attached
    assert api.table == bytes(256)


def test_foreground_change_before_attach_prevents_mutation():
    api = Api()

    def changed():
        raise RuntimeError("foreground changed")

    with pytest.raises(RuntimeError):
        exercise(api, 30, lambda stage: {}, changed)
    assert api.calls == []
