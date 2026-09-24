from copy import deepcopy
from types import SimpleNamespace as NS
import json
import sqlite3
import threading
import time

import pytest

from conquest import merchant_loop_acceptance as acceptance

REAL_PROVENANCE = acceptance.hunt_provenance


def item(uid, kind=410008, plus=1, **fields):
    return {
        "uid": uid,
        "type_id": kind,
        "plus": plus,
        "gem1": 0,
        "gem2": 0,
        "quantity": 1,
        "bound": False,
        "slot": 0,
        **fields,
    }


def snapshot(name, uid, items, *, map_id=1000):
    return {
        "character": name,
        "character_uid": uid,
        "server": "America",
        "identity": {
            "pid": uid,
            "creation_time_100ns": uid * 100,
            "path": f"C:/game/{name}.exe",
        },
        "timestamp": time.time(),
        "hp": 100,
        "map_id": map_id,
        "inventory": items,
        "silver": 1000,
        "capacity": 40,
        "booth": [],
        "booth_open": True,
        "own_booth_uid": uid + 100,
        "trade": None,
        "request": None,
    }


@pytest.fixture
def rig(tmp_path, monkeypatch):
    from conquest.town_visit import TownVisit

    farmer = snapshot("Parasite", 1, [item(10), item(11, 1050002, 0, quantity=5000)])
    merchant = snapshot("Spiritual", 2, [], map_id=1036)
    status = {
        "ready": True,
        "enabled": True,
        "profile_id": "merchant-profile",
        "snapshot": merchant,
        "pending": [],
        "manual_input_fence": False,
        "qualification": {
            capability: True for capability in acceptance.MERCHANT_CAPABILITIES
        },
        "refill": {"enabled": True, "pending": False},
    }
    ui = NS(
        app=NS(
            control=NS(snapshot=lambda: {"enabled": False, "paused": False}),
            observer=NS(
                adapter=NS(identity=farmer["identity"], expected_sha256="build")
            ),
            selected_route=NS(id="bandit", map_id=1000, restock_map_id=1002),
            mouse_priority=NS(active=lambda: False),
        ),
        coordinator=NS(
            stopped=False, owner=None, manual_session_blocked=lambda role: False
        ),
        safe_to_yield=lambda: True,
        runtime=NS(status=lambda: {"Spiritual": deepcopy(status)}),
    )
    qualification = tmp_path / "qualification.json"
    qualification.write_text("qualified evidence")
    merchant_qualification = tmp_path / "merchant-qualification.json"
    merchant_qualification.write_text(
        json.dumps(
            {
                "client_sha256": "merchant-build",
                "character": "Spiritual",
                "server": "America",
                "capabilities": dict(status["qualification"]),
                "evidence": {"verified": True},
            }
        )
    )
    merchant_driver = NS(
        observer=NS(
            adapter=NS(identity=merchant["identity"], expected_sha256="merchant-build")
        ),
        qualification=merchant_qualification,
        require_qualified=lambda capability: True,
    )
    ui.runtime.controllers = {"Spiritual": NS(driver=merchant_driver)}
    monkeypatch.setattr(
        "conquest.merchants.farmer_qualification.qualification_path",
        lambda *a, **kw: qualification,
    )
    monkeypatch.setattr(
        "conquest.merchants.farmer_trade.FarmerTradeDriver",
        lambda ui: NS(require_qualified=lambda: True),
    )
    monkeypatch.setattr(
        "conquest.merchants.delivery_operation.guard_reload", lambda: None
    )
    monkeypatch.setattr("conquest.banking.policy", lambda: {"enabled": True})
    from conquest.discord_notify import write_json
    from conquest.meteor_banking import POLICY

    write_json(
        POLICY,
        {
            "origins": {
                "1002": {
                    "outbound": {
                        "verified": True,
                        "source_map": 1002,
                        "destination_map": 1036,
                    },
                    "return": {
                        "verified": True,
                        "source_map": 1036,
                        "destination_map": 1002,
                    },
                }
            }
        },
    )
    monkeypatch.setattr(
        "conquest.merchants.farmer_preferences.permits_new_delivery", lambda *a: None
    )
    monkeypatch.setattr(
        "conquest.merchants.delivery_bridge.dispatch",
        lambda *a: {"farmer": deepcopy(farmer)},
    )
    visits = TownVisit(
        tmp_path / "town.json", profile="Parasite", probe=lambda: {"available": False}
    )
    monkeypatch.setattr("conquest.town_visit.TownVisit", lambda: visits)
    health = {
        "target": farmer["identity"],
        "embedded_controls": {
            "observed_at": time.time(),
            "control": {"enabled": True, "paused": False},
            "manual_mouse": False,
            "manual_input_fence": False,
            "life": {"map_id": 1000, "current_hp": 100, "dead_candidate": False},
        },
    }
    loop = NS(
        route=ui.app.selected_route,
        identity=farmer["identity"],
        town_visit=visits,
        record=lambda *a, **kw: None,
    )
    body = {
        "action": "farmer-loop-acceptance",
        "enabled": True,
        "farmer_profile_id": "Parasite",
        "request_id": "three-cycles",
        "cycles": 3,
    }
    monkeypatch.setattr(
        acceptance,
        "hunt_provenance",
        lambda row, value: {
            "pickup": {"inventory_uid": value["uid"]},
            "verified_kill": {"count": 1},
        },
    )

    def fresh():
        farmer["timestamp"] = merchant["timestamp"] = time.time()
        health["embedded_controls"]["observed_at"] = time.time()

    def observe():
        fresh()
        return acceptance.observe_hunting(
            loop, health, send=lambda body: {"farmer": deepcopy(farmer)}
        )

    def trigger(uid=20, kind=410008, plus=1):
        selected = item(uid, kind, plus)
        farmer["inventory"].append(selected)
        assert observe()
        return selected

    def town():
        visit = visits.begin("merchant_acceptance", hunt_map_id=1000, route_id="bandit")
        acceptance.town_started(loop, visit, deepcopy(farmer))
        return visit

    return NS(
        ui=ui,
        farmer=farmer,
        merchant=merchant,
        status=status,
        qualification=qualification,
        merchant_qualification=merchant_qualification,
        merchant_driver=merchant_driver,
        loop=loop,
        health=health,
        body=body,
        trigger=trigger,
        town=town,
        observe=observe,
        fresh=fresh,
    )


def enable(rig):
    return acceptance.configure(rig.ui, rig.body)


def admission(rig, selected, key="trade"):
    cycle = acceptance.state()["active"]
    value = {
        "request_id": key,
        "merchant": "Spiritual",
        "merchant_identity": rig.merchant["identity"],
        "merchant_uid": rig.merchant["character_uid"],
        "items": [selected],
        "started_at": time.time(),
        "visit_id": "market",
        "town_visit_id": cycle["town_visit_id"],
        "farmer_profile_id": "Parasite",
    }
    acceptance.admitted(value)
    return value


def delivered(rig, selected, key="trade"):
    admitted = admission(rig, selected, key)
    receipt = {
        **admitted,
        "outcome": "transferred",
        "proof_digest": "proof",
        "next_action": "release_route",
        "verified_at": time.time(),
    }
    acceptance.settled(receipt)
    rig.farmer["inventory"] = [
        i for i in rig.farmer["inventory"] if i["uid"] != selected["uid"]
    ]
    rig.merchant["inventory"].append(deepcopy(selected))
    rig.status["refill"] = {
        "enabled": True,
        "pending": False,
        "status": "completed",
        "operation_id": key,
        "source_delivery_operation_id": key,
        "last_completed_check_at": time.time(),
    }
    return receipt


def test_arm_is_local_idempotent_and_never_changes_control_or_global_policy(rig):
    first = enable(rig)
    assert first["baseline_uids"] == [10, 11] and first["target_cycles"] == 3
    assert first["enabled"] and first["rollout_promoted"] is False
    rig.farmer["inventory"].append(item(12))
    assert enable(rig) == first
    assert rig.ui.app.control.snapshot() == {"enabled": False, "paused": False}
    with sqlite3.connect(acceptance.STATE) as db:
        assert db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1


@pytest.mark.parametrize(
    "hold", ["on", "pause", "stop", "owner", "manual", "mouse", "worker"]
)
def test_arm_requires_no_manual_or_running_control_hold(rig, hold):
    if hold in ("on", "pause"):
        rig.ui.app.control.snapshot = lambda: {
            "enabled": hold == "on",
            "paused": hold == "pause",
        }
    elif hold == "stop":
        rig.ui.coordinator.stopped = True
    elif hold == "owner":
        rig.ui.coordinator.owner = "Spiritual"
    elif hold == "manual":
        rig.ui.coordinator.manual_session_blocked = lambda role: True
    elif hold == "mouse":
        rig.ui.app.mouse_priority.active = lambda: True
    else:
        rig.ui.safe_to_yield = lambda: False
    with pytest.raises(ValueError, match="Farming Off"):
        enable(rig)
    assert not acceptance.state()["enabled"]


@pytest.mark.parametrize(
    "change",
    [
        "enabled",
        "refill",
        "booth",
        "manual",
        "pending",
        "dead",
        "map",
        "stale",
        "capacity",
    ],
)
def test_arm_requires_fresh_ready_exact_merchant_and_refill(rig, change):
    if change == "enabled":
        rig.status[change] = False
    elif change == "refill":
        rig.status["refill"]["enabled"] = False
    elif change == "booth":
        rig.status["qualification"]["booth_input"] = False
    elif change == "manual":
        rig.status["manual_input_fence"] = True
    elif change == "pending":
        rig.status["pending"] = ["transaction"]
    elif change == "dead":
        rig.merchant["hp"] = 0
    elif change == "map":
        rig.merchant["map_id"] = 1002
    elif change == "capacity":
        rig.merchant["capacity"] = 0
    else:
        rig.merchant["timestamp"] -= 6
    with pytest.raises(ValueError, match="exact ready merchant"):
        enable(rig)


@pytest.mark.parametrize("capability", acceptance.MERCHANT_CAPABILITIES)
@pytest.mark.parametrize("value", [False, None, "true"])
def test_arm_requires_each_exact_trade_and_refill_capability(rig, capability, value):
    rig.status["qualification"][capability] = value
    with pytest.raises(ValueError, match="exact ready merchant"):
        enable(rig)
    assert acceptance.state()["enabled"] is False


def test_arm_and_runtime_ignore_only_unrelated_recovery_qualification(rig):
    rig.status["ready"] = False
    for capability in ("login", "market_return", "booth_setup", "booth_panel"):
        rig.status["qualification"][capability] = False
    checked = []
    rig.merchant_driver.require_qualified = lambda capability: checked.append(
        capability
    )
    armed = enable(rig)
    assert checked == list(acceptance.MERCHANT_CAPABILITIES)
    assert (
        armed["merchants"]["Spiritual"]["qualification"]["client_sha256"]
        == "merchant-build"
    )
    rig.trigger()
    rig.town()
    assert acceptance.merchant_allowed("Spiritual", rig.status, rig.merchant)
    assert rig.status["ready"] is False  # No broad/global readiness promotion.


@pytest.mark.parametrize("capability", acceptance.MERCHANT_CAPABILITIES)
def test_arm_rechecks_driver_qualification_instead_of_trusting_status(rig, capability):
    def require(wanted):
        if wanted == capability:
            raise ValueError("live capability missing")

    rig.merchant_driver.require_qualified = require
    with pytest.raises(ValueError, match="live capability missing"):
        enable(rig)
    assert acceptance.state()["enabled"] is False


@pytest.mark.parametrize(
    "changes",
    [
        {"client_sha256": "wrong-build"},
        {"character": "Dutch"},
        {"server": "other"},
        {"evidence": {}},
        {"capabilities": {"booth_input": True}},
    ],
)
def test_arm_pins_only_the_qualification_bytes_rechecked_against_live_build(
    rig, changes
):
    evidence = json.loads(rig.merchant_qualification.read_text())
    rig.merchant_qualification.write_text(json.dumps({**evidence, **changes}))
    with pytest.raises(ValueError, match="qualification changed"):
        enable(rig)
    assert acceptance.state()["enabled"] is False


@pytest.mark.parametrize(
    "change",
    [
        "process",
        "build",
        "uid",
        "booth_uid",
        "closed_booth",
        "server",
        "character",
        "input",
        "attention",
    ],
)
def test_arm_rejects_unbound_merchant_identity_build_booth_and_holds(rig, change):
    if change == "process":
        rig.merchant_driver.observer.adapter.identity = {
            **rig.merchant["identity"],
            "pid": 99,
        }
    elif change == "build":
        rig.merchant_driver.observer.adapter.expected_sha256 = None
    elif change == "uid":
        rig.merchant["character_uid"] = None
    elif change == "booth_uid":
        rig.merchant["own_booth_uid"] = 0
    elif change == "closed_booth":
        rig.merchant["booth_open"] = False
    elif change == "server":
        rig.merchant["server"] = "other"
    elif change == "character":
        rig.merchant["character"] = "Dutch"
    elif change == "input":
        rig.status["input_active"] = True
    else:
        rig.status["needs_attention"] = {"incident": "unresolved"}
    with pytest.raises(ValueError):
        enable(rig)
    assert acceptance.state()["enabled"] is False


@pytest.mark.parametrize("capability", acceptance.MERCHANT_CAPABILITIES)
def test_runtime_rechecks_every_required_capability(rig, capability):
    enable(rig)
    rig.trigger()
    rig.town()
    rig.status["qualification"][capability] = False
    assert not acceptance.merchant_allowed("Spiritual", rig.status, rig.merchant)


@pytest.mark.parametrize(
    "change", ["qualification", "booth", "trade", "request", "stale", "full", "dead"]
)
def test_runtime_rechecks_pinned_qualification_and_live_trade_conditions(rig, change):
    enable(rig)
    rig.trigger()
    rig.town()
    if change == "qualification":
        rig.merchant_qualification.write_text("changed build or qualification evidence")
    elif change == "booth":
        rig.merchant["own_booth_uid"] += 1
    elif change == "trade":
        rig.merchant["trade"] = {"participant": "someone"}
    elif change == "request":
        rig.merchant["request"] = {"participant": "someone"}
    elif change == "stale":
        rig.merchant["timestamp"] -= 6
    elif change == "full":
        rig.merchant["capacity"] = 0
    else:
        rig.merchant["hp"] = 0
    assert not acceptance.merchant_allowed("Spiritual", rig.status, rig.merchant)


def test_unqualified_farmer_cannot_arm(rig, monkeypatch):
    monkeypatch.setattr(
        "conquest.merchants.farmer_trade.FarmerTradeDriver",
        lambda ui: NS(
            require_qualified=lambda: (_ for _ in ()).throw(ValueError("unqualified"))
        ),
    )
    with pytest.raises(ValueError, match="unqualified"):
        enable(rig)


@pytest.mark.parametrize("capacity", [None, True, -1, 41, "40"])
def test_arm_rejects_malformed_merchant_capacity(rig, capacity):
    rig.merchant["capacity"] = capacity
    with pytest.raises(ValueError, match="capacity"):
        enable(rig)
    assert acceptance.state()["enabled"] is False


@pytest.mark.parametrize(
    "fields",
    [{"farmer_profile_id": "other"}, {"cycles": 2}, {"cycles": True}, {"extra": True}],
)
def test_control_schema_and_profile_are_exact(rig, fields):
    with pytest.raises(ValueError):
        acceptance.configure(rig.ui, {**rig.body, **fields})


@pytest.mark.parametrize(
    "kind,plus,bound",
    [
        (1088001, 0, False),
        (2000031, 0, False),
        (1050002, 0, False),
        (1000020, 0, False),
        (410008, 0, False),
        (410008, 1, True),
        # Urgent banking owns +2 gear before an optional acceptance return.
        (410008, 2, False),
    ],
)
def test_existing_stock_consumables_loose_meteors_storage_only_never_trigger(
    rig, kind, plus, bound
):
    enable(rig)
    rig.farmer["inventory"].append(item(21, kind, plus, bound=bound))
    assert not rig.observe() and acceptance.state()["active"] is None


@pytest.mark.parametrize("kind,plus", [(410008, 1), (410009, 0), (720027, 0)])
def test_new_native_deliverable_triggers_exact_one_return(rig, kind, plus):
    enable(rig)
    selected = rig.trigger(kind=kind, plus=plus)
    first = acceptance.state()["active"]
    assert (
        first["item"] == selected
        and first["provenance"]["pickup"]["inventory_uid"] == selected["uid"]
    )
    assert (
        rig.observe() and acceptance.state()["active"]["cycle_id"] == first["cycle_id"]
    )


@pytest.mark.parametrize(
    "field",
    ["enabled", "paused", "manual_mouse", "manual_input_fence", "dead_candidate"],
)
def test_live_manual_and_survival_fences_prevent_trigger(rig, field):
    enable(rig)
    rig.farmer["inventory"].append(item(20))
    data = rig.health["embedded_controls"]
    if field in ("enabled", "paused"):
        data["control"][field] = field != "enabled"
    elif field == "dead_candidate":
        data["life"][field] = True
    else:
        data[field] = True
    assert not rig.observe() and acceptance.state()["active"] is None


def test_process_rollover_and_missing_native_provenance_fail_closed(rig, monkeypatch):
    enable(rig)
    rig.farmer["inventory"].append(item(20))
    monkeypatch.setattr(acceptance, "hunt_provenance", lambda *a: None)
    assert not rig.observe()
    rig.health["target"] = {**rig.health["target"], "creation_time_100ns": 999}
    with pytest.raises(ValueError, match="process changed"):
        rig.observe()


def test_precycle_transit_observation_defers_without_writing_or_triggering_input(rig):
    from conquest.merchants.memory import TransitObservationChanged

    enable(rig)
    before = acceptance.STATE.read_bytes()
    calls = []

    def transient(body):
        calls.append(body)
        raise TransitObservationChanged("Merchant position changed during observation")

    assert not acceptance.observe_hunting(rig.loop, rig.health, send=transient)
    assert calls == [{"action": "delivery-source"}]
    assert (
        acceptance.STATE.read_bytes() == before and acceptance.state()["active"] is None
    )
    # A later stable native tick is still free to make the independently
    # proven pickup trigger; the transient did not alter its baseline.
    selected = rig.trigger()
    assert acceptance.state()["active"]["item"] == selected


def test_precycle_transit_observation_defers_after_real_merchant_bridge_transport(
    rig, tmp_path
):
    from conquest.merchants.bridge import MerchantBridge, request
    from conquest.merchants.memory import TransitObservationChanged

    enable(rig)
    before = acceptance.STATE.read_bytes()
    bridge = MerchantBridge(
        lambda body: (_ for _ in ()).throw(
            TransitObservationChanged("Merchant position changed during observation")
        ),
        tmp_path / "bridge.json",
    )
    try:
        assert not acceptance.observe_hunting(
            rig.loop, rig.health, send=lambda body: request(body, bridge.path)
        )
    finally:
        bridge.close()
    assert (
        acceptance.STATE.read_bytes() == before and acceptance.state()["active"] is None
    )


def test_precycle_delivery_source_only_defers_the_exact_transit_exception(rig):
    enable(rig)

    def fatal(_):
        raise ValueError("merchant process changed during observation")

    with pytest.raises(ValueError, match="process changed"):
        acceptance.observe_hunting(rig.loop, rig.health, send=fatal)
    assert acceptance.state()["active"] is None


@pytest.mark.parametrize("phase", ["triggered", "town", "delivered"])
def test_transit_observation_never_defers_an_active_acceptance_cycle(rig, phase):
    from conquest.merchants.memory import TransitObservationChanged

    enable(rig)
    rig.trigger()
    if phase == "town":
        rig.town()
    elif phase == "delivered":
        acceptance.update(
            "test_delivered",
            lambda row: ({**row, "active": {**row["active"], "phase": "delivered"}}),
        )

    def transient(_):
        raise TransitObservationChanged("Merchant position changed during observation")

    with pytest.raises(TransitObservationChanged):
        acceptance.observe_hunting(rig.loop, rig.health, send=transient)
    assert acceptance.state()["active"]["phase"] == phase


def test_transit_observation_re_raises_when_another_worker_triggers_the_cycle(rig):
    from conquest.merchants.memory import TransitObservationChanged

    enable(rig)

    def raced(_):
        acceptance.update(
            "test_race",
            lambda row: (
                {
                    **row,
                    "phase": "running",
                    "active": {
                        "cycle_id": "raced",
                        "phase": "triggered",
                        "item": item(20),
                        "admissions": [],
                    },
                }
            ),
        )
        raise TransitObservationChanged("Merchant position changed during observation")

    with pytest.raises(TransitObservationChanged):
        acceptance.observe_hunting(rig.loop, rig.health, send=raced)
    assert acceptance.state()["active"]["cycle_id"] == "raced"


def test_precycle_transit_deferral_keeps_completed_cycles_and_same_run(rig):
    from conquest.merchants.memory import TransitObservationChanged

    enable(rig)
    before = acceptance.update(
        "test_completed",
        lambda row: ({**row, "cycles": [{"cycle_id": "old", "phase": "completed"}]}),
    )

    def transient(_):
        raise TransitObservationChanged("Merchant position changed during observation")

    assert not acceptance.observe_hunting(rig.loop, rig.health, send=transient)
    after = acceptance.state()
    assert (
        after["run_id"] == before["run_id"]
        and after["cycles"] == before["cycles"]
        and after["active"] is None
    )


def test_disable_disarms_only_before_trigger_and_retries_do_not_rearm(rig):
    enable(rig)
    body = {
        "action": "farmer-loop-acceptance",
        "enabled": False,
        "farmer_profile_id": "Parasite",
    }
    assert acceptance.configure(rig.ui, body)["phase"] == "disabled"
    assert not enable(rig)["enabled"]
    rig.body["request_id"] = "next"
    enable(rig)
    rig.trigger()
    with pytest.raises(ValueError, match="unresolved cycle"):
        acceptance.configure(rig.ui, body)
    assert acceptance.state()["enabled"]


def test_trial_permission_is_bound_to_qualification_route_process_and_admission(rig):
    enable(rig)
    selected = rig.trigger()
    rig.town()
    assert acceptance.trial_permitted(rig.loop)
    admitted = admission(rig, selected)
    origin = {
        k: admitted[k] for k in ("town_visit_id", "farmer_profile_id", "visit_id")
    }
    assert acceptance.trial_delivery_permitted(
        rig.ui, "trade", "Spiritual", [20], origin
    )
    assert not acceptance.trial_delivery_permitted(
        rig.ui, "other", "Spiritual", [20], origin
    )
    assert not acceptance.trial_delivery_permitted(
        rig.ui, "trade", "Spiritual", [10], origin
    )
    assert not acceptance.trial_delivery_permitted(
        rig.ui, "trade", "Dutch", [20], origin
    )
    assert not acceptance.trial_delivery_permitted(
        rig.ui, "trade", "Spiritual", [20], {**origin, "visit_id": "other"}
    )
    rig.qualification.write_text("changed")
    assert not acceptance.trial_permitted(rig.loop)


def test_trial_selects_only_new_uid_and_pinned_merchant(rig):
    enable(rig)
    selected = rig.trigger()
    rig.town()
    assert acceptance.plan_items(rig.farmer, None) == [selected]
    with pytest.raises(ValueError, match="substitute"):
        acceptance.plan_items(rig.farmer, [item(10)])
    assert acceptance.merchant_allowed("Spiritual", rig.status, rig.merchant)
    assert not acceptance.merchant_allowed("Dutch", rig.status, rig.merchant)
    changed = {**rig.merchant, "identity": {**rig.merchant["identity"], "pid": 99}}
    assert not acceptance.merchant_allowed("Spiritual", rig.status, changed)


@pytest.mark.parametrize(
    "changes",
    [
        {"next_action": "cleanup_trade_modal"},
        {"cleanup_pending": True},
        {"phase": "uncertain"},
        {"outcome": "no_transfer"},
        {"proof_digest": None},
        {"request_id": "historical"},
        {"town_visit_id": "other"},
        {"items": [item(99)]},
    ],
)
def test_only_matching_terminal_cleanup_free_bilateral_receipt_advances(rig, changes):
    enable(rig)
    selected = rig.trigger()
    rig.town()
    active = admission(rig, selected)
    receipt = {
        **active,
        "outcome": "transferred",
        "proof_digest": "proof",
        "verified_at": time.time(),
        "next_action": "release_route",
        **changes,
    }
    acceptance.settled(receipt)
    assert acceptance.state()["active"]["phase"] == "town"


@pytest.mark.parametrize(
    "change",
    ["old", "other_operation", "pending", "manual", "rollover", "missing_item"],
)
def test_refill_must_follow_same_delivery_and_prove_current_stock(rig, change):
    enable(rig)
    selected = rig.trigger()
    rig.town()
    receipt = delivered(rig, selected)
    if change == "old":
        rig.status["refill"]["last_completed_check_at"] = receipt["verified_at"] - 1
    elif change == "other_operation":
        rig.status["refill"]["source_delivery_operation_id"] = "other"
    elif change == "pending":
        rig.status["refill"]["pending"] = True
    elif change == "manual":
        rig.status["manual_input_fence"] = True
    elif change == "rollover":
        rig.merchant["identity"] = {**rig.merchant["identity"], "pid": 99}
    else:
        rig.merchant["inventory"] = []
    acceptance.refill_observed({"Spiritual": rig.status})
    assert "refill" not in acceptance.state()["active"]
    with pytest.raises(ValueError, match="lacks exact delivery"):
        acceptance.finish_town(
            rig.loop, send=lambda body: {"characters": {"Spiritual": rig.status}}
        )


def test_three_full_cycles_auto_disarm_without_promoting_global_rollout(rig):
    enable(rig)
    for number in range(3):
        selected = rig.trigger(uid=20 + number)
        visit = rig.town()
        delivered(rig, selected, key=f"trade-{number}")
        acceptance.finish_town(
            rig.loop, send=lambda body: {"characters": {"Spiritual": rig.status}}
        )
        active = acceptance.state()["active"]
        assert active["refill"]["item_disposition"] == "queued"
        from conquest.discord_notify import write_json

        completed = {
            **visit,
            "phase": "complete",
            "return_target": rig.farmer["identity"],
            "first_verified_resume_kill": {
                "time": time.time(),
                "rowid": number + 1,
                "count": 1,
            },
        }
        write_json(rig.loop.town_visit.path, completed)
        assert not rig.observe()
        assert len(acceptance.state()["cycles"]) == number + 1
    result = acceptance.state()
    assert (
        result["phase"] == "completed"
        and result["enabled"] is False
        and result["active"] is None
    )
    assert result["rollout_promoted"] is False
    assert [cycle["item"]["uid"] for cycle in result["cycles"]] == [20, 21, 22]
    assert rig.ui.app.control.snapshot()["enabled"] is False


def test_lost_acceptance_receipt_checkpoint_recovers_only_admitted_route_receipt(rig):
    enable(rig)
    selected = rig.trigger()
    rig.town()
    active = admission(rig, selected)
    receipt = {
        **active,
        "outcome": "transferred",
        "proof_digest": "proof",
        "verified_at": time.time(),
        "next_action": "release_route",
    }
    from conquest.merchants import delivery_route
    from conquest.discord_notify import write_json

    write_json(
        delivery_route.STATE, {"receipts": [{**receipt, "request_id": "old"}, receipt]}
    )
    acceptance.reconcile_route_receipts()
    assert acceptance.state()["active"]["delivery"]["request_id"] == "trade"
    assert acceptance.plan_items(rig.farmer, None) == []


def test_missing_selected_item_never_implies_a_completed_delivery(rig):
    enable(rig)
    rig.trigger()
    rig.town()
    rig.farmer["inventory"] = [item(10)]
    with pytest.raises(ValueError, match="no withdrawal or trade replay"):
        acceptance.verify_carried_or_delivered(rig.farmer)


def test_journey_scope_durably_excludes_any_unrelated_stored_scroll(rig):
    from conquest.merchants import delivery_journey

    enable(rig)
    rig.trigger()
    rig.town()
    saved = {"acceptance_scope": acceptance.journey_scope()}
    assert (
        delivery_journey.prepare_market_scroll(
            NS(),
            saved,
            send=lambda body: pytest.fail("No scroll observation or withdrawal"),
        )
        is False
    )
    assert saved["acceptance_scope"]["item"]["uid"] == 20


def test_bridge_dispatch_exposes_strict_local_control_surface(rig):
    from conquest.merchants.ui import UnifiedUI

    assert UnifiedUI.dispatch(rig.ui, rig.body)["enabled"]
    assert (
        UnifiedUI.dispatch(rig.ui, {"action": "farmer-loop-acceptance-status"})[
            "request_id"
        ]
        == "three-cycles"
    )
    with pytest.raises(ValueError):
        UnifiedUI.dispatch(
            rig.ui, {"action": "farmer-loop-acceptance-status", "extra": True}
        )


@pytest.mark.parametrize(
    "failure",
    [
        "no_kill",
        "old_kill",
        "missing_pickup",
        "wrong_uid",
        "old_pickup",
        "wrong_map",
        "recovered_inventory",
        "kill_after_pickup",
    ],
)
def test_real_native_provenance_rejects_unproven_hunt_or_pickup(
    rig, monkeypatch, failure
):
    row = enable(rig)
    selected = item(20)
    baseline = row["cycle_baseline_at"]
    kill = {"rowid": 7, "time": baseline + 0.01, "count": 1}
    pickup = {
        "inventory_uid": 20,
        "type_id": 410008,
        "plus": 1,
        "map_id": 1000,
        "increase": 1,
        "timestamp": baseline + 0.02,
        "source": "inventory_gain",
    }
    if failure == "no_kill":
        kill = None
    elif failure == "old_kill":
        kill["time"] = baseline - 1
    elif failure == "wrong_uid":
        pickup["inventory_uid"] = 21
    elif failure == "old_pickup":
        pickup["timestamp"] = baseline - 1
    elif failure == "wrong_map":
        pickup["map_id"] = 1036
    elif failure == "recovered_inventory":
        pickup["source"] = "recovered_inventory"
    elif failure == "kill_after_pickup":
        kill["time"] = baseline + 0.03
    monkeypatch.setattr(acceptance.time, "time", lambda: baseline + 1)
    monkeypatch.setattr(
        "conquest.town_visit.kill_checkpoint",
        lambda **kw: {"available": True, "resume_kill": kill},
    )
    if failure != "missing_pickup":
        acceptance.PICKUPS.write_text(json.dumps(pickup) + "\n")
    assert REAL_PROVENANCE(row, selected) is None


def test_real_native_provenance_binds_exact_post_baseline_item_and_verified_kill(
    rig, monkeypatch
):
    row = enable(rig)
    baseline = row["cycle_baseline_at"]
    selected = item(20)
    pickup = {
        "inventory_uid": 20,
        "type_id": 410008,
        "plus": 1,
        "map_id": 1000,
        "increase": 1,
        "timestamp": baseline + 0.02,
        "source": "ground_pickup",
    }
    kill = {"rowid": 7, "time": baseline + 0.01, "count": 1}
    acceptance.PICKUPS.write_text(json.dumps(pickup) + "\n")
    monkeypatch.setattr(acceptance.time, "time", lambda: baseline + 1)
    monkeypatch.setattr(
        "conquest.town_visit.kill_checkpoint",
        lambda **kw: {"available": True, "resume_kill": kill},
    )
    proof = REAL_PROVENANCE(row, selected)
    assert proof["pickup"] == pickup and proof["verified_kill"] == kill


@pytest.mark.parametrize(
    "change", ["enabled", "refill", "process", "profile", "uid", "manual"]
)
def test_trial_rechecks_merchant_permissions_and_identity_at_submission(rig, change):
    enable(rig)
    selected = rig.trigger()
    rig.town()
    active = admission(rig, selected)
    origin = {k: active[k] for k in ("town_visit_id", "farmer_profile_id", "visit_id")}
    if change == "enabled":
        rig.status["enabled"] = False
    elif change == "refill":
        rig.status["refill"]["enabled"] = False
    elif change == "process":
        rig.merchant["identity"] = {**rig.merchant["identity"], "pid": 99}
    elif change == "profile":
        rig.status["profile_id"] = "other"
    elif change == "uid":
        rig.merchant["character_uid"] = 99
    else:
        rig.status["manual_input_fence"] = True
    assert not acceptance.trial_delivery_permitted(
        rig.ui, "trade", "Spiritual", [20], origin
    )


def test_native_hunt_returns_immediately_on_verified_new_deliverable(rig, monkeypatch):
    from conquest.overnight import OvernightLoop, OvernightStopped

    enable(rig)
    rig.farmer["inventory"].append(item(20))
    loop = rig.loop
    calls = []
    loop.route.monster_type_ids = [1]
    loop.info = "worker"
    loop.living = lambda: rig.health
    loop.health = lambda: rig.health
    loop.stop_farm = lambda: calls.append("stop")
    loop.focus = lambda *args: pytest.fail(
        "New deliverable must return before another combat focus"
    )
    monkeypatch.setattr("conquest.world_travel.travel_to_map", lambda *a: None)
    monkeypatch.setattr("conquest.city_travel.ensure_city_visit", lambda *a: None)
    monkeypatch.setattr("conquest.overnight.request", lambda *a, **kw: None)
    monkeypatch.setattr(
        "conquest.merchants.bridge.request",
        lambda body: {"farmer": deepcopy(rig.farmer)},
    )
    rig.fresh()
    assert OvernightLoop.hunt(loop) == "merchant_acceptance" and calls == ["stop"]
    rig.health["embedded_controls"]["control"]["enabled"] = False
    with pytest.raises(OvernightStopped, match="switched Off"):
        OvernightLoop.hunt(loop)
    assert calls == ["stop"]


def native_town_rig(rig, monkeypatch):
    from conquest.overnight import OvernightLoop

    selected = acceptance.state()["active"]["item"]
    calls = []
    rig.loop.check_stop = lambda: None
    rig.loop.route.supplies = NS(arrow_type=1050002, healing_type=1000020)

    def town(action, **fields):
        calls.append((action, fields))
        return {
            "items": [
                {"type_id": 1050002, "amount": 1000},
                {"type_id": 1000020, "amount": 3},
            ],
            "capacity": 40,
            "silver": 200,
        }

    rig.loop.town = town
    rig.loop.restock = lambda: pytest.fail(
        "Stocked acceptance return must not force extra shopping"
    )
    monkeypatch.setattr(
        "conquest.return_scroll.return_to_town",
        lambda loop: calls.append("native_return"),
    )
    monkeypatch.setattr(
        "conquest.world_travel.travel_to_map",
        lambda loop, world: calls.append(("native_travel", world)),
    )

    def after_shopping(loop):
        calls.append("native_after_shopping")
        delivered(rig, selected)
        return True

    monkeypatch.setattr("conquest.banking.after_shopping", after_shopping)
    monkeypatch.setattr(
        "conquest.merchants.handoff.service_window",
        lambda loop, **kw: calls.append("native_refill_window"),
    )

    def send(body):
        if body["action"] == "delivery-source":
            return {"farmer": deepcopy(rig.farmer)}
        if body["action"] == "status":
            return {"characters": {"Spiritual": deepcopy(rig.status)}}
        raise AssertionError(body)

    monkeypatch.setattr("conquest.merchants.bridge.request", send)
    return lambda: OvernightLoop.bank_acceptance_delivery(rig.loop), calls


@pytest.mark.parametrize("phase", ["triggered", "town", "delivered"])
def test_native_return_restarts_from_durable_phase_without_redelivering(
    rig, monkeypatch, phase
):
    enable(rig)
    selected = rig.trigger()
    if phase != "triggered":
        rig.town()
    if phase == "delivered":
        delivered(rig, selected)
    run, calls = native_town_rig(rig, monkeypatch)
    run()
    assert acceptance.state()["active"]["phase"] == "awaiting_hunt"
    assert ("native_after_shopping" in calls) is (phase != "delivered")
    assert ("native_return" in calls) is (phase != "delivered")
    assert (
        calls.count("native_refill_window") == 0
    )  # Already verified delivery refill is not restarted.


@pytest.mark.parametrize(
    "mismatch",
    [
        None,
        "scope",
        "request",
        "merchant",
        "item",
        "process",
        "merchant_uid",
        "visit",
        "town_visit",
        "profile",
        "missing_admission",
    ],
)
def test_route_startup_reconciles_transferred_uid_before_acceptance_ownership_check(
    rig, monkeypatch, mismatch
):
    """A crash between bilateral transfer and route receipt must not re-admit."""
    from conquest.overnight import OvernightLoop
    from conquest.merchants import delivery_journey, delivery_route
    from conquest.discord_notify import read_json, write_json

    enable(rig)
    selected = rig.trigger()
    rig.town()
    active = admission(rig, selected)
    rig.farmer["inventory"] = [
        i for i in rig.farmer["inventory"] if i["uid"] != selected["uid"]
    ]
    rig.merchant["inventory"].append(deepcopy(selected))
    rig.status["refill"] = {
        "enabled": True,
        "pending": False,
        "status": "completed",
        "operation_id": "trade",
        "source_delivery_operation_id": "trade",
        "last_completed_check_at": time.time() + 1,
    }
    scope = acceptance.journey_scope()
    if mismatch == "scope":
        scope = {**scope, "cycle_id": "unrelated-cycle"}
    native = deepcopy(active)
    if mismatch == "request":
        native["request_id"] = "unrelated-request"
    elif mismatch == "merchant":
        native["merchant"] = "Dutch"
    elif mismatch == "item":
        native["items"] = [item(99)]
    elif mismatch == "process":
        native["merchant_identity"]["pid"] = 99
    elif mismatch == "merchant_uid":
        native["merchant_uid"] = 99
    elif mismatch == "visit":
        native["visit_id"] = "another-market-visit"
    elif mismatch == "town_visit":
        native["town_visit_id"] = "another-town-visit"
    elif mismatch == "profile":
        native["farmer_profile_id"] = "another-profile"
    elif mismatch == "missing_admission":

        def clear_admissions(row):
            row["active"]["admissions"] = []
            return row

        acceptance.update("test_missing_admission", clear_admissions)
    write_json(
        delivery_journey.JOURNAL,
        {
            "phase": "market",
            "origin": 1002,
            "route": {},
            "acceptance_scope": scope,
            "receipts": [],
        },
    )
    write_json(delivery_route.STATE, {"active": native})
    _, calls = native_town_rig(rig, monkeypatch)
    rig.farmer["map_id"] = 1036
    rig.loop.living = lambda: {
        "embedded_controls": {"life": {"map_id": rig.farmer["map_id"]}}
    }
    rig.loop.stop_farm = lambda: calls.append("stop_farm")
    rig.loop.bank_acceptance_delivery = lambda: OvernightLoop.bank_acceptance_delivery(
        rig.loop
    )
    rig.loop.return_to_route_map = lambda: rig.farmer.update(map_id=1000)
    rig.loop.prepare_supplies = lambda: None
    rig.loop.select_level_route = lambda: None

    class HuntResumed(Exception):
        pass

    def hunt():
        assert acceptance.state()["active"]["phase"] == "awaiting_hunt"
        raise HuntResumed()

    rig.loop.hunt = hunt
    native_receipt = {
        **active,
        "character": "Spiritual",
        "uids": [selected["uid"]],
        "phase": "verified",
        "outcome": "transferred",
        "delivered": [selected],
        "proof_digest": "bilateral-proof",
        "next_action": "release_route",
        "cleanup_pending": False,
    }

    def send(body):
        calls.append(body["action"])
        assert body["action"] != "delivery-start", (
            "Restart must never repeat native admission"
        )
        if body["action"] == "delivery-status":
            return {"running": False, "receipt": native_receipt}
        if body["action"] == "delivery-reconcile":
            return {"receipt": native_receipt}
        if body["action"] == "delivery-source":
            assert read_json(delivery_route.STATE).get("active") is None
            return {"farmer": deepcopy(rig.farmer)}
        if body["action"] == "status":
            return {"characters": {"Spiritual": deepcopy(rig.status)}}
        raise AssertionError(body)

    monkeypatch.setattr("conquest.merchants.bridge.request", send)
    original_resume = delivery_journey.resume
    monkeypatch.setattr(
        delivery_journey, "resume", lambda loop: original_resume(loop, send=send)
    )
    monkeypatch.setattr("conquest.navigation.read_terrain", lambda *a: NS())
    monkeypatch.setattr(delivery_route, "check_stop", lambda loop: loop.check_stop())

    def no_remaining_delivery(loop, **kw):
        assert acceptance.state()["active"]["phase"] == "delivered"
        assert acceptance.plan_items(rig.farmer, None) == []
        calls.append("native_delivery_planning_empty")
        return []

    monkeypatch.setattr(delivery_route, "market_storage", no_remaining_delivery)

    def return_leg(loop, state, name):
        assert name == "return" and acceptance.state()["active"]["phase"] == "delivered"
        delivery_journey.save(state, phase="return_pending")
        rig.farmer["map_id"] = 1002

    monkeypatch.setattr(delivery_journey, "leg", return_leg)
    monkeypatch.setattr(delivery_journey, "verify_arrival", lambda *a: None)
    monkeypatch.setattr("conquest.banking.open_warehouse", lambda loop: None)
    monkeypatch.setattr(
        "conquest.banking.after_shopping",
        lambda loop: calls.append("post_journey_banking"),
    )
    monkeypatch.setattr(
        "conquest.merchants.service_visit.MarketVisit",
        lambda: NS(departed=lambda world: None),
    )
    monkeypatch.setattr("conquest.city_travel.ensure_city_visit", lambda loop: None)
    if mismatch:
        with pytest.raises(ValueError, match="scope changed|admission changed"):
            OvernightLoop._run_route(rig.loop)
        assert "delivery-reconcile" not in calls and "delivery-source" not in calls
        assert read_json(delivery_route.STATE)["active"] == native
        return
    with pytest.raises(HuntResumed):
        OvernightLoop._run_route(rig.loop)
    assert calls.count("delivery-reconcile") == 1 and "delivery-start" not in calls
    assert calls.index("delivery-reconcile") < calls.index("delivery-source")
    assert len(read_json(delivery_route.STATE)["receipts"]) == 1
    assert acceptance.state()["active"]["delivery"]["request_id"] == "trade"


def test_manual_stop_at_native_town_boundary_retains_unresolved_evidence(
    rig, monkeypatch
):
    from conquest.overnight import OvernightStopped

    enable(rig)
    rig.trigger()
    run, calls = native_town_rig(rig, monkeypatch)
    rig.loop.check_stop = lambda: (_ for _ in ()).throw(OvernightStopped("user stop"))
    with pytest.raises(OvernightStopped):
        run()
    assert calls == [] and acceptance.state()["active"]["phase"] == "triggered"


@pytest.mark.parametrize(
    "change", ["visit", "kill_time", "target", "manual", "stopped"]
)
def test_return_after_restart_requires_matching_verified_unfenced_hunt(rig, change):
    enable(rig)
    selected = rig.trigger()
    visit = rig.town()
    delivered(rig, selected)
    acceptance.finish_town(
        rig.loop, send=lambda body: {"characters": {"Spiritual": rig.status}}
    )
    completed = {
        **visit,
        "phase": "complete",
        "return_target": deepcopy(rig.farmer["identity"]),
        "first_verified_resume_kill": {"time": time.time(), "rowid": 1, "count": 1},
    }
    if change == "visit":
        completed["town_visit_id"] = "other"
    elif change == "kill_time":
        completed["first_verified_resume_kill"]["time"] = 0
    elif change == "target":
        completed["return_target"]["pid"] = 99
    elif change == "manual":
        rig.health["embedded_controls"]["manual_input_fence"] = True
    else:
        rig.health["embedded_controls"]["control"]["enabled"] = False
    from conquest.discord_notify import write_json

    write_json(rig.loop.town_visit.path, completed)
    assert not rig.observe()
    assert (
        acceptance.state()["cycles"] == []
        and acceptance.state()["active"]["phase"] == "awaiting_hunt"
    )


def test_trial_native_admission_can_cross_only_the_scoped_rollout_gate(
    rig, monkeypatch
):
    from conquest.merchants import delivery_operation as operation

    enable(rig)
    selected = rig.trigger()
    rig.town()
    active = admission(rig, selected)
    origin = {k: active[k] for k in ("town_visit_id", "farmer_profile_id", "visit_id")}
    rig.farmer["map_id"] = 1036
    rig.ui.app.observer.character = "Parasite"
    rig.ui.runtime.delivery_window = "trade"
    rig.ui.runtime.enabled = lambda name: True
    rig.ui.runtime.controllers = {
        "Spiritual": NS(driver=NS(require_qualified=lambda capability: None))
    }
    driver = NS(
        require_qualified=lambda: None,
        check=lambda: None,
        read_pair=lambda name: (deepcopy(rig.farmer), deepcopy(rig.merchant)),
    )
    monkeypatch.setattr(operation, "FarmerTradeDriver", lambda ui: driver)
    monkeypatch.setattr(
        operation,
        "read_json",
        lambda path: {"enabled": False, "parity_verified": False},
    )
    monkeypatch.setattr(operation, "status", lambda *a: None)
    begun = []
    journal = NS(
        update_delivery_admission=lambda *a, **kw: None,
        begin=lambda key, name, kind, intent, **kw: begun.append(intent) or True,
    )
    result = operation.prepare_new(
        rig.ui, journal, "trade", "Spiritual", [20], "delivery-start", origin
    )
    assert result[0] is driver and begun[0]["items"] == [selected]
    with pytest.raises(ValueError, match="rollout is not enabled"):
        operation.prepare_new(
            rig.ui, journal, "trade", "Spiritual", [10], "delivery-start", origin
        )
    assert len(begun) == 1


@pytest.mark.parametrize("phase", ["triggered", "town", "awaiting_hunt"])
def test_input_free_abort_restores_mode_without_claiming_a_completed_cycle(rig, phase):
    enable(rig)
    selected = rig.trigger()
    if phase == "town":
        rig.town()
    if phase == "awaiting_hunt":
        rig.town()
        delivered(rig, selected)
        acceptance.finish_town(
            rig.loop, send=lambda body: {"characters": {"Spiritual": rig.status}}
        )
    body = {
        "action": "farmer-loop-acceptance-abort",
        "farmer_profile_id": "Parasite",
        "run_id": acceptance.state()["run_id"],
    }
    result = acceptance.configure(rig.ui, body)
    assert result["enabled"] is False and result["phase"] == "aborted"
    assert result["cycles"] == [] and result["aborted_cycle"]["previous_phase"] == phase
    assert acceptance.configure(rig.ui, body) == result
    assert not acceptance.trial_permitted()


@pytest.mark.parametrize(
    "failure", ["on", "input", "pending_refill", "missing_item", "wrong_run", "town"]
)
def test_abort_does_not_abandon_ownership_or_unresolved_work(rig, failure):
    enable(rig)
    rig.trigger()
    body = {
        "action": "farmer-loop-acceptance-abort",
        "farmer_profile_id": "Parasite",
        "run_id": acceptance.state()["run_id"],
    }
    if failure == "on":
        rig.ui.app.control.snapshot = lambda: {"enabled": True, "paused": False}
    elif failure == "input":
        rig.ui.coordinator.owner = "Farmer"
    elif failure == "pending_refill":
        rig.status["refill"]["pending"] = True
    elif failure == "missing_item":
        rig.farmer["inventory"] = [item(10)]
    elif failure == "wrong_run":
        body["run_id"] = "another-run"
    else:
        rig.town()
        acceptance.town_input_boundary()
    with pytest.raises(ValueError):
        acceptance.configure(rig.ui, body)
    assert acceptance.state()["enabled"] and acceptance.state()["active"]


def test_budget_paused_refill_preserves_source_delivery_across_new_native_grant(rig):
    from conquest.merchants.refill import RefillSchedule

    values = {}
    events = []
    journal = NS(
        get=lambda character, key, default=None: deepcopy(values.get(key, default)),
        set=lambda character, key, value: values.__setitem__(key, deepcopy(value)),
        event=lambda *a, **kw: events.append((a, kw)),
    )
    schedule = RefillSchedule("Spiritual", journal)
    enable(rig)
    selected = rig.trigger()
    rig.town()
    receipt = delivered(rig, selected)
    schedule.start(
        operation_id="trade",
        source_delivery_operation_id="trade",
        town_visit_id="town",
        visit_id="market",
    )
    schedule.pause_budget()
    resumed = RefillSchedule("Spiritual", journal)
    resumed.start(operation_id="restock-refill:new", town_visit_id="town")
    assert resumed.state()["source_delivery_operation_id"] == "trade"
    resumed.complete("completed", deferred=1)
    rig.status["refill"] = {**resumed.state(), "enabled": True}
    acceptance.refill_observed({"Spiritual": rig.status})
    assert (
        acceptance.state()["active"]["refill"]["state"]["operation_id"]
        == "restock-refill:new"
    )
    assert acceptance.state()["active"]["refill"]["item_disposition"] == "queued"
    resumed.start(operation_id="unrelated-check")
    assert resumed.state()["source_delivery_operation_id"] is None


def test_pending_refill_cannot_be_reparented_to_different_delivery():
    from conquest.merchants.refill import RefillSchedule

    values = {}
    journal = NS(
        get=lambda character, key, default=None: deepcopy(values.get(key, default)),
        set=lambda character, key, value: values.__setitem__(key, deepcopy(value)),
    )
    schedule = RefillSchedule("Spiritual", journal)
    schedule.start(operation_id="trade", source_delivery_operation_id="trade")
    with pytest.raises(ValueError, match="another delivery"):
        schedule.start(operation_id="another", source_delivery_operation_id="another")
    assert schedule.state()["source_delivery_operation_id"] == "trade"


def test_ordinary_refill_behavior_is_unchanged_without_exact_active_acceptance_receipt(
    rig,
):
    assert acceptance.refill_source("trade", "Spiritual") is None
    enable(rig)
    selected = rig.trigger()
    rig.town()
    assert acceptance.refill_source("trade", "Spiritual") is None
    delivered(rig, selected)
    assert acceptance.refill_source("trade", "Spiritual") == "trade"
    assert acceptance.refill_source("unrelated", "Spiritual") is None
    assert acceptance.refill_source("trade", "Dutch") is None


def test_pending_ordinary_refill_accepts_later_ordinary_delivery_without_reparent_guard():
    from conquest.merchants.refill import RefillSchedule

    values = {}
    journal = NS(
        get=lambda character, key, default=None: deepcopy(values.get(key, default)),
        set=lambda character, key, value: values.__setitem__(key, deepcopy(value)),
        event=lambda *a, **kw: None,
    )
    schedule = RefillSchedule("Spiritual", journal)
    schedule.start(
        operation_id="ordinary-first",
        source_delivery_operation_id=acceptance.refill_source(
            "ordinary-first", "Spiritual"
        ),
    )
    schedule.pause_budget()
    schedule.start(
        operation_id="ordinary-second",
        source_delivery_operation_id=acceptance.refill_source(
            "ordinary-second", "Spiritual"
        ),
    )
    assert schedule.state()["operation_id"] == "ordinary-second"
    assert schedule.state()["source_delivery_operation_id"] is None


@pytest.mark.parametrize("warehouse_available", [False, True])
def test_explicit_digest_override_restores_failed_town_mode_without_claiming_delivery(
    rig, warehouse_available
):
    enable(rig)
    selected = rig.trigger()
    rig.town()
    acceptance.town_input_boundary()
    rig.farmer["inventory"] = [
        i for i in rig.farmer["inventory"] if i["uid"] != selected["uid"]
    ]
    calls = []

    def warehouse(body):
        calls.append(body)
        assert body == {"action": "warehouse-items", "rich": True}
        if not warehouse_available:
            raise ValueError("Warehouse is not open")
        return {"items": [deepcopy(selected)], "capacity": 40}

    rig.ui.app.observer.lock = threading.RLock()
    rig.ui.app.observer.town_trade = warehouse
    body = {
        "action": "farmer-loop-acceptance-override-preview",
        "farmer_profile_id": "Parasite",
        "run_id": acceptance.state()["run_id"],
    }
    preview = acceptance.configure(rig.ui, body)
    assert preview["historical_outcome"] == "unknown_or_deferred"
    assert preview["ownership"]["warehouse"]["available"] is warehouse_available
    command = {
        **body,
        "action": "farmer-loop-acceptance-override",
        "operator_confirmed": True,
        "incident_digest": preview["incident_digest"],
    }
    result = acceptance.configure(rig.ui, command)
    assert result["phase"] == "operator_overridden" and not result["enabled"]
    assert result["cycles"] == [] and result["overridden_cycle"]["phase"] == "town"
    assert result["operator_override"]["native_journals_unchanged"] is True
    assert acceptance.configure(rig.ui, command) == result
    assert (
        len(calls) == 2
    )  # Only read-only warehouse observation, never open/withdraw/trade.


@pytest.mark.parametrize(
    "change", ["inventory", "unconfirmed", "digest", "pending_refill", "running"]
)
def test_override_refuses_changed_evidence_or_unresolved_native_work(rig, change):
    enable(rig)
    rig.trigger()
    rig.town()
    acceptance.town_input_boundary()
    rig.ui.app.observer.lock = threading.RLock()
    rig.ui.app.observer.town_trade = lambda body: (_ for _ in ()).throw(
        ValueError("closed")
    )
    body = {
        "action": "farmer-loop-acceptance-override-preview",
        "farmer_profile_id": "Parasite",
        "run_id": acceptance.state()["run_id"],
    }
    preview = acceptance.configure(rig.ui, body)
    command = {
        **body,
        "action": "farmer-loop-acceptance-override",
        "operator_confirmed": True,
        "incident_digest": preview["incident_digest"],
    }
    if change == "inventory":
        rig.farmer["inventory"].append(item(99))
    elif change == "unconfirmed":
        command["operator_confirmed"] = False
    elif change == "digest":
        command["incident_digest"] = "wrong"
    elif change == "pending_refill":
        rig.status["refill"]["pending"] = True
    else:
        rig.ui.app.control.snapshot = lambda: {"enabled": True}
    with pytest.raises(ValueError):
        acceptance.configure(rig.ui, command)
    assert (
        acceptance.state()["enabled"]
        and acceptance.state()["active"]["phase"] == "town"
    )
