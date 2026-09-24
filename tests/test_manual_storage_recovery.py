import copy
import json
import threading
from types import SimpleNamespace as NS

import pytest

from conquest import manual_storage_recovery as recovery
from conquest.recovery_override import evidence_digest


@pytest.fixture
def rig(tmp_path, monkeypatch):
    from conquest import (
        banking,
        meteor_banking,
        overnight,
        restock_town_recovery,
        town_visit,
    )
    from conquest.merchants import service_visit

    monkeypatch.setattr(recovery, "REPORT", tmp_path / "report.json")
    monkeypatch.setattr(meteor_banking, "JOURNAL", tmp_path / "meteor.json")
    market_path = tmp_path / "market.json"
    monkeypatch.setattr(service_visit, "MarketVisit", lambda: NS(path=market_path))
    monkeypatch.setattr(recovery.time, "time", lambda: 100.0)
    monkeypatch.setattr(recovery, "_no_admissions", lambda since: None)
    monkeypatch.setattr(restock_town_recovery, "_other_holds", lambda: False)
    monkeypatch.setattr(
        town_visit, "checkpoint_verified_tail", lambda loop, kind: False
    )
    monkeypatch.setattr(overnight, "supply_counts", lambda bag, route: {})
    monkeypatch.setattr(overnight, "needs_town", lambda counts, route: False)
    target = {
        "pid": 18532,
        "creation_time_100ns": 134345064188672222,
        "path": "C:/game/ImConquer.exe",
        "architecture": "x64",
    }

    def item(uid, kind=130003, plus=1):
        return dict(uid=uid, type_id=kind, amount=1, limit=1, plus=plus, slot=uid)

    items = [item(i) for i in range(1, 8)] + [item(8, 720027, 0)]
    row = {
        "town_visit_id": "trip",
        "farmer_profile_id": "farmer",
        "phase": "town_work",
        "reasons": ["urgent_banking", "restock"],
        "required_at": 10.0,
        "route_id": "bandit",
        "hunt_map_id": 1011,
        "urgent_target": target,
        "urgent_banking_tail_completed_at": 20.0,
        "baseline": {"session_id": 1.0, "cursor": 30, "kills": 500},
    }
    meteor = {
        "origin": 1011,
        "started_at": 30.0,
        "phase": "storing_scroll",
        "exchange_verified": True,
        "scroll_uid": 8,
        "after": {"items": items},
    }
    market = {
        "phase": "active",
        "visit_id": "market",
        "town_visit_id": "trip",
        "farmer_profile_id": "farmer",
        "started_at": 40.0,
        "deadline": 50.0,
        "attempts": [],
    }
    body = {
        "action": "report-manual-phoenix-storage",
        "town_visit_id": "trip",
        "meteor_digest": evidence_digest(meteor),
        "target": target,
        "statement": "Phoenix warehouse, including equipment",
    }
    testimony = {
        "source": "explicit_user_report",
        "command": body,
        "reported_at": 60.0,
        "destination_map": 1011,
        "transaction_receipt": False,
    }
    record = {
        "phase": "reported",
        "report_id": evidence_digest(testimony),
        "operator_report": testimony,
        "original_visit": row,
        "original_meteor": meteor,
        "original_market": market,
        "target": target,
        "town_visit_id": "trip",
        "farmer_profile_id": "farmer",
        "expected_items": [{k: i[k] for k in recovery.BASIC} for i in items],
    }
    for path, value in (
        (recovery.REPORT, record),
        (meteor_banking.JOURNAL, meteor),
        (market_path, market),
    ):
        path.write_text(json.dumps(value))
    visit = town_visit.TownVisit(
        tmp_path / "visit.json", profile="farmer", probe=lambda: {}
    )
    visit.path.write_text(json.dumps(row))
    bag = {
        "items": [item(50, 1000020, 0)],
        "equipped_ammo": item(51, 1050002, 0),
        "silver": 250,
        "capacity": 40,
    }
    warehouse = {"items": copy.deepcopy(items), "capacity": 100, "bank_silver": 1000}
    health = {
        "target": target,
        "profile_id": "farmer",
        "embedded_controls": {
            "life": {"map_id": 1011, "current_hp": 100, "dead_candidate": False},
            "observed_at": 100.0,
            "control": {"enabled": False},
            "manual_mouse": False,
            "manual_input_fence": False,
        },
    }
    actions = []
    stopped = [False]
    source = {"map_id": 1011, "npc_id": 10, "position": [227, 246]}

    def check():
        if stopped[0]:
            raise RuntimeError("Manual Stop")

    def town(action, **kw):
        actions.append((action, kw))
        if action == "warehouse-locate":
            return copy.deepcopy(source)
        if action == "supplies":
            return copy.deepcopy(bag)
        if action == "warehouse-items":
            result = copy.deepcopy(warehouse)
            if kw.get("rich"):
                for i in result["items"]:
                    i.update(quantity=1, gem1=0, gem2=0, bound=False)
            return result
        if action == "warehouse-money":
            return {"silver": bag["silver"], "stored_silver": warehouse["bank_silver"]}
        if action == "close":
            return {}
        raise AssertionError("Unexpected town action " + action)

    loop = NS(
        check_stop=check,
        health=lambda: copy.deepcopy(health),
        identity=target,
        town_visit=visit,
        route=NS(id="bandit", restock_map_id=1011),
        town=town,
        stop_farm=check,
        record=lambda *a, **kw: None,
        adopt_ammunition=lambda: actions.append(("adopt_ammunition", {})),
    )
    monkeypatch.setattr(
        banking, "open_warehouse", lambda loop: actions.append(("open_warehouse", {}))
    )
    monkeypatch.setattr(
        banking, "close_warehouse", lambda loop: actions.append(("close_warehouse", {}))
    )
    monkeypatch.setattr(banking, "transport_reserve", lambda: 200)

    def transfer(loop, direction, amount):
        actions.append(("transfer", {"direction": direction, "amount": amount}))
        bag["silver"] += amount if direction == "withdraw" else -amount
        warehouse["bank_silver"] += amount if direction == "deposit" else -amount
        return {"verified": True}

    monkeypatch.setattr(banking, "transfer", transfer)
    return NS(
        loop=loop,
        record=record,
        bag=bag,
        warehouse=warehouse,
        source=source,
        actions=actions,
        stopped=stopped,
        health=health,
        body=body,
        meteor_path=meteor_banking.JOURNAL,
        market_path=market_path,
    )


def test_exact_manual_observation_finishes_only_cash_tail_and_preserves_originals(rig):
    original = copy.deepcopy(rig.record)
    assert recovery.resume(rig.loop)
    assert not recovery.pending()
    assert rig.actions[0][0] == "adopt_ammunition"
    assert [a for a, _ in rig.actions].count("transfer") == 1
    assert not any(
        "purchase" in a or "deposit" in a or "exchange" in a for a, _ in rig.actions
    )
    meteor = json.loads(rig.meteor_path.read_text())
    assert meteor["phase"] == "operator_overridden"
    assert meteor["operator_override"]["original_state"] == original["original_meteor"]
    assert (
        meteor["operator_override"]["fresh_evidence"]["manual_storage_observation"][
            "transaction_receipt"
        ]
        is False
    )
    market = json.loads(rig.market_path.read_text())
    assert (
        market["deadline"] == 50.0
        and market["departure_source"] == "manual_return_observed"
    )
    assert market["native_return_verified"] is False
    town = rig.loop.town_visit.state()
    assert town["baseline"] == original["original_visit"]["baseline"]
    assert town["town_work_completed_kind"] == "restock"
    assert town["manual_storage_recovery"]["native_cycle_verified"] is False
    before = list(rig.actions)
    assert not recovery.resume(rig.loop)
    assert rig.actions == before


@pytest.mark.parametrize(
    "fault",
    [
        "wrong_map",
        "wrong_vendor",
        "missing_uid",
        "changed_type",
        "changed_amount",
        "changed_plus",
        "also_carried",
        "changed_identity",
    ],
)
def test_fresh_evidence_must_match_exact_storage_and_original_process(rig, fault):
    if fault == "wrong_map":
        rig.health["embedded_controls"]["life"]["map_id"] = 1036
    elif fault == "wrong_vendor":
        rig.source["position"] = [182, 180]
    elif fault == "missing_uid":
        rig.warehouse["items"].pop()
    elif fault == "also_carried":
        rig.bag["items"].append(copy.deepcopy(rig.warehouse["items"][0]))
    elif fault == "changed_identity":
        rig.health["target"] = {**rig.health["target"], "pid": 123}
    else:
        rig.warehouse["items"][0][
            {
                "changed_type": "type_id",
                "changed_amount": "amount",
                "changed_plus": "plus",
            }[fault]
        ] += 1
    with pytest.raises(ValueError):
        recovery.resume(rig.loop)
    assert json.loads(rig.meteor_path.read_text())["phase"] == "storing_scroll"
    assert not any(a == "transfer" for a, _ in rig.actions)


def test_manual_stop_prevents_even_opening_warehouse(rig):
    rig.stopped[0] = True
    with pytest.raises(RuntimeError, match="Manual Stop"):
        recovery.resume(rig.loop)
    assert not rig.actions


def test_cash_submission_failure_never_replays(rig, monkeypatch):
    from conquest import banking

    calls = []

    def fail(*args):
        calls.append(True)
        raise OSError("lost result after submission")

    monkeypatch.setattr(banking, "transfer", fail)
    with pytest.raises(OSError):
        recovery.resume(rig.loop)
    assert recovery.pending()
    with pytest.raises(ValueError, match="uncertain; no replay"):
        recovery.resume(rig.loop)
    assert len(calls) == 1


def test_second_warehouse_observation_must_be_unchanged(rig):
    original = rig.loop.town
    reads = 0

    def changing(action, **kw):
        nonlocal reads
        result = original(action, **kw)
        if action == "warehouse-items":
            reads += 1
            if reads >= 3:
                result["bank_silver"] += 1
        return result

    rig.loop.town = changing
    with pytest.raises(ValueError, match="stable across two"):
        recovery.resume(rig.loop)
    assert not any(a == "transfer" for a, _ in rig.actions)


def test_advancing_native_sample_times_do_not_change_asset_ownership(rig):
    original = rig.loop.town
    tick = 0

    def sampled(action, **kw):
        nonlocal tick
        result = original(action, **kw)
        if action == "supplies":
            tick += 1
            result.update(started_at=100.0 + tick, timestamp=100.0 + tick + 0.01)
        return result

    rig.loop.town = sampled
    assert recovery.resume(rig.loop)
    proof = json.loads(recovery.REPORT.read_text())["observation"]
    assert proof["first_sample"]["bag"]["timestamp"] < proof["bag"]["timestamp"]


def test_changed_bag_assets_between_complete_samples_fail(rig):
    original = rig.loop.town
    reads = 0

    def changed(action, **kw):
        nonlocal reads
        if action == "supplies":
            reads += 1
            if reads == 3:
                rig.bag["items"][0]["amount"] += 1
        return original(action, **kw)

    rig.loop.town = changed
    with pytest.raises(ValueError, match="stable across two"):
        recovery.resume(rig.loop)
    assert not any(a == "transfer" for a, _ in rig.actions)


def test_report_while_off_records_testimony_only_and_preserves_unknown_fields(
    rig, monkeypatch
):
    from conquest import worker

    recovery.REPORT.unlink()
    monkeypatch.setattr(recovery, "TownVisit", lambda: rig.loop.town_visit)
    monkeypatch.setattr(recovery, "process_alive", lambda pid: False)
    monkeypatch.setattr(worker, "request", lambda *args: copy.deepcopy(rig.health))
    app = NS(
        control=NS(snapshot=lambda: {"enabled": False}),
        thread=None,
        last={"worker_info_path": "read-only-worker"},
    )
    ui = NS(app=app, coordinator=NS(lock=threading.RLock(), owner=None), grant=None)
    result = recovery.report_command(ui, rig.body)
    assert result["reported"] and result["verified_storage"] is False
    report = json.loads(recovery.REPORT.read_text())
    assert report["phase"] == "reported"
    assert report["operator_report"]["source"] == "explicit_user_report"
    assert report["unknown_historical_fields"]["8"] == list(recovery.OPTIONAL)
    assert not rig.actions
    assert json.loads(rig.meteor_path.read_text())["phase"] == "storing_scroll"
    assert recovery.report_command(ui, rig.body)["report_id"] == result["report_id"]
    app.control.snapshot = lambda: {"enabled": True}
    with pytest.raises(ValueError, match="Farming Off"):
        recovery.report_command(ui, rig.body)
