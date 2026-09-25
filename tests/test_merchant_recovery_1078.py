"""Milestone 1 of automatic 1078 merchant disconnect recovery (login only)."""

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
