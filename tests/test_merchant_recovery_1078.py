"""Milestone 1 of automatic 1078 merchant disconnect recovery (login only).

Provenance: the baseline (part a) and login-detection/rebind/incident/alert
(parts b, c) tests in this module were written after their code, before the
AGENTS.md testing rule (3b497a0) existed. Later stages are written
failure-modes-first in tests/test_merchant_recovery_1078_login.py.
"""

import threading
import time
from types import SimpleNamespace

import pytest

from conquest.memory_build_layout import CLIENT_SHA256_1078
from conquest.merchants import return_1078
from conquest.merchants.coordination import InputCoordinator
from conquest.merchants.journal import Journal
from conquest.merchants.runtime import MerchantRuntime

IDENTITY = {
    "pid": 4100,
    "creation_time_100ns": 133000000000000000,
    "path": "C:/Co/ImConquer.exe",
}
OTHER = {
    "pid": 4200,
    "creation_time_100ns": 133000000000000001,
    "path": "C:/Co/ImConquer.exe",
}
PROFILE = SimpleNamespace(
    id="merchant-spiritual", name="Spiritual", server="America", character_uid=1001
)


def item(uid, price=None):
    return {
        "uid": uid,
        "type_id": 130805,
        "name": "Coat",
        "plus": 2,
        "gem1": 0,
        "gem2": 0,
        "quantity": 1,
        "bound": False,
        "price": price,
    }


def market(**overrides):
    value = {
        "character": "Spiritual",
        "character_uid": 1001,
        "identity": dict(IDENTITY),
        "server": "America",
        "inventory": [item(1)],
        "booth": [item(2, 250000), item(3, 90000)],
        "capacity": 40,
        "silver": 5000,
        "map_id": 1036,
        "position": [180, 190],
        "hp": 500,
        "health": {"current_hp_candidate": 500, "max_hp_candidate": 500},
        "own_booth_uid": 77001,
        "booth_open": True,
        "trade": None,
        "request": None,
        "timestamp": time.time(),
    }
    value.update(overrides)
    return value


@pytest.fixture
def rt(tmp_path, monkeypatch):
    monkeypatch.setattr(return_1078, "_profile", lambda character: PROFILE)
    journal = Journal(tmp_path / "journal.sqlite3")
    coordinator = InputCoordinator(lambda: True, path=tmp_path / "input.lock")
    catalog = SimpleNamespace(identities=lambda: [], windows=lambda **_: [])
    runtime = MerchantRuntime(catalog, coordinator, journal=journal)
    runtime.native1078_farmer_check = lambda: {"farmer": "parked"}
    return runtime


def test_baseline_is_written_only_for_a_healthy_owned_market_booth(rt):
    assert return_1078.baseline(rt, "Spiritual") is None
    for rejected in (
        market(map_id=1002),
        market(booth_open=False),
        market(own_booth_uid=0),
        market(trade={"participant": "X"}),
        market(request={"participant": "X"}),
        market(character_uid=999),
        market(hp=0),
    ):
        assert return_1078.record_baseline(rt, "Spiritual", rejected) is False
    assert rt.journal.get("Spiritual", return_1078.BASELINE) is None
    snapshot = market()
    assert return_1078.record_baseline(rt, "Spiritual", snapshot) is True
    saved = return_1078.baseline(rt, "Spiritual")
    assert saved["snapshot"] == snapshot
    assert saved["client_sha256"] == CLIENT_SHA256_1078
    assert saved["profile_id"] == PROFILE.id
    assert saved["character_uid"] == 1001 and saved["identity"] == IDENTITY
    assert saved["map_id"] == 1036 and saved["position"] == [180, 190]
    assert saved["own_booth_uid"] == 77001
    assert [p["price"] for p in saved["booth_prices"]] == [250000, 90000]


def test_baseline_is_never_overwritten_outside_market_or_during_pending_work(rt):
    assert return_1078.record_baseline(rt, "Spiritual", market())
    before = rt.journal.get("Spiritual", return_1078.BASELINE)
    # A later non-Market snapshot (for example Twin City after relog) or an
    # open trade never replaces the pre-loss evidence.
    assert not return_1078.record_baseline(
        rt, "Spiritual", market(map_id=1002, booth=[], silver=1)
    )
    assert not return_1078.record_baseline(
        rt, "Spiritual", market(request={"participant": "Visitor"}, booth=[])
    )
    rt.journal.begin("tx-1", "Spiritual", "listing", {"uid": 1})
    assert not return_1078.record_baseline(rt, "Spiritual", market(booth=[]))
    assert rt.journal.get("Spiritual", return_1078.BASELINE) == before


def test_baseline_freezes_while_an_incident_is_unresolved(rt):
    assert return_1078.record_baseline(rt, "Spiritual", market())
    before = rt.journal.get("Spiritual", return_1078.BASELINE)
    rt.journal.set("Spiritual", return_1078.KEY, {"id": "x", "phase": "login"})
    rt._market_baseline_1078.clear()
    assert not return_1078.record_baseline(
        rt, "Spiritual", market(booth=[item(2, 1)], silver=9)
    )
    assert rt.journal.get("Spiritual", return_1078.BASELINE) == before
    rt.journal.set("Spiritual", return_1078.KEY, {"id": "x", "phase": "complete"})
    assert return_1078.record_baseline(rt, "Spiritual", market(silver=9))


def test_tampered_or_foreign_baseline_is_rejected(rt, monkeypatch):
    assert return_1078.record_baseline(rt, "Spiritual", market())
    saved = rt.journal.get("Spiritual", return_1078.BASELINE)
    tampered = dict(saved, position=[1, 1])
    rt.journal.set("Spiritual", return_1078.BASELINE, tampered)
    assert return_1078.baseline(rt, "Spiritual") is None
    rt.journal.set("Spiritual", return_1078.BASELINE, saved)
    assert return_1078.baseline(rt, "Spiritual") is not None
    monkeypatch.setattr(
        return_1078,
        "_profile",
        lambda c: SimpleNamespace(**{**vars(PROFILE), "character_uid": 5}),
    )
    assert return_1078.baseline(rt, "Spiritual") is None


def test_unchanged_baseline_is_not_rewritten_every_observation(rt):
    assert return_1078.record_baseline(rt, "Spiritual", market(), now=100.0)
    assert not return_1078.record_baseline(rt, "Spiritual", market(), now=110.0)
    assert return_1078.record_baseline(rt, "Spiritual", market(silver=1), now=111.0)
    assert return_1078.record_baseline(rt, "Spiritual", market(silver=1), now=200.0)


def test_sales_observation_cannot_replace_the_recovery_baseline(rt):
    from conquest.merchants.sales import observe

    assert return_1078.record_baseline(rt, "Spiritual", market())
    before = return_1078.baseline(rt, "Spiritual")
    observe(rt.journal, market(map_id=1002, booth=[], timestamp=time.time() + 30))
    assert return_1078.baseline(rt, "Spiritual") == before


def fake_observer(identity=IDENTITY, hwnd=77, **extra):
    adapter = SimpleNamespace(
        identity=dict(identity),
        expected_sha256=CLIENT_SHA256_1078,
        assert_identity=lambda: None,
    )
    return SimpleNamespace(
        adapter=adapter,
        hwnd=hwnd,
        lock=threading.RLock(),
        merchant_observation_only=True,
        character_context=None,
        operations=SimpleNamespace(target=SimpleNamespace(hwnd=hwnd)),
        close=lambda: None,
        **extra,
    )


def test_step_observation_records_the_market_baseline(rt, monkeypatch):
    snapshot = market()
    observer = fake_observer(read_ownership=lambda: snapshot)
    rt.observers["Spiritual"] = observer
    monkeypatch.setattr("conquest.merchants.sales.observe", lambda journal, s: None)
    rt.step_observation_1078("Spiritual")
    assert return_1078.baseline(rt, "Spiritual")["snapshot"] == snapshot


# --- Login detection, pinned rebind, one durable incident and #shops alerts ---


@pytest.fixture
def login(monkeypatch):
    """Native login proof controls for fake observers (no Win32/memory)."""
    proof = {"shell": True, "memory": True}
    monkeypatch.setattr(return_1078, "_login_shell", lambda hwnd: proof["shell"])

    def memory(adapter):
        if not proof["memory"]:
            raise ValueError("Requested GUI window is not active")
        return True

    monkeypatch.setattr(return_1078, "_login_memory", memory)
    return proof


def forbid(name):
    def fail(*args, **kwargs):
        raise AssertionError(name + " must not run at the login screen")

    return fail


def events(runtime, name):
    return [e for e in runtime.journal.events(0, 1000) if e["event"] == name]


def test_window_title_alone_never_proves_the_login_screen(login):
    observer = fake_observer()
    assert return_1078.at_login(observer)
    login["memory"] = False
    assert not return_1078.at_login(observer)
    login.update(shell=False, memory=True)
    assert not return_1078.at_login(observer)


def test_login_arms_one_incident_and_skips_sales_trade_and_refill(
    rt, login, monkeypatch
):
    assert return_1078.record_baseline(rt, "Spiritual", market())
    observer = fake_observer(read_ownership=forbid("ownership read"))
    rt.observers["Spiritual"] = observer
    monkeypatch.setattr("conquest.merchants.sales.observe", forbid("sales"))
    monkeypatch.setattr(rt, "step_trade_1078", forbid("trade"))
    rt.refill1078_step = forbid("refill")
    rt.latest["Spiritual"] = market()
    for _ in range(3):
        rt.step_observation_1078("Spiritual")
    state = rt.journal.get("Spiritual", return_1078.KEY)
    assert state["phase"] == "login" and state["login_attempted"] is False
    assert state["identity"] == IDENTITY
    assert state["authorization"] == return_1078.AUTHORIZATION
    assert state["before"] == return_1078.baseline(rt, "Spiritual")
    assert len(events(rt, "native_return_started")) == 1
    assert "Spiritual" not in rt.latest
    # The protective-disconnect watchdog and 1074 recovery identity stay unarmed.
    assert rt.journal.get("Spiritual", "recovery_safety") is None
    assert rt.journal.get("Spiritual", "last_identity") is None
    status = rt.status()["Spiritual"]
    assert status["native_return_1078"]["phase"] == "login"
    assert "before" not in status["native_return_1078"]
    assert status["activity"] == "Disconnect recovery: login"


def test_incident_is_armed_even_while_operations_and_refill_are_off(rt, login):
    rt.enable("Spiritual", False)
    rt.set_refill_enabled("Spiritual", False)
    assert return_1078.record_baseline(rt, "Spiritual", market())
    return_1078.on_login(rt, "Spiritual", fake_observer())
    assert rt.journal.get("Spiritual", return_1078.KEY)["phase"] == "login"


def test_unknown_process_at_login_is_ignored(rt, login):
    assert return_1078.record_baseline(rt, "Spiritual", market())
    assert return_1078.on_login(rt, "Spiritual", fake_observer(OTHER)) is None
    assert rt.journal.get("Spiritual", return_1078.KEY) is None
    assert "not the last verified" in rt.login1078_status["Spiritual"]["reason"]
    rt.journal.set("Spiritual", return_1078.BASELINE, None)
    assert return_1078.on_login(rt, "Spiritual", fake_observer()) is None
    assert rt.journal.get("Spiritual", return_1078.KEY) is None


def test_identity_change_during_an_incident_needs_attention(rt, login):
    assert return_1078.record_baseline(rt, "Spiritual", market())
    return_1078.on_login(rt, "Spiritual", fake_observer())
    return_1078.on_login(rt, "Spiritual", fake_observer(OTHER))
    state = rt.journal.get("Spiritual", return_1078.KEY)
    assert state["phase"] == "needs_attention" and "identity" in state["note"]
    # A later login observation never re-arms or replays anything.
    return_1078.on_login(rt, "Spiritual", fake_observer())
    assert len(events(rt, "native_return_started")) == 1


def client(identity, hwnd):
    return SimpleNamespace(identity=dict(identity), hwnd=hwnd)


def rebind_setup(rt, monkeypatch, clients):
    context = SimpleNamespace(
        profile=SimpleNamespace(
            local_enabled=True, name="Spiritual", server="America", character_uid=1001
        )
    )
    monkeypatch.setattr(
        "conquest.character_context.merchant_context", lambda character: context
    )
    monkeypatch.setattr(rt, "merchant_windows", lambda: clients)
    monkeypatch.setattr("conquest.reconnect.login_screen", lambda hwnd: True)
    monkeypatch.setattr(
        "conquest.input_probe.MessageTarget",
        lambda pid, hwnd: SimpleNamespace(pid=pid, hwnd=hwnd),
    )
    built = []

    def factory(client, character, *, context=None, pinned=None):
        if pinned is None or client.identity != pinned:
            raise ValueError("Login rebind requires the pinned merchant process")
        observer = fake_observer(client.identity, client.hwnd, login_rebound=True)
        built.append(observer)
        return observer

    rt.login_observer_factory = factory
    return built


def test_app_restart_at_login_rebinds_only_the_pinned_process(rt, login, monkeypatch):
    built = rebind_setup(rt, monkeypatch, [client(OTHER, 5), client(IDENTITY, 6)])
    with pytest.raises(ValueError, match="found 0"):
        rt.step_observation_1078("Spiritual")  # No baseline: nothing is pinned.
    assert not built and "Spiritual" not in rt.observers
    assert return_1078.record_baseline(rt, "Spiritual", market())
    rt.step_observation_1078("Spiritual")
    observer = rt.observers["Spiritual"]
    assert observer.adapter.identity == IDENTITY and observer.hwnd == 6
    assert observer.operations.target.hwnd == 6
    assert "Spiritual" not in rt.controllers
    assert rt.journal.get("Spiritual", "last_identity") is None
    assert len(events(rt, "native_login_rebound")) == 1
    # The next observation of the rebound process arms the incident once.
    rt.step_observation_1078("Spiritual")
    assert rt.journal.get("Spiritual", return_1078.KEY)["identity"] == IDENTITY


def test_restart_never_rebinds_an_unpinned_login_process(rt, login, monkeypatch):
    assert return_1078.record_baseline(rt, "Spiritual", market())
    built = rebind_setup(rt, monkeypatch, [client(OTHER, 5)])
    with pytest.raises(ValueError, match="found 0"):
        rt.step_observation_1078("Spiritual")
    assert not built and "Spiritual" not in rt.observers


def test_restart_rebind_requires_memory_login_proof(rt, login, monkeypatch):
    assert return_1078.record_baseline(rt, "Spiritual", market())
    closed = []
    built = rebind_setup(rt, monkeypatch, [client(IDENTITY, 6)])
    login["memory"] = False
    original = rt.login_observer_factory

    def factory(*args, **kwargs):
        observer = original(*args, **kwargs)
        observer.close = lambda: closed.append(True)
        return observer

    rt.login_observer_factory = factory
    with pytest.raises(ValueError, match="memory-proven login"):
        rt.step_observation_1078("Spiritual")
    assert built and closed and "Spiritual" not in rt.observers


def test_login_rebind_observer_rejects_an_unpinned_client():
    from conquest.merchants.read_only_observer import LoginMerchantObserver1078

    with pytest.raises(ValueError, match="pinned"):
        LoginMerchantObserver1078(client(OTHER, 5), "Spiritual", pinned=IDENTITY)
    with pytest.raises(ValueError, match="pinned"):
        LoginMerchantObserver1078(client(OTHER, 5), "Spiritual", pinned=None)


def shops(alerts):
    """Merchant-subject messages only (independent bank-stock checks excluded)."""
    return [m for m in alerts.state["queue"] if m["subject"].startswith("Spiritual")]


def status_row(**overrides):
    row = {
        "enabled": False,
        "connected": False,
        "pending": [],
        "refill": {"enabled": False},
    }
    row.update(overrides)
    return row


def test_operations_off_disconnect_with_an_incident_alerts_shops():
    from conquest.merchants.alerts import Alerts

    alerts = Alerts()
    quiet = {"characters": {"Spiritual": status_row()}}
    alerts.poll(quiet, 1000.0)
    alerts.poll(quiet, 2000.0)
    assert shops(alerts) == []  # Existing quiet manual wait is unchanged.
    native = {"id": "inc-1", "phase": "login", "login_attempted": False}
    armed = {"characters": {"Spiritual": status_row(native_return_1078=native)}}
    alerts.poll(armed, 2001.0)
    [message] = shops(alerts)
    assert message["kind"] == "failure" and "login screen" in message["content"]


def test_manual_fence_does_not_silence_a_disconnect_incident():
    from conquest.merchants.alerts import Alerts

    alerts = Alerts()
    native = {"id": "inc-1", "phase": "login", "login_attempted": False}
    row = status_row(manual_input_fence=True, native_return_1078=native)
    alerts.poll({"characters": {"Spiritual": row}}, 10.0)
    assert [m["kind"] for m in shops(alerts)] == ["failure"]


def test_login_without_a_baseline_alerts_after_a_minute():
    from conquest.merchants.alerts import Alerts

    alerts = Alerts()
    login_row = status_row(
        login_1078={"at_login": True, "reason": "No verified Market baseline"}
    )
    alerts.poll({"characters": {"Spiritual": login_row}}, 10.0)
    assert shops(alerts) == []
    alerts.poll({"characters": {"Spiritual": login_row}}, 71.0)
    [message] = shops(alerts)
    assert "login screen" in message["content"]


def test_needs_attention_notice_is_sent_once_per_incident():
    from conquest.merchants.alerts import Alerts

    alerts = Alerts()
    native = {"id": "inc-2", "phase": "needs_attention", "note": "identity changed"}
    row = status_row(native_return_1078=native)
    for now in (10.0, 20.0, 30.0):
        alerts.poll({"characters": {"Spiritual": row}}, now)
    notices = [m for m in alerts.state["queue"] if m["kind"] == "notice"]
    assert len(notices) == 1 and "identity changed" in notices[0]["content"]
    restarted = Alerts({**alerts.state, "queue": []})
    restarted.poll({"characters": {"Spiritual": row}}, 40.0)
    assert not [m for m in restarted.state["queue"] if m["kind"] == "notice"]


def test_watchdog_never_counts_login_or_loading(rt):
    from conquest.merchants import recovery_safety

    rt.journal.set(
        "Spiritual",
        recovery_safety.KEY,
        {"active": True, "phase": "logging_in", "last_progress": 0.0},
    )
    for phase in ("login", "login_submitted", "needs_attention"):
        rt.journal.set("Spiritual", return_1078.KEY, {"id": "inc", "phase": phase})
        assert recovery_safety.observe(
            rt, "Spiritual", IDENTITY, None, now=10_000.0, close=forbid("close")
        )
    state = rt.journal.get("Spiritual", recovery_safety.KEY)
    assert state["phase"] == "logging_in" and state["last_progress"] is None
    assert not rt.journal.get("Spiritual", "connect_hold", False)
