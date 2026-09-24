from copy import deepcopy
import json
import threading
import time
from types import SimpleNamespace as NS

import pytest

from conquest import merchant_loop_acceptance as acceptance
from conquest.discord_notify import read_json, write_json
from conquest.merchants import (
    service_retry as retry,
    service_visit,
    delivery_journey,
    delivery_route,
)
from conquest.merchants import delivery_operation, handoff
from conquest.merchants.journal import Journal
from test_merchant_loop_acceptance import (
    rig as acceptance_rig,
    enable,
    admission,
    delivered,
    item,
)


@pytest.fixture
def rig(acceptance_rig, monkeypatch, tmp_path):
    r = acceptance_rig
    enable(r)
    r.selected = r.trigger()
    town = r.town()
    r.farmer["map_id"] = 1036
    original = service_visit.MarketVisit
    monkeypatch.setattr(
        service_visit, "MarketVisit", lambda: original(tmp_path / "visit.json")
    )
    r.visit = service_visit.MarketVisit().begin(
        parent=town["town_visit_id"], profile="Parasite"
    )
    r.visit.update(
        started_at=time.time() - 120,
        deadline=time.time() - 60,
        attempts=[{"merchant": "Spiritual", "outcome": "deferred_before_input"}],
    )
    write_json(service_visit.MarketVisit().path, r.visit)
    r.journey = {
        "phase": "market",
        "acceptance_scope": acceptance.journey_scope(),
        "receipts": [],
    }
    write_json(delivery_journey.JOURNAL, r.journey)
    r.windows = handoff.WorkWindows(tmp_path / "windows.json")
    monkeypatch.setattr(handoff, "WorkWindows", lambda: r.windows)
    monkeypatch.setattr(delivery_operation, "JOURNAL", tmp_path / "deliveries.sqlite3")
    r.source_journal = Journal(delivery_operation.JOURNAL)
    r.ui.runtime.journal = Journal(tmp_path / "merchants.sqlite3")
    r.ui.coordinator.lock = threading.RLock()
    r.ui.runtime.lock = threading.RLock()
    monkeypatch.setattr(retry, "ROUTE_STATUS", tmp_path / "route-status.json")
    monkeypatch.setattr(retry, "ROUTE_STOP", tmp_path / "route.stop")
    monkeypatch.setattr("conquest.discord_notify.process_alive", lambda pid: pid == 123)
    r.loop.state = {"pid": 123, "started_at": time.time() - 10}
    r.loop.check_stop = lambda: None

    def route_status(*args, **kw):
        write_json(
            retry.ROUTE_STATUS,
            {
                **r.loop.state,
                "route": "bandit",
                "phase": "restocking",
                "town_visit_id": town["town_visit_id"],
                "updated_at": time.time(),
            },
        )

    r.loop.record = route_status
    route_status()
    r.ui.safe_to_yield = lambda: (
        False
    )  # A live town controller cannot yield without its later grant.
    r.body = {
        "action": "delivery-service-retry",
        "visit_id": r.visit["visit_id"],
        "run_id": acceptance.state()["run_id"],
        "cycle_id": acceptance.state()["active"]["cycle_id"],
        "route_pid": 123,
        "route_started_at": r.loop.state["started_at"],
    }
    r.dispatch = lambda: retry.dispatch(r.ui, r.body)
    return r


def test_exact_unadmitted_retry_is_bounded_durable_and_idempotent(rig, monkeypatch):
    first = retry.route_retry(
        rig.loop, rig.visit, lambda body: retry.dispatch(rig.ui, body)
    )
    assert (
        first["visit_id"] != rig.visit["visit_id"]
        and first["retry_of"] == rig.visit["visit_id"]
    )
    assert first["deadline"] == first["started_at"] + 60 and first["attempts"] == []
    record = acceptance.state()["active"]["service_retry"]
    assert record["previous_visit"] == rig.visit
    assert (
        record["item"] == rig.selected
        and record["farmer_identity"] == rig.farmer["identity"]
    )
    assert (record["run_id"], record["cycle_id"]) == (
        rig.body["run_id"],
        rig.body["cycle_id"],
    )
    assert rig.dispatch() == {"visit": first}  # Lost reply cannot allocate again.
    monkeypatch.setattr(time, "time", lambda: first["deadline"] + 10)
    # Since 4a33737 the bridge, not route_retry, bounds retries per controller:
    # the same route controller cannot earn a second service window.
    with pytest.raises(ValueError, match="fresh native town controller"):
        retry.route_retry(rig.loop, first, lambda body: retry.dispatch(rig.ui, body))
    assert rig.dispatch() == {"visit": first}
    assert read_json(service_visit.MarketVisit().path) == first


def test_crash_after_retry_seal_recovers_same_deadline(rig, monkeypatch):
    def crash(*args):
        raise OSError("simulated visit write failure")

    monkeypatch.setattr(retry, "write_json", crash)
    with pytest.raises(OSError, match="simulated"):
        rig.dispatch()
    saved = acceptance.state()["active"]["service_retry"]
    assert read_json(service_visit.MarketVisit().path) == rig.visit
    monkeypatch.setattr(retry, "write_json", write_json)
    monkeypatch.setattr(time, "time", lambda: saved["visit"]["started_at"] + 20)
    rig.fresh()
    rig.loop.record("heartbeat")
    assert rig.dispatch() == {"visit": saved["visit"]}
    assert (
        read_json(service_visit.MarketVisit().path)["deadline"]
        == saved["visit"]["deadline"]
    )


def test_running_native_market_route_retries_then_admits_exact_item_once(
    rig, monkeypatch, tmp_path
):
    from conquest.merchants import farmer_preferences

    # Transfers default Off since 3fe0f7f; the operator enables this farmer.
    monkeypatch.setattr(farmer_preferences, "PATH", tmp_path / "preferences.json")
    farmer_preferences.set_enabled("Parasite", True)
    events = []
    receipts = {}
    transferred = []
    health = {
        "target": deepcopy(rig.farmer["identity"]),
        "embedded_controls": {
            "control": {"enabled": False, "paused": False, "revision": 7},
            "life": {"map_id": 1036},
            # Pre-admission safety needs a fresh, complete observation (186bcfa).
            "observations_available": True,
            "monsters": [],
        },
    }

    def fresh_health():
        result = deepcopy(health)
        result["embedded_controls"]["observed_at"] = time.time()
        return result

    rig.loop.living = rig.loop.health = fresh_health
    rig.loop.focus = lambda h: None
    rig.loop.town = lambda action, **kw: events.append(action)
    plan = {
        "merchant": "Spiritual",
        "merchant_identity": rig.merchant["identity"],
        "merchant_uid": rig.merchant["character_uid"],
        "items": [rig.selected],
        "position": [180, 200],
    }
    monkeypatch.setattr(
        delivery_route, "candidates", lambda *a, **kw: [] if transferred else [plan]
    )
    monkeypatch.setattr(delivery_route, "approach_merchant", lambda *a, **kw: True)
    monkeypatch.setattr(delivery_route, "WorkWindows", lambda: rig.windows)
    monkeypatch.setattr("conquest.safe_reload.clear_observation", lambda h: True)
    monkeypatch.setattr(
        delivery_route,
        "refill_remainder",
        lambda loop, send, key, deadline, *a: events.append(("refill", key, deadline)),
    )

    def send(body):
        action = body["action"]
        events.append(action)
        if action == "delivery-readiness":
            return {"qualified": True}
        if action == "delivery-service-retry":
            return retry.dispatch(rig.ui, body)
        if action in ("delivery-window", "handoff-grant"):
            return {}
        if action == "delivery-start":
            native = read_json(delivery_route.STATE)["active"]
            assert acceptance.state()["active"]["admissions"] == [native]
            assert body["uids"] == [rig.selected["uid"]]
            transferred.append(rig.selected)
            receipts[body["request_id"]] = {
                **native,
                "character": "Spiritual",
                "uids": body["uids"],
                "phase": "verified",
                "outcome": "transferred",
                "proof_digest": "proof",
                "next_action": "release_route",
                "delivered": [rig.selected],
                "remaining": [],
            }
            return {"running": False, "receipt": receipts[body["request_id"]]}
        if action == "delivery-status":
            return {"running": False, "receipt": receipts[body["request_id"]]}
        if action == "handoff-release":
            return {"released": True}
        pytest.fail(action)

    result = delivery_route.market_storage(rig.loop, send=send)
    renewed = read_json(service_visit.MarketVisit().path)
    assert events.index("delivery-service-retry") < events.index("service-close-panel")
    assert len(result) == 1 and events.count("delivery-start") == 1
    assert (
        result[0]["items"] == [rig.selected]
        and result[0]["visit_id"] == renewed["visit_id"]
    )
    assert ("refill", result[0]["request_id"], renewed["deadline"]) in events
    assert rig.windows.state()["deadline"] == renewed["deadline"]
    assert acceptance.state()["active"]["phase"] == "delivered"
    assert rig.loop.market_service_deadline is None


@pytest.mark.parametrize(
    "hold",
    [
        "acceptance_admission",
        "route_active",
        "cleanup",
        "route_receipt",
        "route_operation",
        "source_admission",
        "other_pending_admission",
        "source_transaction",
        "other_pending_transaction",
        "receiver_reservation",
        "terminal_receiver_reservation",
        "reserved_window",
        "old_completed_window",
        "other_active_window",
        "farmer_on",
        "farmer_pause",
        "global_stop",
        "owner",
        "manual",
        "mouse",
        "closing",
        "grant",
        "handoff",
        "delivery_window",
        "refill_window",
        "worker",
        "worker_admission",
        "merchant_pending",
        "merchant_input",
        "merchant_manual",
        "refill_pending",
        "wrong_process",
        "changed_item",
        "wrong_map",
        "trade",
        "request",
        "dead",
        "stale",
        "wrong_cycle",
        "wrong_run",
        "wrong_route",
        "wrong_journey",
        "deposit_pending",
        "stored_receipt",
        "unqualified_merchant",
        "visit_outcome",
        "unexpired",
        "changed_acceptance",
        "manual_stop",
        "route_terminal",
        "route_stale",
        "route_dead",
        "route_pid",
        "route_start",
        "control_change",
        "input_lock",
        "fence",
        "combat_thread",
    ],
)
def test_retry_rejects_any_admission_ownership_or_changed_evidence(
    rig, monkeypatch, hold
):
    origin = {
        "visit_id": rig.visit["visit_id"],
        "town_visit_id": rig.visit["town_visit_id"],
    }
    if hold == "acceptance_admission":
        admission(rig, rig.selected)
    elif hold in ("route_active", "cleanup", "route_receipt", "route_operation"):
        fields = {
            "route_active": {"active": {"request_id": "x"}},
            "cleanup": {"cleanup_pending": True},
            "route_receipt": {"receipts": [origin]},
            "route_operation": {"operations": [origin]},
        }
        write_json(delivery_route.STATE, fields[hold])
    elif hold in ("source_admission", "other_pending_admission"):
        with rig.source_journal.db() as db:
            db.execute(
                "INSERT INTO delivery_admissions VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    "x",
                    "Spiritual",
                    "[]",
                    "[]",
                    json.dumps(origin if hold == "source_admission" else {}),
                    "rejected" if hold == "source_admission" else "admitted",
                    None,
                    0,
                    0,
                ),
            )
    elif hold in ("source_transaction", "other_pending_transaction"):
        with rig.source_journal.db() as db:
            db.execute(
                "INSERT INTO transactions VALUES(?,?,?,?,?,?,?,?)",
                (
                    "x",
                    "Spiritual",
                    "farmer_delivery",
                    "aborted" if hold == "source_transaction" else "prepared",
                    json.dumps(origin if hold == "source_transaction" else {}),
                    None,
                    0,
                    0,
                ),
            )
    elif hold in ("receiver_reservation", "terminal_receiver_reservation"):
        with rig.ui.runtime.journal.db() as db:
            db.execute(
                "INSERT INTO delivery_reservations VALUES(?,?,?)",
                (
                    "Spiritual",
                    "x",
                    json.dumps(
                        {
                            "phase": "verified"
                            if hold == "terminal_receiver_reservation"
                            else "reserved",
                            "intent": origin,
                        }
                    ),
                ),
            )
    elif hold in ("reserved_window", "old_completed_window", "other_active_window"):
        write_json(
            rig.windows.path,
            {
                "visit_id": "other"
                if hold == "other_active_window"
                else rig.visit["visit_id"],
                "phase": "completed" if hold == "old_completed_window" else "preparing",
            },
        )
    elif hold in ("farmer_on", "farmer_pause"):
        rig.ui.app.control.snapshot = lambda: {
            "enabled": hold == "farmer_on",
            "paused": hold == "farmer_pause",
        }
    elif hold == "global_stop":
        rig.ui.coordinator.stopped = True
    elif hold == "owner":
        rig.ui.coordinator.owner = "Farmer"
    elif hold == "manual":
        rig.ui.coordinator.manual_session_blocked = lambda *a: True
    elif hold == "mouse":
        rig.ui.app.mouse_priority.active = lambda: True
    elif hold == "closing":
        rig.ui.app.closing = True
    elif hold == "grant":
        rig.ui.grant = {"request_id": "x"}
    elif hold == "input_lock":
        rig.ui.coordinator.lock = NS(acquire=lambda **kw: False)
    elif hold == "fence":
        rig.ui.grant_fence = NS(active={"request_id": "x"})
    elif hold == "combat_thread":
        rig.ui.app.thread = NS(is_alive=lambda: True)
    elif hold in ("handoff", "delivery_window", "refill_window"):
        setattr(rig.ui.runtime, hold, "x")
    elif hold == "worker":
        rig.ui.delivery_workers = {"x": NS(is_alive=lambda: True)}
    elif hold == "worker_admission":
        rig.ui.delivery_admissions = {"x"}
    elif hold in ("merchant_pending", "merchant_input", "merchant_manual"):
        rig.status[
            {
                "merchant_pending": "pending",
                "merchant_input": "input_active",
                "merchant_manual": "manual_input_fence",
            }[hold]
        ] = True
    elif hold == "refill_pending":
        rig.status["refill"]["pending"] = True
    elif hold == "wrong_process":
        rig.farmer["identity"]["creation_time_100ns"] += 1
    elif hold == "changed_item":
        rig.farmer["inventory"][-1]["plus"] += 1
    elif hold == "wrong_map":
        rig.farmer["map_id"] = 1002
    elif hold in ("trade", "request"):
        rig.farmer[hold] = {"uid": 3}
    elif hold == "dead":
        rig.farmer["hp"] = 0
    elif hold == "stale":
        rig.farmer["timestamp"] -= 10
    elif hold == "wrong_cycle":
        rig.body["cycle_id"] = "other"
    elif hold == "wrong_run":
        rig.body["run_id"] = "other"
    elif hold == "wrong_route":
        rig.ui.app.selected_route.id = "other"
    elif hold in ("wrong_journey", "deposit_pending", "stored_receipt"):
        fields = {
            "wrong_journey": {"acceptance_scope": {}},
            "deposit_pending": {"deposit_pending": rig.selected},
            "stored_receipt": {"receipts": [{"uid": rig.selected["uid"]}]},
        }
        write_json(delivery_journey.JOURNAL, {**rig.journey, **fields[hold]})
    elif hold == "unqualified_merchant":
        rig.status["qualification"]["trade"] = False
    elif hold in ("visit_outcome", "unexpired"):
        if hold == "visit_outcome":
            rig.visit["attempts"][0]["outcome"] = "retryable_before_input"
        else:
            rig.visit["deadline"] = time.time() + 30
        write_json(service_visit.MarketVisit().path, rig.visit)
    elif hold == "changed_acceptance":
        source = deepcopy(rig.farmer)

        def observe(*a):
            acceptance.update("changed", lambda row: {**row, "concurrent_change": True})
            return {"farmer": source}

        monkeypatch.setattr("conquest.merchants.delivery_bridge.dispatch", observe)
    elif hold == "manual_stop":
        write_json(retry.ROUTE_STOP, {"stopped": True})
    elif hold in (
        "route_terminal",
        "route_stale",
        "route_dead",
        "route_pid",
        "route_start",
    ):
        fields = {
            "route_terminal": {"phase": "stopped"},
            "route_stale": {"updated_at": time.time() - 10},
            "route_dead": {"pid": 456},
            "route_pid": {"pid": 456},
            "route_start": {"started_at": 1},
        }
        write_json(
            retry.ROUTE_STATUS, {**read_json(retry.ROUTE_STATUS), **fields[hold]}
        )
        if hold == "route_dead":
            rig.body["route_pid"] = 456
    elif hold == "control_change":

        def observe(*a):
            rig.ui.app.control.snapshot = lambda: {"enabled": False, "paused": True}
            return {"farmer": deepcopy(rig.farmer)}

        monkeypatch.setattr("conquest.merchants.delivery_bridge.dispatch", observe)
    with pytest.raises(ValueError):
        rig.dispatch()
    assert read_json(service_visit.MarketVisit().path) == rig.visit
    assert not acceptance.state()["active"].get("service_retry")


def test_normal_visit_cannot_renew_after_expiry(rig):
    acceptance.update("disabled", lambda row: {**row, "enabled": False})
    assert (
        retry.route_retry(
            rig.loop, rig.visit, lambda body: pytest.fail("Ordinary retry")
        )
        == rig.visit
    )
    with pytest.raises(ValueError, match="exact unadmitted"):
        rig.dispatch()
    assert read_json(service_visit.MarketVisit().path) == rig.visit


def test_failed_acceptance_delivery_preserves_carried_uid_without_bank_input(rig):
    events = []
    loop = NS(town=lambda *a, **kw: events.append(a))
    with pytest.raises(ValueError, match="exact item remains carried"):
        delivery_journey.warehouse_fallback(
            loop, rig.journey, send=lambda body: {"farmer": deepcopy(rig.farmer)}
        )
    assert events == [] and rig.farmer["inventory"][-1]["uid"] == rig.selected["uid"]


def test_delivered_acceptance_can_bank_unrelated_valuables(rig, monkeypatch):
    delivered(rig, rig.selected)
    extra = item(77, plus=2)
    bag = [extra]
    bank = []
    events = []
    write_json(delivery_journey.JOURNAL, rig.journey)

    def town(action, **fields):
        if action == "supplies":
            return {"items": deepcopy(bag)}
        if action == "warehouse-items":
            return {"items": deepcopy(bank), "capacity": 40}
        if action == "warehouse-deposit":
            assert fields["uid"] == 77
            bank.append(bag.pop())
            events.append(action)
            return {"uid": 77, "verified_in_warehouse": True}
        pytest.fail(action)

    monkeypatch.setattr(
        "conquest.meteor_banking.approach_market_warehouse",
        lambda *a: events.append("approach"),
    )
    monkeypatch.setattr(
        "conquest.banking.open_warehouse", lambda *a: events.append("open")
    )
    monkeypatch.setattr(
        "conquest.banking.close_warehouse", lambda *a: events.append("close")
    )
    delivery_journey.warehouse_fallback(
        NS(town=town, record=lambda *a, **kw: None),
        rig.journey,
        send=lambda body: {"farmer": deepcopy(rig.farmer)},
    )
    assert (
        not bag
        and bank == [extra]
        and events == ["approach", "open", "warehouse-deposit", "close"]
    )
