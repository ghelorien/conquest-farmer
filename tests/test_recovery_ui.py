import threading
import time
from contextlib import contextmanager
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from conquest.desktop_app import DesktopApp
from conquest.merchants.journal import Journal
from conquest.merchants.ui import UnifiedUI


class _Var:
    def __init__(self, value=""):
        self.value = value

    def set(self, value):
        self.value = value


class _Control:
    def __init__(self, revision=1, enabled=False):
        self.revision = revision
        self.enabled = enabled
        self.updates = []

    def snapshot(self):
        return {"revision": self.revision, "enabled": self.enabled}

    def update(self, body):
        self.updates.append(body)
        self.enabled = body.get("enabled", self.enabled)
        return self.snapshot()


def _item(uid=10):
    return {"uid": uid, "type_id": 130403, "plus": 1, "gem1": 0,
            "gem2": 0, "quantity": 1, "bound": False, "slot": 0}


def _snapshot(name, uid, item):
    return {"character": name, "character_uid": uid, "identity": {"pid": uid},
            "server": "America", "timestamp": time.time(), "map_id": 1036,
            "hp": 100, "silver": 100, "capacity": 40,
            "inventory": [item], "booth": [], "request": None, "trade": None}


def test_farmer_recheck_dispatch_uses_fresh_worker_health_shape(monkeypatch):
    """The profile bridge must read the worker's embedded-controls health payload."""
    now = time.time()
    health = {
        "target": {"pid": 42, "hwnd": 99},
        "embedded_controls": {
            "observations_available": True,
            "observed_at": now,
            "external_execution": False,
            "life": {"character": "Parasite", "current_hp": 100, "max_hp": 100},
        },
    }
    incident = {"id": "market-route:one", "digest": "a" * 64,
                "kind": "market-route-departure", "phase": "prepared",
                "items": []}
    app = DesktopApp.__new__(DesktopApp)
    app.last = {"worker_info_path": "worker-info.json"}
    app._farmer_recovery_incidents = lambda: [incident]
    monkeypatch.setattr("conquest.worker.request", lambda path, operation: health)

    result = app.dispatch({"action": "recovery-recheck", "incident_id": incident["id"]})

    assert result["incident_digest"] == incident["digest"]
    assert result["rechecked"]["life"]["character"] == "Parasite"
    assert result["rechecked"]["observations_available"] is True
    assert result["rechecked"]["target"] == health["target"]


def test_meteor_recheck_dispatch_uses_memory_worker_bag_and_warehouse(monkeypatch):
    from conquest import meteor_banking
    incident={"id":"meteor-consolidation:one","digest":"m" * 64,
              "kind":"meteor-consolidation","phase":"travelling","items":[]}
    evidence={"life":{"map_id":1011},"supplies":{"items":[{"uid":1}]},
              "warehouse":{"items":[{"uid":2}]}}
    app=DesktopApp.__new__(DesktopApp)
    app.last={"worker_info_path":"worker-info.json"}
    app._farmer_recovery_incidents=lambda:[incident]
    recheck=Mock(return_value=evidence)
    monkeypatch.setattr(meteor_banking,"recheck_worker",recheck)

    result=app.dispatch({"action":"recovery-recheck","incident_id":incident["id"]})

    recheck.assert_called_once_with("worker-info.json")
    assert result["rechecked"]["supplies"]==evidence["supplies"]
    assert result["rechecked"]["warehouse"]==evidence["warehouse"]


def test_meteor_override_uses_the_same_bag_and_warehouse_recheck(monkeypatch):
    from conquest import meteor_banking
    incident={"id":"meteor-consolidation:one","digest":"m" * 64,
              "kind":"meteor-consolidation","phase":"travelling","items":[]}
    evidence={"life":{"map_id":1011},"supplies":{"items":[{"uid":1}]},
              "warehouse":{"items":[{"uid":2}]}}
    app=DesktopApp.__new__(DesktopApp)
    app.last={"worker_info_path":"worker-info.json"}
    recheck=Mock(return_value=evidence);override=Mock(return_value={"phase":"operator_overridden"})
    monkeypatch.setattr(meteor_banking,"recheck_worker",recheck)
    monkeypatch.setattr(meteor_banking,"operator_override",override)
    @contextmanager
    def unlocked():
        yield True
    monkeypatch.setattr("conquest.route_controller.controller_guard",unlocked)

    assert app._apply_farmer_override(incident,incident["digest"])["phase"]=="operator_overridden"
    recheck.assert_called_once_with("worker-info.json")
    assert override.call_args.kwargs["fresh_evidence"]==evidence
    assert override.call_args.kwargs["incident_digest"]==incident["digest"]


def test_merchant_listing_recheck_dispatches_against_merchant_journal(tmp_path):
    """A Dutch listing is a merchant transaction, never a farmer-delivery row."""
    journal = Journal(tmp_path / "merchant.sqlite3")
    item = _item()
    journal.begin("listing-1", "Dutch", "listing", {"items": [item], "booth": []})
    snapshot = {"character": "Dutch", "timestamp": time.time(), "hp": 100,
                "inventory": [item], "booth": []}
    controller = NS(driver=NS(read=Mock(return_value=snapshot)),
                    reconcile=Mock(return_value={"ready": True}))
    observer = NS(lock=threading.RLock())
    ui = UnifiedUI.__new__(UnifiedUI)
    ui.runtime = NS(journal=journal, controllers={"Dutch": controller},
                    observers={"Dutch": observer})
    ui.coordinator = NS(owner=None)

    result = ui.dispatch({"action": "recovery-recheck", "character": "Dutch",
                          "incident_id": "listing:listing-1"})

    assert result["incident"]["kind"] == "listing"
    assert result["incident"]["request_id"] == "listing-1"
    assert result["incident"]["rechecked"]["snapshot"] == snapshot
    controller.driver.read.assert_called_once_with()
    controller.reconcile.assert_called_once_with(snapshot)
    assert journal.pending("Dutch")[0]["kind"] == "listing"


def test_farmer_override_rejects_stale_confirmation_before_mutation(monkeypatch):
    incident = {"id": "overflow:one", "digest": "b" * 64,
                "kind": "overflow", "phase": "market", "items": []}
    app = DesktopApp.__new__(DesktopApp)
    app._farmer_recovery_incidents = lambda: [incident]
    app._apply_farmer_override = Mock(side_effect=AssertionError("must not mutate"))

    with pytest.raises(ValueError, match="evidence changed"):
        app.dispatch({"action": "recovery-override", "incident_id": incident["id"],
                      "operator_confirmed": True, "incident_digest": "c" * 64,
                      "confirmation_reference": "c" * 64})
    app._apply_farmer_override.assert_not_called()


def test_offline_farmer_delivery_override_closes_both_journals(monkeypatch, tmp_path):
    """A disconnected participant is evidence for a journal disposition, not a transfer."""
    from conquest.merchants import delivery_operation
    from conquest.merchants.delivery_reservation import reserve

    source_path = tmp_path / "source.sqlite3"
    receiver_path = tmp_path / "receiver.sqlite3"
    monkeypatch.setattr(delivery_operation, "JOURNAL", source_path)
    source = Journal(source_path)
    receiver = Journal(receiver_path)
    item = _item()
    farmer = _snapshot("Parasite", 1, item)
    # The receiver must not already own the offered UID; the reserved
    # incident records the farmer's item and the receiver's separate stock.
    merchant = _snapshot("Dutch", 2, _item(20))
    intent = {"operation_id": "delivery-1", "farmer_profile_id": "Parasite",
              "farmer": farmer, "merchant": merchant, "items": [item]}
    source.begin("delivery-1", "Dutch", "farmer_delivery", intent)
    source.step("delivery-1", "action_trace", "initialized")
    reserve(receiver, "delivery-1", farmer, merchant, [item],
            origin={"operation_id": "delivery-1", "farmer_profile_id": "Parasite"})

    ui = UnifiedUI.__new__(UnifiedUI)
    ui.app = NS(observer=None)
    ui.runtime = NS(journal=receiver, observers={})
    ui.coordinator = NS(owner=None, lock=threading.RLock())
    ui.delivery_workers = {}
    ui.delivery_admissions = set()
    ui.delivery_errors = {}
    digest = source.original_evidence_digest("delivery-1")

    result = ui.dispatch({"action": "recovery-override", "character": "Dutch",
                          "incident_id": "farmer-delivery:delivery-1",
                          "operator_confirmed": True, "incident_digest": digest,
                          "confirmation_reference": digest})

    assert result["receipt"]["phase"] == "operator_overridden"
    assert result["reservation_phase"] == "operator_overridden"
    assert Journal(source_path).pending("Dutch") == []


def test_manual_stop_revision_and_global_stop_prevent_resume():
    app = DesktopApp.__new__(DesktopApp)
    app.control = _Control(revision=8)
    app._recovery_epoch = 2
    app.recovery_text = _Var()
    app.unified = NS(coordinator=NS(stopped=True))
    app.mouse_priority = NS(active=lambda: False)
    app.root = NS(after=Mock())
    app._farmer_fresh_recheck = Mock(return_value={"rechecked": {}})
    app.update_control = Mock()

    assert app._farmer_resume_if_fresh((8, 1)) is False  # a newer manual Stop wins
    assert app._farmer_resume_if_fresh((8, 2)) is False  # Global Stop also wins
    app.update_control.assert_not_called()


def test_mouse_priority_defers_resume_after_hold_is_cleared():
    active = [{"id": "overflow:one", "digest": "d" * 64,
               "kind": "overflow", "phase": "market", "items": []}]
    app = DesktopApp.__new__(DesktopApp)
    app.control = _Control(revision=3)
    app.mouse_priority = NS(active=lambda: True)
    app.unified = None
    app.recovery_text = _Var()
    app.root = NS(after=Mock())
    app.last = {}
    qualified = {"observations_available": True, "observed_at": time.time(),
                 "external_execution": False,
                 "life": {"character": "Parasite", "current_hp": 100}}
    app._farmer_recovery_incidents = lambda: active
    app._farmer_fresh_recheck = lambda incident: {**incident, "rechecked": qualified}
    applied = []
    app._apply_farmer_override = lambda incident, digest: applied.append((incident["id"], digest))
    app.record = Mock()
    app.refresh_farmer_recovery = lambda: active.clear()

    result = app.dispatch({"action": "recovery-override", "incident_id": "overflow:one",
                           "operator_confirmed": True, "incident_digest": "d" * 64,
                           "confirmation_reference": "d" * 64})

    assert result is None  # the override is a disposition; resume is deferred
    assert applied == [("overflow:one", "d" * 64)]
    assert active == []  # the selected hold was cleared despite mouse activity
    assert app.control.updates == []  # no gameplay input while another mouse owns input
    app.root.after.assert_called_once()


def test_global_stop_stops_runtime_and_farmer_without_touching_other_holds():
    cancel = threading.Event()
    runtime = NS(global_stop=Mock())
    app = NS(stop=Mock())
    ui = UnifiedUI.__new__(UnifiedUI)
    ui.grant = {"request_id": "active"}
    ui.calibration_cancel = {"Dutch": cancel}
    ui.runtime = runtime
    ui.app = app

    ui.global_stop()

    assert ui.grant is None
    assert cancel.is_set()
    runtime.global_stop.assert_called_once_with()
    app.stop.assert_called_once_with()
