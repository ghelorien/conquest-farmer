from types import SimpleNamespace

import pytest

from conquest.merchants.background_probe import isolated_ctrl_probe


def fixture(*, fail_neutralize=False, change_identity=False, fail_down=False):
    calls = []
    identity = {"pid": 12, "creation_time_100ns": 34}
    adapter = SimpleNamespace(identity=dict(identity), assert_identity=lambda: None)
    state = {"modifiers": {"ctrl": False}, "queue": {"size": 0}}

    def post(message, wp, lp):
        calls.append(("post", message))
        state["modifiers"]["ctrl"] = message == 0x100
        if message == 0x100 and fail_down:
            raise OSError("uncertain queued down")

    target = SimpleNamespace(hwnd=56, post=post)

    class Scope:
        process = SimpleNamespace(poll=lambda: 0)

        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            calls.append("enter")
            return self

        def __exit__(self, *args):
            calls.append("exit")
            self.close()

        def check(self):
            pass

        def neutralize(self):
            calls.append("neutralize")
            if change_identity:
                adapter.identity["creation_time_100ns"] += 1
            if fail_neutralize:
                raise TimeoutError("neutralize acknowledgment missing")

        def close(self):
            calls.append("close")

    samples, report = [], {}

    def sample(stage):
        samples.append({"gui": {"modifiers": dict(state["modifiers"])}})

    def run():
        isolated_ctrl_probe(
            target,
            adapter,
            identity,
            SimpleNamespace(snapshot=lambda: state),
            lambda: None,
            sample,
            samples,
            report,
            scope_factory=Scope,
        )

    return calls, report, run


def test_normal_release_stays_before_detach_and_does_not_duplicate_keyup():
    calls, report, run = fixture()
    run()
    assert calls == [
        "enter",
        ("post", 0x100),
        "neutralize",
        ("post", 0x101),
        "exit",
        "close",
    ]
    assert (
        report["ctrl_verified"]
        and report["ctrl_release_verified"]
        and report["helper_detached"]
    )


def test_neutralize_failure_still_releases_after_helper_close():
    calls, report, run = fixture(fail_neutralize=True)
    with pytest.raises(TimeoutError, match="neutralize"):
        run()
    assert calls.index(("post", 0x101)) > calls.index("close")
    assert report["ctrl_fallback_keyup_sent"] and report["ctrl_release_verified"]


def test_identity_change_prevents_cleanup_input_to_reused_process():
    calls, report, run = fixture(fail_neutralize=True, change_identity=True)
    with pytest.raises(TimeoutError):
        run()
    assert ("post", 0x101) not in calls
    assert report["ctrl_fallback_cleanup"]["verified"] is False


def test_partial_keydown_failure_also_releases():
    calls, report, run = fixture(fail_down=True)
    with pytest.raises(OSError, match="uncertain queued down"):
        run()
    assert ("post", 0x101) in calls
    assert report["ctrl_release_verified"]
