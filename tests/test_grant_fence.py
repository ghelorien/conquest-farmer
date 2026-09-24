import threading

import pytest

from conquest.capture import CaptureUnavailable
from conquest.merchants.coordination import InputCoordinator, input_scope, install
from conquest.merchants.grant_fence import GrantFence


@pytest.fixture
def fence():
    return GrantFence(clock=lambda: 100)


def grant(fence, request_id="one"):
    return fence.activate(request_id, 7, 115)


def test_revoked_worker_cannot_fall_back_to_idle_or_new_grant(fence):
    token = grant(fence)
    with fence.bind_worker(token):
        assert not fence.revoke(token)["released"]
        with pytest.raises(CaptureUnavailable):
            fence.check()
        assert fence.quiescence(token)["released"]
        next_token = grant(fence, "two")
        assert fence.capture() == token
        with pytest.raises(CaptureUnavailable):
            fence.check(next_token)
    fence.check(next_token)


def test_delayed_worker_birth_and_callback_never_mutate_after_release(fence):
    token = grant(fence)
    mutations = []
    callback = fence.guard_callback(token, lambda: mutations.append("focused"))
    assert fence.revoke(token)["released"]
    grant(fence, "two")
    with pytest.raises(CaptureUnavailable):
        with fence.bind_worker(token):
            mutations.append("pressed")
    with pytest.raises(CaptureUnavailable):
        callback()
    assert mutations == []


def test_idle_bindings_and_callbacks_cannot_cross_grant_transition(fence):
    token = fence.capture()
    assert token.scope == "idle"
    mutations = []
    callback = fence.guard_callback(token, lambda: mutations.append("restore"))
    active = grant(fence)
    fence.revoke(active)
    with pytest.raises(CaptureUnavailable):
        callback()
    with pytest.raises(CaptureUnavailable):
        with fence.bind_worker(token):
            pass
    assert fence.capture() != token
    fence.check()
    assert mutations == []


def test_release_waits_for_held_input_even_after_worker_downgrade(fence):
    token = grant(fence)
    with fence.bind_worker(token):
        with fence.input_action():
            fence.mark_read_only()
            receipt = fence.revoke(token)
            assert receipt["active_actions"] == 1
            assert receipt["waiting_workers"] == 0
            assert not receipt["released"]
            with pytest.raises(CaptureUnavailable):
                fence.check()
        assert fence.quiescence(token)["released"]


def test_readonly_recovery_can_outlive_grant_without_holding_input(fence):
    token = grant(fence)
    fence.revoke(token)
    with fence.bind_worker(token, action_capable=False):
        assert fence.quiescence(token)["released"]
        with pytest.raises(CaptureUnavailable):
            with fence.input_action():
                pytest.fail("read-only worker sent input")


def test_release_is_idempotent_and_does_not_revoke_new_grant(fence):
    old = grant(fence)
    receipt = fence.revoke(old)
    new = grant(fence, "two")
    assert fence.revoke("one") == receipt
    assert fence.quiescence(old) == receipt
    fence.check(new)
    with pytest.raises(ValueError):
        fence.revoke("not-a-grant")
    fence.check(new)


def test_expiry_and_session_identity_reject_new_input(fence):
    token = grant(fence)
    with fence.bind_worker(token):
        fence.clock = lambda: 115
        with pytest.raises(CaptureUnavailable):
            fence.check()
    with pytest.raises(CaptureUnavailable):
        GrantFence(clock=lambda: 100).check(token)


def test_grant_cannot_be_extended_or_reused(fence):
    token = grant(fence)
    assert grant(fence) is token
    with pytest.raises(ValueError):
        fence.activate("one", 7, 160)
    fence.revoke(token)
    with pytest.raises(ValueError):
        grant(fence)


def test_revoke_does_not_wait_for_worker_or_callback_lock(fence, tmp_path):
    token = grant(fence)
    entered, release_worker = threading.Event(), threading.Event()
    failures = []
    guard = InputCoordinator(lambda: True, path=tmp_path / "input.lock")
    guard.fence = fence

    def worker():
        try:
            with fence.bind_worker(token):
                with guard.lease("Dutch"):
                    entered.set()
                    assert release_worker.wait(2)
                    with pytest.raises(CaptureUnavailable):
                        guard.check()
        except BaseException as error:
            failures.append(error)

    thread = threading.Thread(target=worker)
    thread.start()
    try:
        assert entered.wait(2)
        # The worker holds coordinator.lock. Revocation must return immediately.
        receipt = fence.revoke(token)
        assert not receipt["released"] and receipt["active_actions"] == 1
        with pytest.raises(CaptureUnavailable):
            grant(fence, "two")
    finally:
        release_worker.set()
        thread.join(3)
    assert not thread.is_alive() and not failures
    assert fence.quiescence(token)["released"]


def test_callback_counts_as_action_until_surface_mutation_finishes(fence):
    token = grant(fence)
    receipts = []
    callback = fence.guard_callback(token, lambda: receipts.append(fence.revoke(token)))
    callback()
    assert receipts[0]["active_actions"] == 1 and not receipts[0]["released"]
    assert fence.quiescence(token)["released"]


@pytest.mark.parametrize("owner", ["Farmer", "Dutch"])
def test_coordinator_checks_bound_token_for_every_owner(fence, tmp_path, owner):
    token = grant(fence)
    guard = InputCoordinator(lambda: True, path=tmp_path / "input.lock")
    guard.fence = fence
    with fence.bind_worker(token):
        fence.revoke(token)
        with pytest.raises(CaptureUnavailable):
            with guard.lease(owner):
                pytest.fail("revoked worker acquired input")


def test_farmer_input_scope_checks_fence_and_manual_priority(fence, tmp_path):
    token = grant(fence)
    manual = [False]
    guard = InputCoordinator(
        lambda: True, lambda: manual[0], path=tmp_path / "input.lock"
    )
    guard.fence = fence
    install(guard)
    try:
        with fence.bind_worker(token):
            with input_scope():
                assert fence.quiescence(token)["active_actions"] == 1
                manual[0] = True
                with pytest.raises(CaptureUnavailable):
                    guard.check()
            fence.revoke(token)
            with pytest.raises(CaptureUnavailable):
                with input_scope():
                    pytest.fail("revoked farmer action")
    finally:
        install(None)


@pytest.mark.parametrize("active", [False, True])
def test_stop_invalidates_queued_idle_and_granted_callbacks(fence, active):
    if active:
        grant(fence)
    guard = InputCoordinator(lambda: True)
    guard.fence = fence
    mutations = []
    callback = fence.guard_callback(fence.capture(), lambda: mutations.append("show"))
    guard.stop()
    guard.resume()
    with pytest.raises(CaptureUnavailable):
        callback()
    assert mutations == []
