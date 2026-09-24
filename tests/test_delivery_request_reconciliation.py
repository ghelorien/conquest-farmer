from copy import deepcopy
import threading
import time
from types import SimpleNamespace

import pytest

from conquest.merchants import delivery_probe as probe, request_identity
from conquest.merchants import delivery_request_reconciliation as recovery


def setup(monkeypatch, tmp_path):
    now = time.time()
    item = dict(
        uid=10, type_id=410008, plus=1, gem1=0, gem2=0, quantity=1, bound=False, slot=0
    )

    def account(name, uid, inventory):
        return dict(
            character=name,
            character_uid=uid,
            identity={"pid": uid, "creation_time_100ns": 100 + uid, "path": "game.exe"},
            server="America",
            timestamp=now - 1,
            map_id=1036,
            hp=100,
            silver=200,
            capacity=40,
            position=[10, 10],
            inventory=inventory,
            booth=[],
            trade=None,
            request=None,
            own_booth_uid=0,
            booth_open=False,
        )

    farmer = account("Parasite", 1, [item])
    merchant = account("Spiritual", 2, [])
    state = {
        "phase": "request_submitted",
        "character": "Spiritual",
        "selected_uids": [10],
        "recipient": {"uid": 2, "name": "Spiritual", "position": [10, 10]},
        "intent": deepcopy({"farmer": farmer, "merchant": merchant, "items": [item]}),
        "error": "Incoming request actor identity changed",
    }
    merchant["request"] = {
        "participant": "Parasite",
        "participant_uid": 1,
        "message": "Parasite wishes to trade with you.",
    }
    snapshots = []
    for index in range(2):
        f, m = deepcopy(farmer), deepcopy(merchant)
        f["timestamp"] = m["timestamp"] = now - 0.5 + index * 0.1
        snapshots.append((f, m))
    path = tmp_path / "probe.json"
    monkeypatch.setattr(probe, "JOURNAL", path)
    probe.write_probe(path, state)
    calls = []

    def pair(*args):
        calls.append("pair")
        return deepcopy(snapshots[(len(calls) // 2) % 2])

    # Calls alternate pair/actor; modulo permits a second idempotent request.
    monkeypatch.setattr(probe, "pair", pair)
    actor = SimpleNamespace(lock=threading.RLock())

    def uid(observer, name):
        assert observer is actor and name == "Parasite"
        calls.append("actor")
        return 1

    monkeypatch.setattr(request_identity, "participant_uid", uid)
    monkeypatch.setattr(recovery.time, "sleep", lambda _: None)
    ui = SimpleNamespace(
        coordinator=SimpleNamespace(lock=threading.RLock(), owner=None),
        runtime=SimpleNamespace(observers={"Spiritual": actor}),
    )
    return ui, path, state, snapshots, calls


def test_reconcile_observes_stable_exact_request_without_any_gameplay_input(
    tmp_path, monkeypatch
):
    ui, path, state, snapshots, calls = setup(monkeypatch, tmp_path)
    result = recovery.reconcile_request(ui)
    assert result["phase"] == "request_verified" and result["gameplay_input"] is False
    assert not result["idempotent"] and result["uids"] == [10]
    assert calls == ["pair", "actor", "pair", "actor"]
    after = probe.read_probe()
    assert after["intent"] == state["intent"] and after["error"] is None
    assert (
        after["farmer_after"] == snapshots[1][0]
        and after["merchant_after"] == snapshots[1][1]
    )
    assert len(after["request_reconciliation"]["observations"]) == 2
    original = path.read_bytes()
    assert recovery.reconcile_request(ui)["idempotent"] is True
    assert path.read_bytes() == original


@pytest.mark.parametrize(
    "change",
    [
        "wrong_name",
        "wrong_uid",
        "missing_uid",
        "wrong_message",
        "request_disappears",
        "process_rollover",
        "farmer_position",
        "merchant_position",
        "inventory",
        "silver",
        "booth",
        "price",
        "farmer_trade",
        "merchant_trade",
        "farmer_request",
        "map",
        "dead",
        "stale",
        "same_timestamp",
        "wrong_server",
        "capacity",
        "slot",
        "actor_uid",
        "actor_changed",
    ],
)
def test_reconcile_rejects_every_identity_ownership_or_observation_mismatch(
    tmp_path, monkeypatch, change
):
    ui, path, state, snapshots, calls = setup(monkeypatch, tmp_path)
    farmer, merchant = snapshots[1]
    if change == "wrong_name":
        merchant["request"]["participant"] = "Someone"
    if change == "wrong_uid":
        merchant["request"]["participant_uid"] = 3
    if change == "missing_uid":
        merchant["request"].pop("participant_uid")
    if change == "wrong_message":
        merchant["request"]["message"] = "Someone wishes to trade with you."
    if change == "request_disappears":
        merchant["request"] = None
    if change == "process_rollover":
        farmer["identity"]["creation_time_100ns"] += 1
    if change == "farmer_position":
        farmer["position"] = [11, 10]
    if change == "merchant_position":
        merchant["position"] = [11, 10]
    if change == "inventory":
        farmer["inventory"] = []
    if change == "silver":
        merchant["silver"] += 1
    if change in ("booth", "price"):
        item = {**farmer["inventory"][0], "uid": 100, "price": 100}
        if change == "price":
            for snap in (state["intent"]["merchant"], snapshots[0][1]):
                snap["booth"] = [deepcopy(item)]
            probe.write_probe(path, state)
            item["price"] = 200
        merchant["booth"] = [item]
    if change == "farmer_trade":
        farmer["trade"] = {"participant": "Spiritual"}
    if change == "merchant_trade":
        merchant["trade"] = {"participant": "Parasite"}
    if change == "farmer_request":
        farmer["request"] = {"participant": "Spiritual"}
    if change == "map":
        farmer["map_id"] = 1011
    if change == "dead":
        merchant["hp"] = 0
    if change == "stale":
        farmer["timestamp"] -= 6
    if change == "same_timestamp":
        farmer["timestamp"] = snapshots[0][0]["timestamp"]
    if change == "wrong_server":
        merchant["server"] = "Other"
    if change == "capacity":
        merchant["capacity"] -= 1
    if change == "slot":
        farmer["inventory"][0]["slot"] = 1
    if change == "actor_uid":
        monkeypatch.setattr(request_identity, "participant_uid", lambda *args: 3)
    if change == "actor_changed":

        def changed(*args):
            raise ValueError("Incoming request actor identity changed")

        monkeypatch.setattr(request_identity, "participant_uid", changed)
    before = path.read_bytes()
    with pytest.raises(ValueError):
        recovery.reconcile_request(ui)
    assert (
        path.read_bytes() == before
        and probe.read_probe()["phase"] == "request_submitted"
    )


@pytest.mark.parametrize(
    "phase",
    [
        "prepared",
        "targeting_verified",
        "accept_submitted",
        "offer_verified",
        "farmer_confirm_submitted",
        "delivery_verified",
        "operator_overridden",
        "unknown",
    ],
)
def test_only_submitted_request_may_advance(tmp_path, monkeypatch, phase):
    ui, path, state, _, calls = setup(monkeypatch, tmp_path)
    probe.write_probe(path, {**state, "phase": phase})
    with pytest.raises(ValueError, match="Only a submitted"):
        recovery.reconcile_request(ui)
    assert not calls


@pytest.mark.parametrize("worker", ["probe", "delivery"])
def test_running_input_worker_prevents_reconciliation(tmp_path, monkeypatch, worker):
    ui, path, state, _, calls = setup(monkeypatch, tmp_path)
    active = SimpleNamespace(is_alive=lambda: True)
    if worker == "probe":
        ui.delivery_probe_thread = active
    else:
        ui.delivery_workers = {"operation": active}
    with pytest.raises(ValueError, match="Wait for delivery input"):
        recovery.reconcile_request(ui)
    assert not calls and probe.read_probe() == state


@pytest.mark.parametrize("failure", ["write", "flush", "publish"])
def test_persistence_failure_keeps_original_action_phase(
    tmp_path, monkeypatch, failure
):
    ui, path, state, _, _ = setup(monkeypatch, tmp_path)
    before = path.read_bytes()

    def fail(*args, **kwargs):
        raise OSError("journal persistence failed")

    if failure == "write":
        fdopen = recovery.os.fdopen

        class BrokenWrite:
            def __init__(self, fd, mode):
                self.stream = fdopen(fd, mode)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.stream.close()

            write = staticmethod(fail)

        monkeypatch.setattr(recovery.os, "fdopen", BrokenWrite)
    if failure == "flush":
        monkeypatch.setattr(recovery.os, "fsync", fail)
    if failure == "publish":
        monkeypatch.setattr(recovery.os, "replace", fail)
    with pytest.raises(OSError, match="journal persistence failed"):
        recovery.reconcile_request(ui)
    assert path.read_bytes() == before
    assert list(tmp_path.glob("*.tmp")) == []


def test_verified_request_idempotency_requires_saved_after_evidence(
    tmp_path, monkeypatch
):
    ui, path, state, _, _ = setup(monkeypatch, tmp_path)
    probe.write_probe(path, {**state, "phase": "request_verified"})
    with pytest.raises(ValueError, match="lacks its original"):
        recovery.reconcile_request(ui)


def test_request_reconciliation_bridge_is_strict_and_input_free(monkeypatch):
    from conquest.merchants.ui import UnifiedUI

    ui = SimpleNamespace()
    calls = []
    monkeypatch.setattr(
        recovery,
        "reconcile_request",
        lambda target: calls.append(target) or {"gameplay_input": False},
    )
    assert UnifiedUI.dispatch(ui, {"action": "probe-delivery-reconcile-request"}) == {
        "gameplay_input": False
    }
    with pytest.raises(
        ValueError, match="Unsupported request reconciliation arguments"
    ):
        UnifiedUI.dispatch(
            ui, {"action": "probe-delivery-reconcile-request", "uids": [10]}
        )
    assert calls == [ui]


def test_concurrent_journal_update_is_not_overwritten(tmp_path, monkeypatch):
    ui, path, state, _, _ = setup(monkeypatch, tmp_path)
    observe = recovery.observe
    reads = []

    def mutate_after_read(*args):
        result = observe(*args)
        reads.append(result)
        if len(reads) == 2:
            probe.write_probe(path, {**state, "phase": "accept_submitted"})
        return result

    monkeypatch.setattr(recovery, "observe", mutate_after_read)
    with pytest.raises(ValueError, match="changed during read-only"):
        recovery.reconcile_request(ui)
    assert probe.read_probe()["phase"] == "accept_submitted"


def test_concurrent_worker_start_is_detected_before_journal_promotion(
    tmp_path, monkeypatch
):
    ui, path, state, _, _ = setup(monkeypatch, tmp_path)
    observe = recovery.observe

    def worker_after_read(*args):
        result = observe(*args)
        ui.delivery_probe_thread = SimpleNamespace(is_alive=lambda: True)
        return result

    monkeypatch.setattr(recovery, "observe", worker_after_read)
    with pytest.raises(ValueError, match="Wait for delivery input"):
        recovery.reconcile_request(ui)
    assert probe.read_probe() == state


def test_same_phase_with_changed_after_evidence_is_not_idempotent(
    tmp_path, monkeypatch
):
    ui, path, state, _, _ = setup(monkeypatch, tmp_path)
    recovery.reconcile_request(ui)
    verified = probe.read_probe()
    verified["merchant_after"]["silver"] += 1
    probe.write_probe(path, verified)
    with pytest.raises(ValueError):
        recovery.reconcile_request(ui)
    assert probe.read_probe() == verified
