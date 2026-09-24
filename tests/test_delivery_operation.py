from types import SimpleNamespace as NS
import json
import threading
import pytest
from conquest.merchants import delivery_operation as operation
from conquest.merchants.journal import Journal


@pytest.mark.parametrize(
    "fault",
    [None, "other_incident", "other_reservation", "unresolved", "proof", "cleanup"],
)
def test_no_transfer_clears_only_matching_bilateral_incident(tmp_path, fault):
    journal = Journal(tmp_path / "merchant.sqlite3")
    attention = {
        "kind": "farmer_delivery",
        "request_id": "one",
        "note": "Old unresolved error",
    }
    reservation = {
        "request_id": "one",
        "phase": "no_transfer_reconciled",
        "disposition": {"proof_digest": "proof"},
    }
    receipt = {
        "character": "Spiritual",
        "phase": "aborted",
        "outcome": "no_transfer",
        "next_action": "retry_delivery",
        "cleanup_pending": [],
        "proof_digest": "proof",
    }
    if fault == "other_incident":
        attention["request_id"] = "newer"
    if fault == "other_reservation":
        reservation["request_id"] = "newer"
    if fault == "unresolved":
        receipt["phase"] = "uncertain"
    if fault == "proof":
        reservation["disposition"]["proof_digest"] = "different"
    if fault == "cleanup":
        receipt["cleanup_pending"] = ["Spiritual"]
    journal.set("Spiritual", "attention", attention)
    journal.set("Spiritual", "delivery_reservation", reservation)
    ui = NS(runtime=NS(journal=journal), delivery_errors={"one": "Old error"})
    assert operation.clear_settled_attention(ui, "one", receipt) is (fault is None)
    assert journal.get("Spiritual", "attention") == (attention if fault else None)
    assert ("one" in ui.delivery_errors) is bool(fault)


def test_readiness_checks_qualification_without_input_or_journal(monkeypatch):
    calls = []
    monkeypatch.setattr(
        operation,
        "FarmerTradeDriver",
        lambda ui: NS(require_qualified=lambda: calls.append("qualification")),
    )
    assert (
        operation.dispatch(NS(), {"action": "delivery-readiness"})["qualified"] is True
    )
    assert calls == ["qualification"]

    def absent():
        raise ValueError("No live qualification")

    monkeypatch.setattr(
        operation, "FarmerTradeDriver", lambda ui: NS(require_qualified=absent)
    )
    result = operation.dispatch(NS(), {"action": "delivery-readiness"})
    assert result["qualified"] is False
    assert "farmer_trade_controls_unqualified" in result["blockers"]


def test_town_source_reader_requires_farmer_identity(monkeypatch):
    from conquest.merchants import delivery_bridge

    monkeypatch.setattr(
        delivery_bridge,
        "MerchantMemory",
        lambda observer: pytest.fail("Wrong character must not be read"),
    )
    ui = NS(app=NS(observer=NS(character="Dutch")))
    with pytest.raises(ValueError, match="verified farmer"):
        delivery_bridge.dispatch(ui, {"action": "delivery-source"})


def test_unknown_route_submission_blocks_reload_before_source_receipt(monkeypatch):
    from conquest.merchants import delivery_route
    from conquest.discord_notify import write_json

    write_json(delivery_route.STATE, {"active": {"request_id": "unknown"}})
    with pytest.raises(ValueError, match="pending farmer delivery"):
        operation.guard_reload()


def test_disabled_rollout_cannot_construct_native_input_driver(tmp_path, monkeypatch):
    monkeypatch.setattr(operation, "JOURNAL", tmp_path / "source.sqlite3")
    monkeypatch.setattr(operation, "read_json", lambda path: {"enabled": False})
    monkeypatch.setattr(
        operation,
        "FarmerTradeDriver",
        lambda *a: pytest.fail("Native input must remain gated"),
    )
    ui = NS()
    selected = {
        "uid": 10,
        "type_id": 720027,
        "plus": 0,
        "gem1": 0,
        "gem2": 0,
        "quantity": 1,
        "bound": False,
    }
    with pytest.raises(ValueError, match="Merchant transfers are Off for Parasite"):
        operation.dispatch(
            ui,
            {
                "action": "delivery-start",
                "request_id": "one",
                "character": "Dutch",
                "uids": [10],
                "items": [selected],
            },
        )
    status = operation.dispatch(ui, {"action": "delivery-status", "request_id": "one"})
    assert (
        status["receipt"]["outcome"] == "retryable_before_input"
        and not status["running"]
    )
    assert (
        status["receipt"]["items"] == [selected]
        and status["receipt"]["next_action"] == "release_route"
    )


def test_inflight_admission_is_running_until_source_transaction_or_rejection(
    tmp_path, monkeypatch
):
    path = tmp_path / "source.sqlite3"
    monkeypatch.setattr(operation, "JOURNAL", path)
    journal = Journal(path)
    selected = {
        "uid": 10,
        "type_id": 720027,
        "plus": 0,
        "gem1": 0,
        "gem2": 0,
        "quantity": 1,
        "bound": False,
    }
    journal.admit_delivery("one", "Dutch", [10], {"operation_id": "one"}, [selected])
    ui = NS(delivery_workers={}, delivery_errors={}, delivery_admissions={"one"})
    result = operation.dispatch(ui, {"action": "delivery-status", "request_id": "one"})
    assert result["running"] is True
    assert result["receipt"]["outcome"] == "retryable_before_input"
    ui.delivery_admissions.clear()
    assert (
        operation.dispatch(ui, {"action": "delivery-status", "request_id": "one"})[
            "running"
        ]
        is False
    )


def test_delivery_admission_matches_core_request_to_rich_live_item(tmp_path):
    journal = Journal(tmp_path / "source.sqlite3")
    selected = {
        "uid": 10,
        "type_id": 114643,
        "plus": 1,
        "gem1": 0,
        "gem2": 0,
        "quantity": 1,
        "bound": False,
    }
    live = {**selected, "name": "CloudCap", "slot": 17, "price": None, "vtable": 12345}
    origin = {"operation_id": "one"}
    journal.admit_delivery("one", "Dutch", [10], origin, [selected])
    journal.update_delivery_admission("one", "admitted", items=[live])
    admission = journal.delivery_admission("one")
    assert json.loads(admission["items_json"]) == [selected]
    assert journal.begin(
        "one", "Dutch", "farmer_delivery", {"items": [live]}, admission=True
    )
    admission = journal.delivery_admission("one")
    assert admission["phase"] == "transaction_started"
    with journal.db() as db:
        before = json.loads(
            db.execute(
                "SELECT before_json FROM transactions WHERE id=?", ("one",)
            ).fetchone()[0]
        )
    assert before["items"] == [live]


@pytest.mark.parametrize(
    ("field", "changed"),
    [
        ("uid", 11),
        ("type_id", 114644),
        ("plus", 2),
        ("gem1", 1),
        ("gem2", 2),
        ("quantity", 2),
        ("bound", True),
    ],
)
def test_delivery_admission_rejects_changed_ownership_attribute(
    tmp_path, field, changed
):
    journal = Journal(tmp_path / f"{field}.sqlite3")
    selected = {
        "uid": 10,
        "type_id": 114643,
        "plus": 1,
        "gem1": 0,
        "gem2": 0,
        "quantity": 1,
        "bound": False,
    }
    journal.admit_delivery("one", "Dutch", [10], {"operation_id": "one"}, [selected])
    with pytest.raises(ValueError, match="fingerprints changed"):
        journal.update_delivery_admission(
            "one",
            "admitted",
            items=[{**selected, field: changed, "name": "CloudCap", "slot": 17}],
        )


def test_restart_only_reconciles_and_repeated_request_reuses_live_worker(
    tmp_path, monkeypatch
):
    from conquest.merchants import farmer_preferences

    monkeypatch.setattr(farmer_preferences, "PATH", tmp_path / "preferences.json")
    farmer_preferences.set_enabled("Parasite", False)
    path = tmp_path / "source.sqlite3"
    monkeypatch.setattr(operation, "JOURNAL", path)
    journal = Journal(path)
    journal.begin("one", "Dutch", "farmer_delivery", {"items": [{"uid": 10}]})
    monkeypatch.setattr(
        operation,
        "FarmerTradeDriver",
        lambda *a: pytest.fail("Recovery must not construct an input driver"),
    )
    entered = threading.Event()
    release = threading.Event()
    calls = []

    class Recovery:
        def __init__(self, journal, driver, **kwargs):
            pass

        def recover(self, key):
            calls.append(key)
            entered.set()
            assert release.wait(3)

    monkeypatch.setattr(operation, "DeliveryTransaction", Recovery)
    ui = NS(runtime=NS(journal=Journal(tmp_path / "merchant.sqlite3")))
    command = {"action": "delivery-reconcile", "request_id": "one"}
    try:
        operation.dispatch(ui, command)
        assert entered.wait(1)
        worker = ui.delivery_workers["one"]
        assert operation.dispatch(ui, command)["running"]
        assert ui.delivery_workers["one"] is worker and calls == ["one"]
        with pytest.raises(ValueError, match="reused"):
            operation.dispatch(
                ui,
                {
                    "action": "delivery-start",
                    "request_id": "one",
                    "character": "Dutch",
                    "uids": [11],
                },
            )
    finally:
        release.set()
        ui.delivery_workers["one"].join(2)
    assert not ui.delivery_workers["one"].is_alive()


def test_reload_waits_for_submitted_delivery_reconciliation(tmp_path, monkeypatch):
    path = tmp_path / "source.sqlite3"
    monkeypatch.setattr(operation, "JOURNAL", path)
    operation.guard_reload()
    journal = Journal(path)
    journal.begin("one", "Dutch", "farmer_delivery", {"items": [{"uid": 10}]})
    for phase in ("submitted", "uncertain"):
        journal.transition("one", phase)
        with pytest.raises(ValueError, match="before reloading"):
            operation.guard_reload()
    journal.transition("one", "verified")
    operation.guard_reload()


def test_revoked_worker_before_thread_entry_records_no_input_terminal(tmp_path):
    from conquest.capture import CaptureUnavailable
    from conquest.merchants.grant_fence import GrantFence

    journal = Journal(tmp_path / "source.sqlite3")
    journal.begin(
        "one",
        "Dutch",
        "farmer_delivery",
        {
            "items": [
                {
                    "uid": 10,
                    "type_id": 720027,
                    "plus": 0,
                    "gem1": 0,
                    "gem2": 0,
                    "quantity": 1,
                    "bound": False,
                }
            ]
        },
    )
    fence = GrantFence()
    token = fence.capture()
    fence.invalidate()
    with pytest.raises(CaptureUnavailable):
        with fence.bind_worker(token):
            pass
    receipt = operation.reject_worker_admission(journal, "one")
    assert (
        receipt["phase"] == "aborted" and receipt["outcome"] == "retryable_before_input"
    )
    assert receipt["evidence_outcome"] == "not_started"
    assert (
        receipt["next_action"] == "release_route"
        and not receipt["action_trace_initialized"]
    )


def test_verified_receipt_holds_route_until_trade_cleanup_is_observed(tmp_path):
    journal = Journal(tmp_path / "source.sqlite3")
    selected = {
        "uid": 10,
        "type_id": 720027,
        "plus": 0,
        "gem1": 0,
        "gem2": 0,
        "quantity": 1,
        "bound": False,
    }
    journal.begin("one", "Dutch", "farmer_delivery", {"items": [selected]})
    journal.transition(
        "one", "verified", {"outcome": "delivered", "cleanup_pending": ["Dutch"]}
    )
    receipt = operation.status(journal, "one")
    assert (
        receipt["items"] == [selected]
        and receipt["next_action"] == "cleanup_trade_modal"
    )
    journal.step("one", "cleanup", "observed", {"proof_digest": "proof"}, terminal=True)
    assert (
        operation.status(journal, "one")["next_action"] == "finalize_receiver_receipt"
    )
    journal.step(
        "one", "receiver_receipt", "observed", {"phase": "verified"}, terminal=True
    )
    assert operation.status(journal, "one")["next_action"] == "release_route"


@pytest.mark.parametrize("action", ["delivery-start", "delivery-test"])
def test_mismatched_work_window_rejects_before_native_driver_or_intent(
    tmp_path, monkeypatch, action
):
    path = tmp_path / "source.sqlite3"
    monkeypatch.setattr(operation, "JOURNAL", path)
    monkeypatch.setattr(
        operation,
        "FarmerTradeDriver",
        lambda *a: pytest.fail("Mismatched window must not construct input"),
    )
    ui = NS(runtime=NS(delivery_window="reserved-window"))
    with pytest.raises(ValueError, match="match its reserved work window"):
        operation.dispatch(
            ui,
            {
                "action": action,
                "request_id": "other-request",
                "character": "Spiritual",
                "uids": [10],
            },
        )
    receipt = operation.status(Journal(path), "other-request")
    assert (
        receipt["outcome"] == "retryable_before_input"
        and receipt["next_action"] == "release_route"
    )
    assert not ui.delivery_workers
