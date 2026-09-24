from copy import deepcopy
import threading
from types import SimpleNamespace as NS

import pytest

from conquest.memory_life import CLIENT_SHA256
from conquest.merchants import delivery_probe as probe
from conquest.merchants.delivery_probe_ownership import local_ownership
from conquest.merchants.manual_sessions import BindingMismatch
from test_delivery_probe_manual_ownership import supervised, open_trade
from test_manual_runtime import rig


def blocked_peer(x, role):
    peer = x.source if role == "merchant" else x.runtime.observers["Dutch"]
    peer.lock = NS(acquire=lambda **_kw: False, release=lambda: None)


def observe(x, role):
    owner, snapshot = (
        ("Dutch", x.read()) if role == "merchant" else ("Farmer", x.farmer_read())
    )
    return x.runtime.process_manual(owner, snapshot, now=x.now)


@pytest.mark.parametrize("role", ["farmer", "merchant"])
@pytest.mark.parametrize(
    "phase,offered",
    [
        ("accept_submitted", False),
        ("trade_open_verified", False),
        ("placement_submitted", False),
        ("placement_submitted", True),
        ("trade_open_verified", True),
        ("offer_verified", True),
        ("farmer_confirm_submitted", True),
        ("farmer_confirm_verified", True),
        ("merchant_confirm_submitted", True),
    ],
)
def test_peer_lock_defers_exact_trade_without_manual_admission(
    supervised, role, phase, offered
):
    x = supervised
    open_trade(x, phase=phase, offered=offered)
    blocked_peer(x, role)
    for _ in range(3):
        assert observe(x, role)
    assert x.runtime.manual_status() == []
    assert x.calls == [] and x.runtime.manual_sessions.permissions() == []
    assert x.journal.get("Dutch", "enabled") is True
    assert not x.runtime.process_probe_owned(
        "Dutch" if role == "merchant" else "Farmer",
        x.read() if role == "merchant" else x.farmer_read(),
        require_bilateral=True,
    )


@pytest.mark.parametrize("role", ["farmer", "merchant"])
@pytest.mark.parametrize(
    "phase,offered", [("trade_open_verified", False), ("offer_verified", True)]
)
def test_actual_cross_thread_peer_lock_race_is_observation_only(
    supervised, role, phase, offered
):
    x = supervised
    open_trade(x, phase=phase, offered=offered)
    peer = x.source if role == "merchant" else x.runtime.observers["Dutch"]
    held, release = threading.Event(), threading.Event()

    def hold():
        with peer.lock:
            held.set()
            assert release.wait(5)

    worker = threading.Thread(target=hold)
    worker.start()
    try:
        assert held.wait(5)
        assert observe(x, role)
        assert x.runtime.manual_status() == [] and x.calls == []
    finally:
        release.set()
        worker.join(5)


@pytest.mark.parametrize("role", ["farmer", "merchant"])
@pytest.mark.parametrize(
    "fault",
    [
        "process",
        "name",
        "uid",
        "server",
        "position",
        "map",
        "silver",
        "inventory",
        "capacity",
        "booth",
        "peer_uid",
        "peer_name",
        "peer_server",
        "own_silver",
        "other_silver",
        "acceptance",
        "reverse_offer",
        "offer",
        "missing_offer",
        "phase",
        "saved_pair",
        "saved_time",
        "saved_after_write",
        "saved_before_start",
        "saved_acceptance",
        "rollover",
        "digest",
    ],
)
def test_local_contention_mismatch_still_quarantines(
    supervised, monkeypatch, role, fault
):
    x = supervised
    open_trade(x, phase="offer_verified", offered=True)
    blocked_peer(x, role)
    value = x.farmer if role == "farmer" else x.state
    if fault == "process":
        value["identity"]["creation_time_100ns"] += 1
    if fault == "name":
        value["character"] = "Other"
    if fault == "uid":
        value["character_uid"] += 1
    if fault == "server":
        value["server"] = "Other"
    if fault == "position":
        value["position"][0] += 1
    if fault == "map":
        value["map_id"] = 1002
    if fault == "silver":
        value["silver"] += 1
    if fault == "inventory":
        value["inventory"].append({**deepcopy(value["inventory"][0]), "uid": 901})
    if fault == "capacity":
        value["capacity"] -= 1
    if fault == "booth":
        value["own_booth_uid"] += 1
    if fault == "peer_uid":
        value["trade"]["participant_uid"] += 1
    if fault == "peer_name":
        value["trade"]["participant"] = "Other"
    if fault == "peer_server":
        value["trade"]["server"] = "Other"
    if fault == "own_silver":
        value["trade"]["own_silver"] = 1
    if fault == "other_silver":
        value["trade"]["other_silver"] = False
    if fault == "acceptance":
        value["trade"]["accepted"] = True
    offered_key = "own_items" if role == "farmer" else "items"
    if fault == "reverse_offer":
        value["trade"]["items" if role == "farmer" else "own_items"] = deepcopy(
            x.probe["intent"]["items"]
        )
    if fault == "offer":
        value["trade"][offered_key][0]["quantity"] += 1
    if fault == "missing_offer":
        value["trade"][offered_key] = []
    if fault == "phase":
        x.probe["phase"] = "request_verified"
    if fault == "saved_pair":
        x.probe["merchant_after"]["trade"]["items"][0]["uid"] += 1
    if fault == "saved_time":
        x.probe["farmer_after"]["timestamp"] = 101
    if fault == "saved_after_write":
        x.now = 102
        x.probe["farmer_after"]["timestamp"] = 101
    if fault == "saved_before_start":
        x.probe["farmer_after"]["timestamp"] = 99
    if fault == "saved_acceptance":
        x.probe["merchant_after"]["trade"]["accepted"] = True
    if fault == "rollover":
        peer = x.source if role == "merchant" else x.runtime.observers["Dutch"]
        peer.adapter.identity = {**peer.adapter.identity, "creation_time_100ns": 999}
    x.save()
    if fault == "digest":
        states = iter([deepcopy(x.probe), {**x.probe, "phase": "delivery_verified"}])
        monkeypatch.setattr(probe, "read_probe", lambda: next(states))
    assert observe(x, role)
    owner = "Dutch" if role == "merchant" else "Farmer"
    assert x.runtime.manual_status(owner)["phase"] == "needs_attention"
    assert x.guard.manual_session_blocked(owner) and x.calls == []


@pytest.mark.parametrize("role", ["farmer", "merchant"])
def test_structural_trade_never_releases_existing_hold(supervised, role):
    x = supervised
    open_trade(x, phase="offer_verified", offered=True)
    owner, snapshot = (
        ("Dutch", x.read()) if role == "merchant" else ("Farmer", x.farmer_read())
    )
    row = x.runtime.manual_sessions.observe_target(owner, snapshot, now=x.now)
    x.runtime._sync_manual_fence()
    blocked_peer(x, role)
    assert observe(x, role)
    assert x.runtime.manual_sessions.get(row["id"]) == row
    assert x.guard.manual_session_blocked(owner) and x.calls == []


@pytest.mark.parametrize("role", ["farmer", "merchant"])
def test_restart_attachment_gap_defers_only_observation(supervised, role):
    x = supervised
    open_trade(x, phase="offer_verified", offered=True)
    if role == "merchant":
        x.runtime.manual_farmer_provider = lambda: None
    else:
        x.runtime.observers.pop("Dutch")
    assert observe(x, role)
    assert x.runtime.manual_status() == [] and x.calls == []


@pytest.mark.parametrize(
    "fault",
    [None, "after_accept", "visitor", "silver", "process", "slot", "peer_process"],
)
def test_request_read_before_accept_write_cannot_mint_false_manual_session(
    supervised, fault
):
    x = supervised
    stale = x.read()
    x.now += 0.2
    open_trade(x)
    x.now += 0.2
    if fault == "after_accept":
        stale["timestamp"] = x.now
    if fault == "visitor":
        stale["request"]["participant_uid"] += 1
    if fault == "silver":
        stale["silver"] += 1
    if fault == "process":
        stale["identity"]["pid"] += 1
    if fault == "slot":
        stale["inventory"][0]["slot"] = 20
    if fault == "peer_process":
        x.source.adapter.identity = {**x.source.adapter.identity, "pid": 999}
    assert x.runtime.process_manual("Dutch", stale, now=x.now)
    assert (x.runtime.manual_status("Dutch") is None) == (fault is None)
    assert x.calls == []


@pytest.mark.parametrize("stage", ["offer", "confirm"])
@pytest.mark.parametrize(
    "reconciled,held", [(False, None), (True, None), (True, "Farmer"), (True, "Dutch")]
)
def test_trade_stage_reconciles_pair_before_first_lease_or_input(
    supervised, monkeypatch, stage, reconciled, held
):
    from conquest.capture import CaptureUnavailable
    from conquest.merchants import delivery_offer_probe, delivery_confirm_probe

    x = supervised
    open_trade(
        x,
        phase="trade_open_verified" if stage == "offer" else "offer_verified",
        offered=stage == "confirm",
    )
    module = delivery_offer_probe if stage == "offer" else delivery_confirm_probe
    events = []

    class ReachedLease(Exception):
        pass

    def reconcile(character, farmer, merchant):
        events.append("reconcile")
        assert (
            character == "Dutch" and farmer == x.farmer_read() and merchant == x.read()
        )
        return reconciled

    def lease(*_args, **_kwargs):
        events.append("lease")
        assert ui.coordinator.lock._is_owned()
        concurrent = []

        def try_admit():
            acquired = ui.coordinator.lock.acquire(blocking=False)
            concurrent.append(acquired)
            if acquired:
                ui.coordinator.lock.release()

        worker = threading.Thread(target=try_admit)
        worker.start()
        worker.join(5)
        assert concurrent == [False]  # No peer hold/focus gap after proof.
        raise ReachedLease()

    monkeypatch.setattr(module, "pair", lambda *_a: (x.farmer_read(), x.read()))
    monkeypatch.setattr("conquest.merchants.memory.MerchantMemory", lambda *_a: NS())
    # The offer stage now builds source memory through delivery_bridge, which
    # binds MerchantMemory at import time.
    monkeypatch.setattr(
        "conquest.merchants.delivery_bridge.MerchantMemory", lambda *_a: NS()
    )
    monkeypatch.setattr(
        "conquest.foreground.foreground_drag",
        lambda *_a, **_kw: pytest.fail("Unexpected input"),
    )
    monkeypatch.setattr(
        "conquest.foreground.foreground_click",
        lambda *_a, **_kw: pytest.fail("Unexpected input"),
    )
    monkeypatch.setattr(
        "conquest.focus_recovery.activate_client",
        lambda *_a: pytest.fail("Unexpected focus"),
    )
    ui = NS(
        app=NS(
            control=NS(snapshot=lambda: {"revision": 1, "enabled": False}),
            closing=False,
            # A pre-1078 client keeps source_memory on the MerchantMemory path.
            observer=NS(
                operations=NS(target=NS()),
                adapter=NS(expected_sha256=CLIENT_SHA256),
            ),
        ),
        closed=False,
        safe_to_yield=lambda: True,
        delivery_probe_thread=threading.current_thread(),
        coordinator=NS(
            lock=threading.RLock(),
            check=lambda: None,
            lease=lease,
            manual_session_blocked=lambda owner: owner == held,
        ),
        runtime=NS(reconcile_probe_pair=reconcile),
    )
    monkeypatch.setattr(
        "conquest.merchants.farmer_preferences.permits_new_delivery", lambda *a: None
    )
    monkeypatch.setattr("ctypes.windll.user32.GetAsyncKeyState", lambda *a: 0)
    with pytest.raises(
        ReachedLease if reconciled and held is None else CaptureUnavailable
    ):
        module.run(ui, deepcopy(x.probe))
    assert events == (
        ["reconcile", "lease"] if reconciled and held is None else ["reconcile"]
    )


@pytest.mark.parametrize(
    "fault", ["premature_merchant", "missing_nonoffered", "extra_nonoffered"]
)
def test_full_trade_proof_rejects_premature_acceptance_and_surrounding_stock(
    supervised, fault
):
    x = supervised
    extra = {**deepcopy(x.farmer["inventory"][0]), "uid": 300}
    x.farmer["inventory"].append(deepcopy(extra))
    x.probe["intent"]["farmer"]["inventory"].append(deepcopy(extra))
    open_trade(x, phase="farmer_confirm_submitted", offered=True)
    if fault == "premature_merchant":
        x.state["trade"]["accepted"] = True
    if fault == "missing_nonoffered":
        x.farmer["inventory"] = [i for i in x.farmer["inventory"] if i["uid"] != 300]
    if fault == "extra_nonoffered":
        x.farmer["inventory"].append({**extra, "uid": 301})
    assert not x.runtime.reconcile_probe_pair(
        "Dutch", x.farmer_read(), x.read(), now=x.now
    )
    assert x.calls == []


def false_pair(x, *, stale_request=False, observe_open=True):
    store = x.runtime.manual_sessions
    request = x.read()
    x.now += 0.2
    open_trade(x)
    x.now += 0.2
    if stale_request:
        merchant = store.begin_request("Dutch", request, now=x.now)
        x.now += 0.1
        if observe_open:
            store.observe(merchant["id"], x.read(), now=x.now)
    else:
        merchant = store.observe_target("Dutch", x.read(), now=x.now)
    x.now += 0.1
    accepted_at = x.probe["accepted_at"]
    open_trade(x, phase="offer_verified", offered=True)
    x.probe["accepted_at"] = accepted_at
    x.save()
    x.now += 0.1
    farmer = store.observe_target("Farmer", x.farmer_read(), now=x.now)
    x.runtime._sync_manual_fence()
    return farmer, merchant


@pytest.mark.parametrize(
    "stale_request,observe_open", [(False, True), (True, True), (True, False)]
)
def test_exact_false_pair_retracts_atomically_with_immutable_no_sale_audit(
    supervised, stale_request, observe_open
):
    x = supervised
    rows = false_pair(x, stale_request=stale_request, observe_open=observe_open)
    if stale_request and not observe_open:
        assert (
            x.runtime.manual_sessions.get(rows[1]["id"])["phase"] == "approval_pending"
        )
    x.now += 0.2
    assert x.runtime.reconcile_probe_pair("Dutch", x.farmer_read(), x.read(), now=x.now)
    assert x.runtime.manual_status() == [] and x.calls == []
    store = x.runtime.manual_sessions
    for row in rows:
        final = store.get(row["id"])
        assert not final["holds_automation"]
        assert final["terminal"]["trade_still_visible"] is True
        assert final["terminal"]["gameplay_input"] is False
        assert final["terminal"]["sales_receipt"] is False
    original_audit = store.audit()
    assert store.verify_audit()
    assert x.runtime.reconcile_probe_pair("Dutch", x.farmer_read(), x.read(), now=x.now)
    assert store.audit() == original_audit
    with store.db() as db:
        for table in (
            "sales",
            "manual_replans",
            "manual_decline_claims",
            "visitor_permissions",
        ):
            assert db.execute("SELECT COUNT(*) FROM " + table).fetchone()[0] == 0


@pytest.mark.parametrize(
    "fault",
    [
        "missing_evidence",
        "rollover",
        "stock_change",
        "predates",
        "decline_pending",
        "decline_claimed",
        "approved",
        "multiple_request",
        "journal_race",
        "write_failure",
    ],
)
@pytest.mark.parametrize("observe_open", [False, True])
def test_any_uncertain_side_rolls_back_both_retractions(
    supervised, monkeypatch, fault, observe_open
):
    x = supervised
    rows = false_pair(x, stale_request=True, observe_open=observe_open)
    farmer, merchant = rows
    store = x.runtime.manual_sessions
    if fault in ("missing_evidence", "rollover", "stock_change"):
        evidence = (
            {"reader_error": "missing"} if fault == "missing_evidence" else x.read()
        )
        if fault == "rollover":
            evidence["identity"]["creation_time_100ns"] += 1
        if fault == "stock_change":
            evidence["silver"] += 1
        store.observe(merchant["id"], evidence, now=x.now)
    if fault == "predates":
        x.probe["accepted_at"] = x.now
        x.probe["updated_at"] = x.now
        x.save()
    # These transitions are deliberately persisted using SQL to model a
    # competing actor racing the pair's SQLite transaction.
    if fault in ("decline_pending", "decline_claimed", "approved", "multiple_request"):
        with store.db() as db:
            if fault == "approved":
                db.execute(
                    "UPDATE manual_sessions SET ever_approved=1 WHERE id=?",
                    (merchant["id"],),
                )
                db.execute(
                    "UPDATE manual_requests SET state='approved' WHERE session_id=?",
                    (merchant["id"],),
                )
            elif fault == "multiple_request":
                db.execute(
                    "INSERT INTO manual_requests SELECT id||'2',session_id,fingerprint,binding_json,before_json,'pending',created_at,expires_at FROM manual_requests WHERE session_id=?",
                    (merchant["id"],),
                )
            else:
                db.execute(
                    "UPDATE manual_requests SET state=? WHERE session_id=?",
                    (fault, merchant["id"]),
                )
    if fault == "journal_race":
        calls = [0]

        def read():
            calls[0] += 1
            return (
                deepcopy(x.probe)
                if calls[0] < 3
                else {**x.probe, "phase": "delivery_verified"}
            )

        monkeypatch.setattr(probe, "read_probe", read)
    if fault == "write_failure":
        audit = store._audit
        count = [0]

        def fail(db, session, event, payload, now):
            count[0] += 1
            if count[0] == 2:
                raise BindingMismatch("injected second-side write failure")
            return audit(db, session, event, payload, now)

        monkeypatch.setattr(store, "_audit", fail)
    x.now += 0.2
    x.runtime.reconcile_probe_pair("Dutch", x.farmer_read(), x.read(), now=x.now)
    assert all(store.get(row["id"])["holds_automation"] for row in rows)
    assert not any(
        a["event"] == "manual_admission_retracted_bot_owned" for a in store.audit()
    )
    assert x.guard.manual_session_blocked("Farmer") and x.guard.manual_session_blocked(
        "Dutch"
    )
    assert x.calls == []


@pytest.mark.parametrize("role", ["farmer", "merchant"])
def test_no_request_approval_pending_cannot_be_retracted_as_stale_bot_request(
    supervised, role
):
    x = supervised
    farmer, merchant = false_pair(x)
    store = x.runtime.manual_sessions
    row = farmer if role == "farmer" else merchant
    with store.db() as db:
        db.execute(
            "UPDATE manual_sessions SET phase='approval_pending' WHERE id=?",
            (row["id"],),
        )
    x.runtime._sync_manual_fence()
    x.now += 0.2
    x.runtime.reconcile_probe_pair("Dutch", x.farmer_read(), x.read(), now=x.now)
    assert all(
        store.get(value["id"])["holds_automation"] for value in (farmer, merchant)
    )
    assert not any(
        event["event"] == "manual_admission_retracted_bot_owned"
        for event in store.audit()
    )
    assert x.calls == []


def test_pristine_farmer_request_is_never_the_merchant_stale_request_exception(
    supervised,
):
    x = supervised
    incoming = x.farmer_read()
    incoming["request"] = {
        "participant": "Dutch",
        "participant_uid": 123,
        "message": "Dutch wishes to trade with you.",
    }
    x.now += 0.2
    open_trade(x)
    x.now += 0.1
    store = x.runtime.manual_sessions
    farmer = store.begin_request("Farmer", incoming, now=x.now)
    merchant = store.observe_target("Dutch", x.read(), now=x.now)
    x.runtime._sync_manual_fence()
    x.now += 0.1
    x.runtime.reconcile_probe_pair("Dutch", x.farmer_read(), x.read(), now=x.now)
    assert all(store.get(row["id"])["holds_automation"] for row in (farmer, merchant))
    assert not any(
        event["event"] == "manual_admission_retracted_bot_owned"
        for event in store.audit()
    )
    assert x.calls == []
