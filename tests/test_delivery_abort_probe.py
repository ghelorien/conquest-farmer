from contextlib import nullcontext
from copy import deepcopy
from types import SimpleNamespace as NS

import pytest

from conquest.capture import CaptureUnavailable
from conquest.merchants import delivery_abort_probe as abort
from conquest.merchants import delivery_abort_sessions as sessions
from conquest.merchants import delivery_probe as probe
from conquest.merchants.manual_recovery import observe_rebaseline, start_rebaseline
from conquest.merchants.manual_sessions import BindingMismatch
from conquest.recovery_override import evidence_digest as digest
from test_delivery_probe_manual_ownership import supervised
from test_delivery_probe_trade_contention import false_pair
from test_manual_runtime import rig


@pytest.fixture
def incident(supervised, monkeypatch):
    x = supervised
    x.rows = false_pair(x, stale_request=True)
    # An unrelated booth sale makes ordinary delivery proof unavailable.
    x.state["booth"] = []
    x.state["silver"] += 100
    x.farmer["inventory"] = []  # The selected item resides in the trade deque.
    x.controls = {"enabled": False, "paused": False, "revision": 7}
    x.ui = NS(
        runtime=x.runtime,
        coordinator=x.guard,
        closed=False,
        safe_to_yield=lambda: True,
        app=NS(closing=False, control=NS(snapshot=lambda: dict(x.controls))),
        delivery_probe_thread=None,
    )
    x.driver.target = NS(hwnd=1234, snapshot=lambda: {"client_size": (1024, 768)})
    x.driver.observer = x.runtime.observers["Dutch"]
    x.driver.observer.operations = NS(target=x.driver.target)
    x.driver.memory.gui = NS(
        viewport_size=lambda: (1024, 768),
        assert_hovered=lambda *a: x.calls.append("hover"),
    )
    x.guard.owner_allowed = lambda character: abort.lease_authorized(x.ui, character)
    x.guard.on_acquire = lambda character: x.calls.append("focus")
    x.guard.on_release = lambda character: None
    x.journal.set("Dutch", "enabled", False)
    monkeypatch.setattr(abort, "pair", lambda *a, **kw: (x.farmer_read(), x.read()))
    monkeypatch.setattr("conquest.desktop_runtime.physical_coordinates", nullcontext)
    monkeypatch.setattr(
        "conquest.merchants.empty_delivery_cancel.control",
        lambda *a: ({"name": "Trade"}, (90, 12)),
    )
    monkeypatch.setattr(
        "conquest.merchants.driver.wait_hover_validation", lambda guard, check: guard()
    )
    monkeypatch.setattr("ctypes.windll.user32.GetAsyncKeyState", lambda key: 0)

    def close():
        x.farmer.update(
            trade=None,
            request=None,
            inventory=deepcopy(x.probe["intent"]["farmer"]["inventory"]),
        )
        x.state.update(trade=None, request=None)

    x.close = close

    def click(*a, **kw):
        assert abort.read()["phase"] == "cancel_submitted"
        kw["before_press"]()
        x.calls.append("close")
        close()

    monkeypatch.setattr("conquest.foreground.foreground_click", click)
    return x


def prepared(x):
    preview = abort.recheck(x.ui)
    value = {
        **preview,
        "phase": "abort_prepared",
        "operator": "Floor",
        "prepared_at": x.now,
    }
    abort.save(value)
    return value


def completed(x):
    value = prepared(x)
    abort.run(x.ui, value)
    assert abort.read()["phase"] == "cancel_verified"
    return abort.read()


def disposition(x):
    preview = abort.disposition_recheck(x.ui)
    return abort.disposition_override(
        x.ui,
        confirmation_reference=preview["confirmation_reference"],
        operator_confirmed=True,
        operator="Floor",
    )


def test_exact_abort_one_close_preserves_original_and_creates_no_delivery_or_sale(
    incident,
):
    x = incident
    original = digest(probe.read_probe())
    receipt = completed(x)
    assert x.calls == ["focus", "hover", "close"]
    assert receipt["probe_digest"] == original
    assert (
        receipt["baseline"]["merchant"]["silver"] == 1100
        and receipt["baseline"]["merchant"]["booth"] == []
    )
    assert receipt["after"]["farmer"]["inventory"][0]["uid"] == 99
    assert not receipt["sales_receipt"] and not receipt["delivery_receipt"]
    assert probe.read_probe()["phase"] == "cancel_verified"
    assert len(x.runtime.manual_status()) == 2
    assert x.guard._probe_abort_capability is None and not x.journal.get(
        "Dutch", "enabled"
    )
    with pytest.raises(ValueError, match="submitted"):
        abort.run(x.ui, receipt)
    assert x.calls.count("close") == 1


@pytest.mark.parametrize("role", ["farmer", "merchant"])
@pytest.mark.parametrize(
    "fault",
    [
        "accepted",
        "other_accepted",
        "silver",
        "reverse",
        "missing",
        "extra",
        "quantity",
        "process",
        "uid",
        "name",
        "server",
        "position",
        "capacity",
        "inventory",
        "request",
    ],
)
def test_abort_rejects_changed_terms_before_any_focus(incident, role, fault):
    x = incident
    value = x.farmer if role == "farmer" else x.state
    offer = "own_items" if role == "farmer" else "items"
    if fault in ("accepted", "other_accepted"):
        value["trade"][fault] = True
    if fault == "silver":
        value["trade"]["own_silver"] = 1
    if fault == "reverse":
        value["trade"]["items" if role == "farmer" else "own_items"] = deepcopy(
            x.probe["intent"]["items"]
        )
    if fault == "missing":
        value["trade"][offer] = []
    if fault == "extra":
        value["trade"][offer].append(
            {**deepcopy(x.probe["intent"]["items"][0]), "uid": 999}
        )
    if fault == "quantity":
        value["trade"][offer][0]["quantity"] += 1
    if fault == "process":
        value["identity"]["creation_time_100ns"] += 1
    if fault == "uid":
        value["character_uid"] += 1
    if fault == "name":
        value["character"] = "Other"
    if fault == "server":
        value["server"] = "Other"
    if fault == "position":
        value["position"][0] += 1
    if fault == "capacity":
        value["capacity"] -= 1
    if fault == "inventory":
        value["inventory"].append(
            {**deepcopy(x.probe["intent"]["items"][0]), "uid": 999}
        )
    if fault == "request":
        value["request"] = {"participant": "Other"}
    with pytest.raises(ValueError):
        abort.recheck(x.ui)
    assert x.calls == []


@pytest.mark.parametrize(
    "fault",
    [
        "probe",
        "profile",
        "phase",
        "baseline",
        "session",
        "approved",
        "claim",
        "permission",
        "extra_hold",
        "rebaseline",
        "reader_hold",
        "history_peer",
    ],
)
def test_abort_capability_never_bypasses_changed_journal_or_protected_holds(
    incident, fault
):
    x = incident
    value = prepared(x)
    store = x.runtime.manual_sessions
    merchant = x.rows[1]
    if fault == "probe":
        x.probe["updated_at"] += 0.1
        x.save()
    if fault == "profile":
        x.probe["target_profile_id"] = "Other"
        x.save()
    if fault == "phase":
        value["phase"] = "cancel_submitted"
        abort.save(value)
    if fault == "baseline":
        x.state["silver"] += 1
    if fault == "session":
        store.observe(merchant["id"], x.read(), now=x.now)
    if fault == "history_peer":
        snapshot = x.read()
        snapshot["trade"]["participant"] = "Other"
        store.observe(merchant["id"], snapshot, now=x.now)
    if fault in ("approved", "claim", "permission"):
        with store.db() as db:
            if fault == "approved":
                db.execute(
                    "UPDATE manual_sessions SET ever_approved=1 WHERE id=?",
                    (merchant["id"],),
                )
            if fault == "claim":
                db.execute(
                    "UPDATE manual_requests SET state='decline_claimed' WHERE session_id=?",
                    (merchant["id"],),
                )
            if fault == "permission":
                db.execute(
                    "INSERT INTO visitor_permissions VALUES('Dutch','Parasite','America',55,1,?)",
                    (x.now,),
                )
    if fault == "extra_hold":
        third = x.read()
        third["character"] = "Spiritual"
        third["character_uid"] = 999
        store.observe_target("Spiritual", third, now=x.now)
    if fault == "rebaseline":
        start_rebaseline(x.journal, "Spiritual", "other", now=x.now)
    if fault == "reader_hold":
        x.runtime.manual_reader_failure("Dutch", {}, "missing", now=x.now)
    x.runtime._sync_manual_fence()
    with pytest.raises(ValueError):
        abort.run(x.ui, value)
    assert x.calls == [] and x.guard._probe_abort_capability is None


@pytest.mark.parametrize(
    "fault",
    [
        "stop",
        "pause",
        "enable",
        "merchant_enable",
        "revision",
        "mouse",
        "F11",
        "F12",
        "expiry",
    ],
)
@pytest.mark.parametrize("boundary", ["before_lease", "before_press"])
def test_control_holds_override_abort_with_zero_press(
    incident, monkeypatch, fault, boundary
):
    x = incident
    value = prepared(x)

    def change():
        if fault == "stop":
            x.guard.stop()
        if fault == "pause":
            x.controls["paused"] = True
        if fault == "enable":
            x.controls["enabled"] = True
        if fault == "merchant_enable":
            x.journal.set("Dutch", "enabled", True)
        if fault == "revision":
            x.controls["revision"] += 1
        if fault == "mouse":
            x.guard.manual_active = lambda: True
        if fault == "expiry":
            x.now += 31
        if fault in ("F11", "F12"):
            key = 0x7A if fault == "F11" else 0x7B
            monkeypatch.setattr(
                "ctypes.windll.user32.GetAsyncKeyState",
                lambda k: 0x8000 if k == key else 0,
            )

    if boundary == "before_lease":
        change()
    else:

        def click(*a, **kw):
            change()
            kw["before_press"]()
            pytest.fail("No press may follow a control hold")

        monkeypatch.setattr("conquest.foreground.foreground_click", click)
    with pytest.raises(ValueError):
        abort.run(x.ui, value)
    assert "close" not in x.calls and x.guard._probe_abort_capability is None
    if boundary == "before_lease":
        assert x.calls == []


def test_abort_purpose_alone_and_another_thread_grant_nothing(incident):
    import threading

    x = incident
    value = prepared(x)
    assert x.guard.manual_session_blocked("Dutch", purpose=abort.PURPOSE)
    check = lambda: abort._authorization(
        x.ui, value, lambda: digest(value), submitted=lambda: False
    )
    with x.guard.probe_abort_scope(check):
        assert not x.guard.manual_session_blocked("Dutch", purpose=abort.PURPOSE)
        assert x.guard.manual_session_blocked("Dutch", purpose="trade")
        result = []
        worker = threading.Thread(
            target=lambda: result.append(
                x.guard.manual_session_blocked("Dutch", purpose=abort.PURPOSE)
            )
        )
        worker.start()
        worker.join(3)
        assert result == [True]
    assert x.guard.manual_session_blocked("Dutch", purpose=abort.PURPOSE)


def test_uncertain_click_is_never_retried_and_read_only_reconcile_recovers(
    incident, monkeypatch
):
    x = incident
    value = prepared(x)

    def lost(*a, **kw):
        kw["before_press"]()
        x.calls.append("close")
        x.close()
        raise OSError("lost acknowledgement")

    monkeypatch.setattr("conquest.foreground.foreground_click", lost)
    with pytest.raises(OSError):
        abort.run(x.ui, value)
    assert abort.read()["phase"] == "cancel_submitted"
    with pytest.raises(ValueError):
        abort.run(x.ui, abort.read())
    receipt = abort.reconcile(x.ui)
    assert receipt["phase"] == "cancel_verified" and x.calls.count("close") == 1


@pytest.mark.parametrize(
    "role,fault",
    [
        (role, fault)
        for role in ("farmer", "merchant")
        for fault in (
            "silver",
            "inventory",
            "booth",
            "price",
            "process",
            "trade",
            "request",
            "selected",
        )
        if (role, fault) != ("farmer", "price")
    ],
)
def test_close_verification_allows_only_exact_item_restoration(incident, role, fault):
    x = incident
    if fault == "price":
        x.state["booth"] = deepcopy(x.probe["intent"]["merchant"]["booth"])
    value = prepared(x)
    x.close()
    current = x.farmer if role == "farmer" else x.state
    if fault == "silver":
        current["silver"] += 1
    if fault == "inventory":
        current["inventory"].append(
            {**deepcopy(x.probe["intent"]["items"][0]), "uid": 999}
        )
    if fault == "booth":
        current["booth"] = [
            {**deepcopy(x.probe["intent"]["items"][0]), "uid": 999, "price": 999}
        ]
    if fault == "price":
        current["booth"][0]["price"] += 1
    if fault == "process":
        current["identity"]["creation_time_100ns"] += 1
    if fault == "trade":
        current["trade"] = {"participant": "Other"}
    if fault == "request":
        current["request"] = {"participant": "Other"}
    if fault == "selected":
        if role == "farmer":
            current["inventory"] = []
        else:
            current["inventory"] += deepcopy(x.probe["intent"]["items"])
    with pytest.raises(ValueError):
        abort.closed(value, x.farmer_read(), x.read(), now=x.now)


def test_second_confirmation_atomically_overrides_then_two_samples_rebaseline(incident):
    x = incident
    completed(x)
    with pytest.raises(ValueError):
        abort.require_rebaseline(x.ui, probe.read_probe())
    preview = abort.disposition_recheck(x.ui)
    args = dict(
        confirmation_reference=preview["confirmation_reference"],
        operator_confirmed=True,
        operator="Floor",
    )
    result = abort.disposition_override(x.ui, **args)
    assert result == abort.disposition_override(x.ui, **args)
    assert set(result["session_ids"]) == {row["id"] for row in x.rows}
    assert all(row["rebaseline"] for row in x.runtime.manual_status())
    assert x.runtime.manual_sessions.verify_audit()
    with pytest.raises(ValueError):
        abort.require_rebaseline(x.ui, probe.read_probe())
    for name, snapshot, role in [
        ("Farmer", x.farmer_read(), "Farmer"),
        ("Dutch", x.read(), "Merchant"),
    ]:
        observe_rebaseline(x.journal, name, snapshot, now=x.now, target_role=role)
    x.now += 4.9
    for name, snapshot, role in [
        ("Farmer", x.farmer_read(), "Farmer"),
        ("Dutch", x.read(), "Merchant"),
    ]:
        observe_rebaseline(x.journal, name, snapshot, now=x.now, target_role=role)
    with pytest.raises(ValueError):
        abort.require_rebaseline(x.ui, probe.read_probe())
    x.now += 0.2
    for name, snapshot, role in [
        ("Farmer", x.farmer_read(), "Farmer"),
        ("Dutch", x.read(), "Merchant"),
    ]:
        observe_rebaseline(x.journal, name, snapshot, now=x.now, target_role=role)
    x.runtime._sync_manual_fence()
    abort.require_rebaseline(x.ui, probe.read_probe())
    with x.journal.db() as db:
        assert (
            db.execute(
                "SELECT count(*) FROM events WHERE event='sales_observation_gap'"
            ).fetchone()[0]
            == 2
        )
        assert db.execute("SELECT count(*) FROM sales_baseline").fetchone()[0] == 2
    assert x.calls.count("close") == 1


@pytest.mark.parametrize("fault", ["append", "second_write", "probe", "permission"])
def test_disposition_races_rollback_both_and_never_release_input(
    incident, monkeypatch, fault
):
    x = incident
    completed(x)
    preview = abort.disposition_recheck(x.ui)
    if fault == "append":
        snapshot = x.farmer_read()
        snapshot["inventory"][0]["quantity"] += 1
        x.runtime.manual_sessions.observe(x.rows[0]["id"], snapshot, now=x.now)
    if fault == "probe":
        state = probe.read_probe()
        state["error"] = "changed"
        probe.write_probe(probe.JOURNAL, state)
    if fault == "permission":
        with x.runtime.manual_sessions.db() as db:
            db.execute(
                "INSERT INTO visitor_permissions VALUES('Dutch','Parasite','America',55,1,?)",
                (x.now,),
            )
    if fault == "second_write":
        original = x.runtime.manual_sessions._audit
        count = [0]

        def fail(*a, **kw):
            count[0] += 1
            if count[0] == 2:
                raise ValueError("second write failed")
            return original(*a, **kw)

        monkeypatch.setattr(x.runtime.manual_sessions, "_audit", fail)
    with pytest.raises(ValueError):
        abort.disposition_override(
            x.ui,
            confirmation_reference=preview["confirmation_reference"],
            operator_confirmed=True,
            operator="Floor",
        )
    assert all(
        x.runtime.manual_sessions.get(row["id"])["phase"] == "needs_attention"
        for row in x.rows
    )
    with x.journal.db() as db:
        assert db.execute("SELECT count(*) FROM manual_rebaseline").fetchone()[0] == 0
    assert x.guard.manual_session_blocked("Farmer") and x.guard.manual_session_blocked(
        "Dutch"
    )


def test_restart_before_first_baseline_sample_cannot_adopt_another_process(incident):
    x = incident
    completed(x)
    disposition(x)
    x.farmer["identity"]["creation_time_100ns"] += 1
    observe_rebaseline(
        x.journal, "Farmer", x.farmer_read(), now=x.now, target_role="Farmer"
    )
    with x.journal.db() as db:
        assert (
            db.execute(
                "SELECT phase FROM manual_rebaseline WHERE target_profile_id='Farmer'"
            ).fetchone()[0]
            == "needs_attention"
        )
    with pytest.raises(ValueError):
        abort.require_rebaseline(x.ui, probe.read_probe())


@pytest.mark.parametrize("field", ["silver", "booth"])
@pytest.mark.parametrize("boundary", ["before_press", "after_press"])
def test_merchant_baseline_drift_is_not_allowed_after_preparation(
    incident, monkeypatch, field, boundary
):
    x = incident
    value = prepared(x)

    def change():
        if field == "silver":
            x.state["silver"] += 1
        else:
            x.state["booth"] = deepcopy(x.probe["intent"]["merchant"]["booth"])

    def click(*a, **kw):
        if boundary == "before_press":
            change()
        kw["before_press"]()
        x.calls.append("close")
        x.close()
        if boundary == "after_press":
            change()

    monkeypatch.setattr("conquest.foreground.foreground_click", click)
    with pytest.raises(ValueError):
        abort.run(x.ui, value)
    assert x.calls.count("close") == int(boundary == "after_press")
    assert (
        abort.read()["phase"] == "cancel_submitted"
        and probe.read_probe()["phase"] == "offer_verified"
    )


@pytest.mark.parametrize(
    "fault", ["peer", "approval", "permission", "decline", "rollover"]
)
def test_preexisting_genuine_history_cannot_be_reclassified(incident, fault):
    x = incident
    store = x.runtime.manual_sessions
    merchant = x.rows[1]
    if fault in ("peer", "rollover"):
        current = x.read()
        if fault == "peer":
            current["trade"]["participant_uid"] += 1
        else:
            current["identity"]["creation_time_100ns"] += 1
        store.observe(merchant["id"], current, now=x.now)
    else:
        with store.db() as db:
            if fault == "approval":
                store._audit(db, merchant["id"], "visitor_approved", {}, x.now)
            if fault == "decline":
                db.execute(
                    "UPDATE manual_requests SET state='decline_claimed' WHERE session_id=?",
                    (merchant["id"],),
                )
            if fault == "permission":
                db.execute(
                    "INSERT INTO visitor_permissions VALUES('Dutch','Parasite','America',55,1,?)",
                    (x.now,),
                )
    with pytest.raises(ValueError):
        abort.recheck(x.ui)
    assert x.calls == []


def test_unknown_reader_errors_remain_audited_as_unknown_not_delivery(incident):
    x = incident
    x.runtime.manual_sessions.observe(
        x.rows[1]["id"], {"reader_error": "unavailable"}, now=x.now
    )
    x.runtime._sync_manual_fence()
    completed(x)
    disposition(x)
    assert all(
        x.runtime.manual_sessions.get(row["id"])["terminal"]["historical_outcome"]
        == "unknown"
        for row in x.rows
    )
    with x.journal.db() as db:
        for table in ("sales", "delivery_admissions", "transactions"):
            assert db.execute("SELECT count(*) FROM " + table).fetchone()[0] == 0


def test_pre_submit_crash_can_only_retry_after_new_confirmed_preview(
    incident, monkeypatch
):
    x = incident
    old = prepared(x)
    old["error"] = "lost host before click"
    abort.save(old)
    preview = abort.recheck(x.ui)
    assert preview["prior_prepared_digest"] == digest(old)
    submitted = []
    # Real start still launches a worker; replace only its body to inspect the
    # durable new attempt without sending gameplay input.
    monkeypatch.setattr(abort, "run", lambda ui, value: submitted.append(value))
    result = abort.start(
        x.ui,
        confirmation_reference=preview["confirmation_reference"],
        operator_confirmed=True,
        operator="Floor",
    )
    x.ui.delivery_probe_thread.join(3)
    assert result["started"] and len(submitted) == 1 and x.calls == []
    assert list((probe.JOURNAL.parent / "delivery-request-probe-audit").glob("*.json"))
    retry = abort.read()
    retry.update(phase="cancel_submitted", submitted_at=x.now)
    abort.save(retry)
    with pytest.raises(ValueError, match="one-shot"):
        abort.recheck(x.ui)


def native_window_geometry(x):
    # GuiReader.windows returns struct.unpack tuples, not JSON lists.
    for snapshot in (x.farmer, x.state):
        snapshot["windows"] = [
            {
                "name": "Trade",
                "address": 123456,
                "geometry": (10.0, 20.0, 300.0, 400.0),
                "scroll": (0.0, 0.0),
            }
        ]


def test_async_abort_uses_durable_representation_of_native_geometry(incident):
    x = incident
    native_window_geometry(x)
    preview = abort.recheck(x.ui)
    result = abort.start(
        x.ui,
        confirmation_reference=preview["confirmation_reference"],
        operator_confirmed=True,
        operator="Floor",
    )
    x.ui.delivery_probe_thread.join(10)
    assert result["started"] and not x.ui.delivery_probe_thread.is_alive()
    receipt = abort.read()
    assert receipt["phase"] == "cancel_verified" and not receipt.get("error")
    for role in ("farmer", "merchant"):
        window = receipt["baseline"][role]["windows"][0]
        assert window["geometry"] == [10.0, 20.0, 300.0, 400.0] and window[
            "scroll"
        ] == [0.0, 0.0]
    assert x.calls == ["focus", "hover", "close"]
    assert probe.read_probe()["phase"] == "cancel_verified"


@pytest.mark.parametrize("journal_changed", [False, True])
def test_async_worker_failure_persists_across_representation_only_difference(
    incident, monkeypatch, journal_changed
):
    x = incident
    native_window_geometry(x)
    preview = abort.recheck(x.ui)

    def fail(ui, value):
        assert value == abort.read()  # The worker starts with the durable shape.
        window = value["baseline"]["merchant"]["windows"][0]
        window.update(
            geometry=tuple(window["geometry"]), scroll=tuple(window["scroll"])
        )
        assert value != abort.read() and digest(value) == digest(abort.read())
        if journal_changed:
            saved = abort.read()
            saved["operator"] = "Different operator"
            abort.save(saved)
        raise ValueError("Pre-submit native observation failure")

    monkeypatch.setattr(abort, "run", fail)
    abort.start(
        x.ui,
        confirmation_reference=preview["confirmation_reference"],
        operator_confirmed=True,
        operator="Floor",
    )
    x.ui.delivery_probe_thread.join(10)
    assert not x.ui.delivery_probe_thread.is_alive() and x.calls == []
    receipt = abort.read()
    assert receipt["phase"] == "abort_prepared" and "submitted_at" not in receipt
    if journal_changed:
        assert receipt["operator"] == "Different operator" and "error" not in receipt
    else:
        assert (
            receipt["error"] == "Pre-submit native observation failure"
            and receipt["failed_at"] == x.now
        )


@pytest.mark.parametrize(
    "fault", ["unconfirmed", "digest", "expired", "extra", "operator"]
)
def test_explicit_bridge_confirmation_schema_and_expiry(incident, fault):
    x = incident
    preview = abort.recheck(x.ui)
    body = dict(
        action="probe-delivery-abort-start",
        operator_confirmed=True,
        operator="Floor",
        confirmation_reference=preview["confirmation_reference"],
    )
    if fault == "unconfirmed":
        body["operator_confirmed"] = False
    if fault == "digest":
        body["confirmation_reference"] = "other"
    if fault == "expired":
        x.now += 31
    if fault == "extra":
        body["gameplay_input"] = True
    if fault == "operator":
        body["operator"] = ""
    with pytest.raises(ValueError):
        abort.dispatch(x.ui, body)
    assert x.ui.delivery_probe_thread is None and x.calls == []


def test_terminal_normalizes_stale_failure_without_destroying_original(incident):
    x = incident
    x.probe.update(error="old confirmation failure", finished_at=x.now)
    x.save()
    completed(x)
    state = probe.read_probe()
    assert (
        state["error"] is None
        and state["finished_at"] == state["abort_receipt"]["verified_at"]
    )
    assert state["original_failure"]["error"] == "old confirmation failure"
    assert state["abort_receipt"]["probe"]["error"] == "old confirmation failure"


@pytest.mark.parametrize("crash", ["sidecar", "main"])
def test_abort_terminal_publication_is_crash_retryable_without_input(
    incident, monkeypatch, crash
):
    x = incident
    value = prepared(x)
    original = probe.write_probe

    def write(path, value):
        if str(path) == str(probe.JOURNAL) and value.get("phase") == "cancel_verified":
            if crash == "main":
                original(path, value)
            raise OSError("power loss publishing terminal")
        return original(path, value)

    monkeypatch.setattr(probe, "write_probe", write)
    with pytest.raises(OSError):
        abort.run(x.ui, value)
    assert abort.read()["phase"] == "cancel_verified"
    monkeypatch.setattr(probe, "write_probe", original)
    abort.reconcile(x.ui)
    assert (
        probe.read_probe()["phase"] == "cancel_verified" and x.calls.count("close") == 1
    )


def test_large_history_is_compact_and_hot_checks_never_scan_snapshot_json(
    incident, monkeypatch
):
    import json
    from contextlib import contextmanager

    x = incident
    store = x.runtime.manual_sessions
    with store.db() as db:
        for index in range(10000):
            row = x.rows[index % 2]
            snapshot = x.farmer_read() if index % 2 == 0 else x.read()
            store._evidence(
                db,
                row["id"],
                snapshot,
                x.now,
                sessions.canonical_ownership(snapshot, require_closed=False),
            )
    value = prepared(x)
    assert len(json.dumps(value)) < 60000
    assert abort.path().stat().st_size < 60000
    assert (
        sum(record["evidence"]["count"] for record in value["sessions"]["records"])
        >= 10000
    )
    statements = []
    original = store.db

    @contextmanager
    def traced():
        with original() as db:
            db.set_trace_callback(statements.append)
            yield db

    monkeypatch.setattr(store, "db", traced)
    monkeypatch.setattr(
        sessions,
        "capture",
        lambda *a, **kw: pytest.fail("Hot check must not rescan immutable snapshots"),
    )
    for _ in range(3):
        sessions.check_binding(x.runtime, value["sessions"])
    assert not any(
        "snapshot_json" in statement.lower()
        or "select * from manual_evidence" in statement.lower()
        for statement in statements
    )


@pytest.mark.parametrize(
    "fault", ["accepted", "offered_other", "missing_uid", "request_message"]
)
def test_same_peer_but_different_pre_preview_trade_history_is_protected(
    incident, fault
):
    x = incident
    sample = x.read()
    if fault == "accepted":
        sample["trade"]["accepted"] = True
    if fault == "offered_other":
        sample["trade"]["items"][0]["uid"] += 1
    if fault == "missing_uid":
        sample["trade"].pop("participant_uid")
    if fault == "request_message":
        sample.update(
            trade=None,
            request={
                "participant": "Parasite",
                "participant_uid": 55,
                "message": "Changed request",
            },
        )
    x.runtime.manual_sessions.observe(x.rows[1]["id"], sample, now=x.now)
    with pytest.raises(ValueError):
        abort.recheck(x.ui)


def test_later_booth_sale_is_unknown_disposition_baseline_not_a_changed_abort_receipt(
    incident,
):
    x = incident
    receipt = completed(x)
    receipt_digest = digest(receipt)
    x.state["silver"] += 200
    x.state["booth"] = deepcopy(x.probe["intent"]["merchant"]["booth"])
    assert digest(abort.reconcile(x.ui)) == receipt_digest
    preview = abort.disposition_recheck(x.ui)
    x.state["silver"] += 1
    with pytest.raises(ValueError, match="closed ownership changed"):
        abort.disposition_override(
            x.ui,
            confirmation_reference=preview["confirmation_reference"],
            operator_confirmed=True,
            operator="Floor",
        )
    preview = abort.disposition_recheck(x.ui)
    abort.disposition_override(
        x.ui,
        confirmation_reference=preview["confirmation_reference"],
        operator_confirmed=True,
        operator="Floor",
    )
    assert digest(abort.read()) == receipt_digest


def surface_ui(x):
    from unittest.mock import Mock
    import threading

    identity = deepcopy(x.state["identity"])

    def host(hwnd, identity):
        return NS(
            saved=NS(hwnd=hwnd, identity=identity),
            mode="owned",
            resize=Mock(),
            api=NS(assert_owner=Mock(), show_async=Mock()),
        )

    x.host = host(1234, identity)
    x.sibling = host(999, {"pid": 9})
    x.ui.hosts = {"Dutch": x.host, "Spiritual": x.sibling}
    x.ui.frames = {"Dutch": "dutch-frame"}
    x.ui.notebook = NS(select=Mock())
    x.ui.detail_tabs = {"Dutch": NS(select=Mock())}
    x.ui.root = NS(update_idletasks=Mock())
    x.ui.client_panes = {
        "Dutch": NS(winfo_width=lambda: 1200, winfo_height=lambda: 800)
    }

    class UIQueue:
        def put(self, entry):
            callback, done, result = entry

            def work():
                try:
                    callback()
                except Exception as error:
                    result["error"] = str(error)
                finally:
                    done.set()

            thread = threading.Thread(target=work)
            thread.start()

    x.ui.ui_requests = UIQueue()
    x.ui.grant_fence = None


def test_real_prepare_input_abort_path_uses_offer_verified_not_accept_binding(
    incident, monkeypatch
):
    from conquest.merchants.ui import UnifiedUI

    x = incident
    surface_ui(x)
    monkeypatch.setattr(
        "conquest.merchants.ui.wait_for_merchant_surface", lambda *a: None
    )
    x.guard.on_acquire = lambda character: UnifiedUI.prepare_input(x.ui, character)
    completed(x)
    x.host.resize.assert_called_once_with(1200, 800)
    x.sibling.api.show_async.assert_called_once_with(999, 0)
    assert x.calls == ["hover", "close"]


@pytest.mark.parametrize(
    "fault",
    [
        "observer",
        "controller",
        "target",
        "host",
        "viewport",
        "sibling",
        "uuid",
        "name",
        "server",
        "disabled",
        "scope",
    ],
)
def test_abort_surface_binding_preflights_all_ownership_before_any_native_presentation(
    incident, monkeypatch, fault
):
    import threading
    from conquest.merchants.delivery_abort_surface import prepare

    x = incident
    surface_ui(x)
    prepared(x)
    capability = (threading.get_ident(), lambda: {"Dutch", "Farmer"})
    x.guard._probe_abort_capability = capability
    x.guard.owner = "Dutch"
    x.guard.thread = capability[0]
    x.guard.purpose = abort.PURPOSE
    if fault == "observer":
        x.driver.observer.adapter.identity = {**x.state["identity"], "pid": 80}
    if fault == "controller":
        x.driver.observer = NS(adapter=NS(identity=x.state["identity"]))
    if fault == "target":
        x.driver.target = NS(hwnd=1234)
    if fault == "host":
        x.host.saved.hwnd += 1
    if fault == "viewport":
        x.ui.client_panes["Dutch"].winfo_width = lambda: 500
    if fault == "sibling":
        from unittest.mock import Mock

        bad = NS(
            saved=NS(hwnd=998, identity={"pid": 90}),
            mode="owned",
            resize=Mock(),
            api=NS(
                assert_owner=Mock(side_effect=ValueError("foreign sibling")),
                show_async=Mock(),
            ),
        )
        x.ui.hosts["Third"] = bad
    if fault in ("uuid", "name", "server", "disabled"):
        profile = NS(id="Dutch", name="Dutch", server="America", local_enabled=True)
        if fault == "uuid":
            profile.id = "other-uuid"
        if fault == "name":
            profile.name = "Other"
        if fault == "server":
            profile.server = "Europe"
        if fault == "disabled":
            profile.local_enabled = False
        monkeypatch.setattr(
            "conquest.character_context.registry",
            lambda: NS(resolve=lambda *a, **kw: profile),
        )
    if fault == "scope":
        x.guard._probe_abort_capability = None
    try:
        with pytest.raises(ValueError):
            prepare(x.ui, "Dutch", capability)
        x.host.resize.assert_not_called()
        x.sibling.api.show_async.assert_not_called()
    finally:
        x.guard._probe_abort_capability = None
        x.guard.owner = x.guard.thread = x.guard.purpose = None


def test_observational_suffix_between_abort_preview_and_start_is_prefix_bound(
    incident, monkeypatch
):
    x = incident
    preview = abort.recheck(x.ui)
    for _ in range(4):
        x.now += 0.1
        x.runtime.manual_sessions.observe(x.rows[0]["id"], x.farmer_read(), now=x.now)
        x.runtime.manual_sessions.observe(x.rows[1]["id"], x.read(), now=x.now)
    x.runtime._sync_manual_fence()
    started = []
    monkeypatch.setattr(abort, "run", lambda ui, value: started.append(value))
    abort.start(
        x.ui,
        confirmation_reference=preview["confirmation_reference"],
        operator_confirmed=True,
        operator="Floor",
    )
    x.ui.delivery_probe_thread.join(3)
    assert len(started) == 1
    assert (
        started[0]["confirmed_session_history_digest"] == preview["sessions"]["digest"]
    )
    assert started[0]["sessions"]["digest"] != preview["sessions"]["digest"]
    assert x.calls == []


@pytest.mark.parametrize("fault", ["accepted", "foreign", "changed_offer", "approval"])
def test_harmful_suffix_cannot_be_refresh_authority(incident, fault):
    x = incident
    preview = abort.recheck(x.ui)
    snapshot = x.read()
    if fault == "accepted":
        snapshot["trade"]["accepted"] = True
    if fault == "foreign":
        snapshot["trade"]["participant_uid"] += 1
    if fault == "changed_offer":
        snapshot["trade"]["items"][0]["quantity"] += 1
    if fault == "approval":
        with x.runtime.manual_sessions.db() as db:
            db.execute(
                "UPDATE manual_sessions SET ever_approved=1 WHERE id=?",
                (x.rows[1]["id"],),
            )
    else:
        x.runtime.manual_sessions.observe(x.rows[1]["id"], snapshot, now=x.now)
    x.runtime._sync_manual_fence()
    with pytest.raises(ValueError):
        abort.start(
            x.ui,
            confirmation_reference=preview["confirmation_reference"],
            operator_confirmed=True,
            operator="Floor",
        )
    assert x.calls == []


def test_closed_observation_suffix_between_disposition_preview_and_confirm_is_prefix_bound(
    incident,
):
    x = incident
    completed(x)
    preview = abort.disposition_recheck(x.ui)
    for _ in range(4):
        x.now += 0.1
        x.runtime.manual_sessions.observe(x.rows[0]["id"], x.farmer_read(), now=x.now)
        x.runtime.manual_sessions.observe(x.rows[1]["id"], x.read(), now=x.now)
    x.runtime._sync_manual_fence()
    result = abort.disposition_override(
        x.ui,
        confirmation_reference=preview["confirmation_reference"],
        operator_confirmed=True,
        operator="Floor",
    )
    assert result["phase"] == "operator_overridden"
    for row in x.rows:
        terminal = x.runtime.manual_sessions.get(row["id"])["terminal"]
        assert terminal["session_history_digest"] == preview["sessions"]["digest"]
        assert terminal["prepared_history_digest"] != terminal["session_history_digest"]


def test_successful_disposition_retry_survives_expiry_without_reapplying_or_input(
    incident,
):
    x = incident
    completed(x)
    preview = abort.disposition_recheck(x.ui)
    args = dict(
        confirmation_reference=preview["confirmation_reference"],
        operator_confirmed=True,
        operator="Floor",
    )
    first = abort.disposition_override(x.ui, **args)
    audit = len(x.runtime.manual_sessions.audit())
    x.now += 100
    assert abort.disposition_override(x.ui, **args) == first
    assert (
        len(x.runtime.manual_sessions.audit()) == audit and x.calls.count("close") == 1
    )


@pytest.mark.parametrize("fault", ["ownership", "error_authority", "audit_chain"])
def test_corrupt_evidence_or_audit_digest_cannot_prepare_abort(incident, fault):
    x = incident
    store = x.runtime.manual_sessions
    merchant = x.rows[1]["id"]
    # Append malformed records through the raw DB, never defeat immutable
    # triggers by modifying/deleting historical records.
    with store.db() as db:
        if fault == "audit_chain":
            db.execute(
                "INSERT INTO manual_audit(session_id,event,at,payload_json,previous_digest,digest) VALUES(?,'needs_attention',?,'{}','bad','bad')",
                (merchant, x.now),
            )
        else:
            sample = x.read()
            proof = sessions.canonical_ownership(sample, require_closed=False)
            store._evidence(
                db,
                merchant,
                sample,
                x.now,
                proof if fault == "error_authority" else None,
                error="reader error" if fault == "error_authority" else None,
            )
    with pytest.raises(ValueError):
        abort.recheck(x.ui)
    assert x.calls == []


@pytest.mark.parametrize("entry", ["capture", "binding", "refresh"])
def test_full_history_capture_keeps_one_snapshot_while_observer_appends(
    incident, monkeypatch, entry
):
    import threading

    x = incident
    store = x.runtime.manual_sessions
    before = sessions.binding(x.runtime, x.probe)
    reading, appended = threading.Event(), threading.Event()
    errors = []
    clock_values = []
    original = sessions._audit_chain

    def pause(db):
        assert db.in_transaction
        reading.set()
        assert appended.wait(5)
        original(db)

    monkeypatch.setattr(sessions, "_audit_chain", pause)

    def writer():
        try:
            assert reading.wait(5)
            x.now += 1
            store.observe(x.rows[0]["id"], x.farmer_read(), now=x.now)
            # This also changes session reason/updated_at and the audit chain:
            # returned hold views must come from the original snapshot too.
            store.observe(
                x.rows[1]["id"], {"reader_error": "concurrent observer"}, now=x.now
            )
        except BaseException as error:
            errors.append(error)
        finally:
            appended.set()

    worker = threading.Thread(target=writer)
    worker.start()

    def clock():
        clock_values.append(x.now)
        return x.now

    try:
        if entry == "capture":
            with store.db() as db:
                records = sessions.capture(store, db, x.probe, clock=clock)
            assert records == before["records"]
        else:
            current = (
                sessions.binding(x.runtime, x.probe, clock=clock)
                if entry == "binding"
                else sessions.refresh_binding(x.runtime, x.probe, before, clock=clock)
            )
            assert current == before
    finally:
        worker.join(5)
    assert not worker.is_alive() and errors == []
    assert len(clock_values) == 1 and clock_values[0] < x.now
    monkeypatch.setattr(sessions, "_audit_chain", original)
    refreshed = sessions.refresh_binding(x.runtime, x.probe, before)
    assert refreshed["digest"] != before["digest"]
    assert (
        sum(record["evidence"]["count"] for record in refreshed["records"])
        == sum(record["evidence"]["count"] for record in before["records"]) + 2
    )
    assert x.calls == []


def test_capture_clock_is_chosen_after_first_read_pins_snapshot(incident, monkeypatch):
    import threading
    from contextlib import contextmanager

    x = incident
    store = x.runtime.manual_sessions
    original = store.db
    before = sessions.binding(x.runtime, x.probe)
    waiting, appended = threading.Event(), threading.Event()
    pinned = [False]
    errors = []
    reader_id = threading.get_ident()

    class Connection:
        def __init__(self, db):
            self.db = db

        def __getattr__(self, name):
            return getattr(self.db, name)

        def execute(self, sql, *args):
            if (
                sql == "SELECT id FROM manual_sessions ORDER BY id LIMIT 1"
                and threading.get_ident() == reader_id
            ):
                assert self.db.in_transaction
                waiting.set()
                assert appended.wait(5)
                result = self.db.execute(sql, *args)
                pinned[0] = True
                return result
            return self.db.execute(sql, *args)

    @contextmanager
    def wrapped():
        with original() as db:
            yield Connection(db)

    monkeypatch.setattr(store, "db", wrapped)

    def writer():
        try:
            assert waiting.wait(5)
            x.now += 1
            store.observe(x.rows[0]["id"], x.farmer_read(), now=x.now)
        except BaseException as error:
            errors.append(error)
        finally:
            appended.set()

    worker = threading.Thread(target=writer)
    worker.start()

    def clock():
        assert pinned[0]
        return x.now

    try:
        current = sessions.binding(x.runtime, x.probe, clock=clock)
    finally:
        worker.join(5)
    assert not worker.is_alive() and errors == []
    assert current["digest"] != before["digest"]
    assert (
        sum(record["evidence"]["count"] for record in current["records"])
        == sum(record["evidence"]["count"] for record in before["records"]) + 1
    )


def test_disposition_clock_follows_blocked_immediate_transaction_acquisition(
    incident, monkeypatch
):
    import threading
    from contextlib import contextmanager

    x = incident
    receipt = completed(x)
    store = x.runtime.manual_sessions
    expected = sessions.binding(x.runtime, x.probe, closed_after=receipt["verified_at"])
    original = store.db
    attempting = threading.Event()
    errors = []
    results = []
    clock_values = []
    worker = None

    class Connection:
        def __init__(self, db):
            self.db = db

        def __getattr__(self, name):
            return getattr(self.db, name)

        def execute(self, sql, *args):
            if sql == "BEGIN IMMEDIATE" and threading.current_thread() is worker:
                attempting.set()
            return self.db.execute(sql, *args)

    @contextmanager
    def wrapped():
        with original() as db:
            yield Connection(db)

    monkeypatch.setattr(store, "db", wrapped)

    def clock():
        clock_values.append(x.now)
        return x.now

    def dispose():
        try:
            results.append(
                sessions.disposition(
                    x.runtime,
                    x.probe,
                    receipt,
                    expected,
                    confirmation_reference="snapshot-race",
                    operator="Floor",
                    recheck=lambda: None,
                    clock=clock,
                )
            )
        except BaseException as error:
            errors.append(error)

    old = x.now
    with original() as db:
        db.execute("BEGIN IMMEDIATE")
        worker = threading.Thread(target=dispose)
        worker.start()
        assert attempting.wait(5)
        x.now += 1
        sample = x.farmer_read()
        store._evidence(
            db, x.rows[0]["id"], sample, x.now, sessions.canonical_ownership(sample)
        )
    worker.join(5)
    assert not worker.is_alive() and errors == [] and len(results) == 1
    assert clock_values == [x.now] and x.now > old
    for row in x.rows:
        assert store.get(row["id"])["terminal"]["at"] == x.now
    assert x.calls.count("close") == 1  # Disposition never sends another input.


def interrupted_close(x, monkeypatch):
    x.now += 1
    value = prepared(x)

    def interrupted(*args, **kwargs):
        kwargs["before_press"]()
        x.calls.append("close")
        x.close()
        raise CaptureUnavailable("Stop/read failure before closure verification")

    monkeypatch.setattr("conquest.foreground.foreground_click", interrupted)
    with pytest.raises(CaptureUnavailable):
        abort.run(x.ui, value)
    assert abort.read()["phase"] == "cancel_submitted"
    assert probe.read_probe()["phase"] == "offer_verified"
    return abort.read()["submitted_at"]


@pytest.mark.parametrize("offset", [0, 0.2])
def test_verified_late_abort_accepts_exact_closed_history_since_submission(
    incident, monkeypatch, offset
):
    x = incident
    submitted = interrupted_close(x, monkeypatch)
    x.now = submitted + 0.5
    for row, snapshot in ((x.rows[0], x.farmer_read()), (x.rows[1], x.read())):
        snapshot["timestamp"] = submitted + offset
        x.runtime.manual_sessions.observe(row["id"], snapshot, now=x.now)
    x.runtime._sync_manual_fence()
    x.now = submitted + 2
    receipt = abort.reconcile(x.ui)
    assert receipt["verified_at"] > submitted + offset
    result = disposition(x)
    assert result["phase"] == "operator_overridden" and x.calls.count("close") == 1


def test_verified_late_abort_rejects_closed_history_before_submission(
    incident, monkeypatch
):
    x = incident
    submitted = interrupted_close(x, monkeypatch)
    x.now = submitted + 0.5
    snapshot = x.farmer_read()
    snapshot["timestamp"] = submitted - 0.1
    x.runtime.manual_sessions.observe(x.rows[0]["id"], snapshot, now=x.now)
    x.runtime._sync_manual_fence()
    x.now = submitted + 2
    abort.reconcile(x.ui)
    with pytest.raises(ValueError, match="predates"):
        abort.disposition_recheck(x.ui)
    assert all(
        x.runtime.manual_sessions.get(row["id"])["phase"] == "needs_attention"
        for row in x.rows
    )
    assert x.calls.count("close") == 1


def test_submitted_but_unverified_abort_cannot_authorize_disposition(
    incident, monkeypatch
):
    x = incident
    submitted = interrupted_close(x, monkeypatch)
    x.now = submitted + 0.5
    x.runtime.manual_sessions.observe(x.rows[0]["id"], x.farmer_read(), now=x.now)
    with pytest.raises(ValueError, match="Verified abort receipt"):
        abort.disposition_recheck(x.ui)
    expected = sessions.binding(x.runtime, x.probe, closed_after=submitted)
    with pytest.raises(ValueError, match="verified chronological"):
        sessions.disposition(
            x.runtime,
            x.probe,
            abort.read(),
            expected,
            confirmation_reference="invalid",
            operator="Floor",
            recheck=lambda: None,
        )
    assert all(
        x.runtime.manual_sessions.get(row["id"])["phase"] == "needs_attention"
        for row in x.rows
    )


@pytest.mark.parametrize("role", ["farmer", "merchant"])
def test_post_submit_closed_history_with_unrelated_inventory_change_is_still_protected(
    incident, monkeypatch, role
):
    x = incident
    submitted = interrupted_close(x, monkeypatch)
    x.now = submitted + 0.5
    snapshot = x.farmer_read() if role == "farmer" else x.read()
    snapshot["inventory"].append(
        {**deepcopy(x.probe["intent"]["items"][0]), "uid": 9876}
    )
    row = x.rows[0] if role == "farmer" else x.rows[1]
    x.runtime.manual_sessions.observe(row["id"], snapshot, now=x.now)
    x.runtime._sync_manual_fence()
    x.now = submitted + 2
    abort.reconcile(x.ui)
    with pytest.raises(ValueError, match="unrelated ownership"):
        abort.disposition_recheck(x.ui)
    assert all(
        x.runtime.manual_sessions.get(row["id"])["phase"] == "needs_attention"
        for row in x.rows
    )
