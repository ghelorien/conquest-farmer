from copy import deepcopy
import threading

import pytest

from conquest.merchants import manual_farmer
from conquest.merchants.manual_runtime import OBSERVATION_DEFERRED
from test_delivery_probe_manual_ownership import supervised, open_trade
from test_manual_runtime import rig


class ObservedLock:
    """Expose the real failed nonblocking acquisition without timing sleeps."""

    def __init__(self):
        self.lock = threading.RLock()
        self.waiting = threading.Event()

    def acquire(self, *args, **kwargs):
        acquired = self.lock.acquire(*args, **kwargs)
        if not acquired:
            self.waiting.set()
        return acquired

    def release(self):
        self.lock.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *_args):
        self.release()


def queued_call(x, call, advance):
    lock = ObservedLock()
    x.guard.lock = lock
    result, errors = [], []

    def work():
        try:
            result.append(call())
        except BaseException as error:
            errors.append(error)

    with lock:
        worker = threading.Thread(target=work)
        worker.start()
        assert lock.waiting.wait(5), "Observer did not reach the coordinator queue"
        advance()
    worker.join(5)
    assert not worker.is_alive()
    assert errors == []
    return result


def prepare(x, role, transition):
    if transition == "trade_to_terminal":
        open_trade(x, phase="offer_verified", offered=True)
    elif role == "farmer":
        # A queued incoming request is an observation, never automated delivery
        # authority. It too must be refreshed after an input-owner wait.
        x.farmer["request"] = {
            "participant": "Dutch",
            "participant_uid": 123,
            "message": "Dutch wishes to trade with you.",
        }


def advance(x, transition, delay=6):
    x.now += delay
    x.farmer["request"] = None
    if transition == "request_to_trade":
        open_trade(x)
    else:
        x.farmer["trade"] = None
        x.state.update(request=None, trade=None)
        x.probe.update(phase="delivery_verified", updated_at=x.now)
        x.save()


@pytest.mark.parametrize("role", ["farmer", "merchant"])
@pytest.mark.parametrize("transition", ["request_to_trade", "trade_to_terminal"])
@pytest.mark.parametrize("entry", ["manual", "production"])
@pytest.mark.parametrize("delay", [0.1, 6])
def test_fresh_observation_waiting_in_input_queue_cannot_create_a_manual_hold(
    supervised, monkeypatch, role, transition, entry, delay
):
    x = supervised
    prepare(x, role, transition)
    owner = "Farmer" if role == "farmer" else "Dutch"
    snapshot = x.farmer_read() if role == "farmer" else x.read()
    monkeypatch.setattr(manual_farmer, "presence", lambda _observer: True)
    monkeypatch.setattr(
        manual_farmer,
        "controller",
        lambda *_a: pytest.fail("Deferred observation cannot qualify input"),
    )
    if entry == "manual":
        call = lambda: x.runtime.process_manual(owner, snapshot)
    elif role == "farmer":
        call = x.runtime.observe_manual_farmer
    else:
        call = lambda: x.runtime.step("Dutch")
    queued_call(x, call, lambda: advance(x, transition, delay))
    assert x.runtime.manual_status() == []
    assert x.guard.manual_sessions == {}
    assert x.runtime._manual_get(owner, "manual_reader_hold") is None
    assert x.runtime.manual_sessions.audit() == []
    assert x.calls == []
    if entry == "production" and role == "farmer":
        assert x.runtime.manual_farmer_observation.get("observation_deferred") is True
        assert x.runtime.manual_farmer_observation.get("bot_owned") is not True
    # No terminal journal receives structural input privilege. A fresh, new
    # unknown request is admitted normally immediately after the skipped sample.
    fresh = x.farmer_read() if role == "farmer" else x.read()
    if transition == "request_to_trade":
        assert x.runtime.process_manual(owner, fresh)
        assert x.runtime.manual_status() == []
    else:
        assert x.runtime.process_manual(owner, fresh) is False
        fresh["request"] = {
            "participant": "Visitor",
            "participant_uid": 777,
            "message": "Visitor wishes to trade with you.",
        }
        assert x.runtime.process_manual(owner, fresh)
        assert x.runtime.manual_status(owner)["phase"] == "approval_pending"
    assert x.calls == []


@pytest.mark.parametrize("role", ["farmer", "merchant"])
@pytest.mark.parametrize(
    "fault", ["stale_on_entry", "missing_evidence", "changed_while_queued"]
)
def test_invalid_observation_is_not_hidden_by_coordinator_wait(supervised, role, fault):
    x = supervised
    open_trade(x, phase="offer_verified", offered=True)
    owner = "Farmer" if role == "farmer" else "Dutch"
    snapshot = x.farmer_read() if role == "farmer" else x.read()
    if fault == "stale_on_entry":
        snapshot["timestamp"] -= 6
    if fault == "missing_evidence":
        snapshot.pop("inventory")

    def finish():
        advance(x, "trade_to_terminal")
        if fault == "changed_while_queued":
            snapshot.pop("inventory")

    queued_call(x, lambda: x.runtime.process_manual(owner, snapshot), finish)
    row = x.runtime.manual_status(owner)
    assert row["phase"] == "needs_attention"
    assert x.guard.manual_session_blocked(owner)
    assert x.calls == []


@pytest.mark.parametrize("role", ["farmer", "merchant"])
@pytest.mark.parametrize("approved", [False, True])
def test_expired_queued_observation_preserves_existing_genuine_session(
    supervised, role, approved
):
    x = supervised
    owner = "Farmer" if role == "farmer" else "Dutch"
    incoming = x.farmer_read() if role == "farmer" else x.read()
    incoming["request"] = {
        "participant": "Visitor",
        "participant_uid": 777,
        "message": "Visitor wishes to trade with you.",
    }
    store = x.runtime.manual_sessions
    row = store.begin_request(owner, incoming, now=x.now)
    if approved:
        row = store.allow_and_activate(
            row["approval_binding"], incoming, operator="Floor", now=x.now
        )
    x.runtime._sync_manual_fence()
    original_row, audit, fence = (
        deepcopy(store.get(row["id"])),
        store.audit(),
        deepcopy(x.guard.manual_sessions),
    )
    open_trade(x, phase="offer_verified", offered=True)
    snapshot = x.farmer_read() if role == "farmer" else x.read()
    queued_call(
        x,
        lambda: x.runtime.process_manual(owner, snapshot),
        lambda: advance(x, "trade_to_terminal"),
    )
    assert store.get(row["id"]) == original_row
    assert store.audit() == audit and x.guard.manual_sessions == fence
    assert x.guard.manual_session_blocked(owner) and x.calls == []


@pytest.mark.parametrize("require_bilateral", [False, True])
@pytest.mark.parametrize("explicit_time", [False, True])
def test_probe_queue_deferral_never_becomes_a_bilateral_proof(
    supervised, require_bilateral, explicit_time
):
    x = supervised
    open_trade(x, phase="offer_verified", offered=True)
    snapshot = x.read()
    evidence_now = x.now if explicit_time else None
    result = queued_call(
        x,
        lambda: x.runtime.process_probe_owned(
            "Dutch", snapshot, require_bilateral=require_bilateral, now=evidence_now
        ),
        lambda: advance(x, "trade_to_terminal"),
    )
    assert result == [False if require_bilateral else OBSERVATION_DEFERRED]
    assert x.runtime.manual_status() == [] and x.calls == []


def test_manual_routing_and_admission_share_the_same_mutex(supervised, monkeypatch):
    x = supervised
    x.probe["phase"] = "delivery_verified"
    x.save()
    process_probe = x.runtime.process_probe_owned

    def route(*args, **kwargs):
        assert x.guard.lock._is_owned()
        return process_probe(*args, **kwargs)

    begin = x.runtime.manual_sessions.begin_request

    def admit(*args, **kwargs):
        assert x.guard.lock._is_owned()
        return begin(*args, **kwargs)

    monkeypatch.setattr(x.runtime, "process_probe_owned", route)
    monkeypatch.setattr(x.runtime.manual_sessions, "begin_request", admit)
    assert x.runtime.process_manual("Dutch", x.read())
    assert x.runtime.manual_status("Dutch")["phase"] == "approval_pending"


def queued_farmer_input(x, monkeypatch, kind):
    x.runtime.farmer_bot_owned = lambda: False
    if kind == "bot_trade":
        open_trade(x, phase="offer_verified", offered=True)
    else:
        x.farmer["request"] = {
            "participant": "Visitor",
            "participant_uid": 777,
            "message": "Visitor wishes to trade with you.",
        }
        x.probe["phase"] = "delivery_verified"
        x.save()
    monkeypatch.setattr(
        manual_farmer,
        "presence",
        lambda _observer: bool(x.farmer["request"] or x.farmer["trade"]),
    )
    monkeypatch.setattr(
        manual_farmer,
        "controller",
        lambda *_a: pytest.fail("Queued observation cannot qualify a decline"),
    )


@pytest.mark.parametrize("kind", ["unknown_request", "bot_trade"])
@pytest.mark.parametrize("delay", [0.1, 6])
def test_native_farm_deferral_blocks_dispatch_until_next_fresh_read(
    supervised, monkeypatch, kind, delay
):
    from test_native_farm import setup

    x = supervised
    queued_farmer_input(x, monkeypatch, kind)
    supervisor, control, life, _notifications = setup(monkeypatch)
    life.dead_candidate = False
    life.current_hp = life.max_hp
    x.source.operations = supervisor.observer.operations
    x.source.town_trade = supervisor.observer.town_trade
    supervisor.observer = x.source
    supervisor.read_life = lambda: life
    recovery, dispatch = [], []
    supervisor.recovery.step = lambda *_a: recovery.append(True)
    supervisor.dispatch = lambda callback: (dispatch.append(True), callback())[1]
    monkeypatch.setattr("conquest.mouse_priority.require_idle", lambda: None)
    saved_intent = control.snapshot()
    result = queued_call(
        x, supervisor.observe, lambda: advance(x, "trade_to_terminal", delay)
    )[0]
    assert result["waiting"] is True and result["manual_session"] is True
    assert recovery == [] and dispatch == []
    assert x.runtime.manual_status() == [] and x.guard.manual_sessions == {}
    assert x.runtime.manual_sessions.audit() == [] and x.calls == []
    assert control.snapshot() == saved_intent
    # Presence is read again from memory. No sticky pause or permission was
    # persisted by the discarded observation; closed windows permit this tick.
    fresh = supervisor.observe()
    assert fresh["waiting"] is False
    assert recovery == [True] and dispatch == [True]
    assert not x.runtime.manual_farmer_observation.get("observation_deferred")


@pytest.mark.parametrize("kind", ["unknown_request", "bot_trade"])
@pytest.mark.parametrize("delay", [0.1, 6])
def test_town_trade_deferral_blocks_execute_until_next_fresh_read(
    supervised, monkeypatch, kind, delay
):
    from conquest.town_trade import TownTrade, TownObservationUnavailable

    x = supervised
    queued_farmer_input(x, monkeypatch, kind)
    trade = TownTrade.__new__(TownTrade)
    trade.observer = x.source
    executions = []
    trade.execute = lambda body: (executions.append(body), {"executed": True})[1]
    body = {"action": "warehouse-deposit", "uid": 99}

    def blocked():
        with pytest.raises(TownObservationUnavailable, match="fresh read"):
            trade(body)

    queued_call(x, blocked, lambda: advance(x, "trade_to_terminal", delay))
    assert executions == [] and trade.input_attempted is False
    assert x.runtime.manual_status() == [] and x.guard.manual_sessions == {}
    assert x.runtime.manual_sessions.audit() == [] and x.calls == []
    assert trade(body) == {"executed": True}
    assert executions == [body]
    assert not x.runtime.manual_farmer_observation.get("observation_deferred")
