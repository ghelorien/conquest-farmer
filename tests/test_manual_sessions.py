from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import asdict
import json
import sqlite3
import threading

import pytest

from conquest.merchants.manual_sessions import (
    BindingMismatch,
    ManualSessionError,
    ManualSessionStore,
    VisitorKey,
    canonical_ownership,
    ownership_digest,
)


def item(uid=100, **changes):
    return dict(
        uid=uid,
        type_id=410009,
        plus=1,
        gem1=0,
        gem2=0,
        quantity=1,
        bound=False,
        name="SuperBlade",
        **changes,
    )


def snapshot(at=100, *, request=True, **changes):
    value = dict(
        identity={
            "pid": 12,
            "creation_time_100ns": 12345,
            "path": "C:/Game/ImConquer.exe",
            "architecture": "x64",
        },
        character="Spiritual",
        character_uid=456,
        server="America",
        timestamp=at,
        hp=100,
        position=[200, 201],
        map_id=1036,
        capacity=40,
        silver=1000,
        inventory=[item()],
        booth=[item(101, price=200)],
        booth_open=True,
        own_booth_uid=456,
        request={
            "participant": "Visitor",
            "participant_uid": 789,
            "message": "Visitor wishes to trade with you.",
        }
        if request
        else None,
        trade=None,
    )
    value.update(changes)
    return value


@pytest.fixture
def store(tmp_path):
    return ManualSessionStore(tmp_path / "journal.sqlite3")


def pending(store, **kwargs):
    return store.begin_request("merchant-id", snapshot(), now=100, **kwargs)


def activate(store):
    row = pending(store)
    return store.allow_and_activate(
        row["approval_binding"], snapshot(101), operator="Floor", now=101
    )


def test_visitor_key_normalizes_native_profile_names_without_using_hidden_profile_ids():
    from conquest.character_context import ProfileName

    fields = dict(
        target_profile_id=ProfileName("merchant-uuid", "not-the-target"),
        visitor_name=ProfileName("Visitor", "not-a-game-uid"),
        visitor_server=ProfileName("America", "not-a-server"),
        visitor_uid=789,
    )
    key = VisitorKey(**fields)
    expected = dict(
        target_profile_id="merchant-uuid",
        visitor_name="Visitor",
        visitor_server="America",
        visitor_uid=789,
    )
    assert asdict(key) == expected and asdict(deepcopy(key)) == expected
    assert all(
        type(getattr(key, field)) is str
        for field in ("target_profile_id", "visitor_name", "visitor_server")
    )
    assert key == VisitorKey(**expected) and hash(key) == hash(VisitorKey(**expected))
    assert (
        fields["visitor_name"].profile_id == "not-a-game-uid"
    )  # Runtime identity was not changed.


@pytest.mark.parametrize(
    "field", ["target_profile_id", "visitor_name", "visitor_server"]
)
@pytest.mark.parametrize("value", [None, 77, "", " padded "])
def test_visitor_text_normalization_never_coerces_invalid_values(field, value):
    fields = dict(
        target_profile_id="merchant-id",
        visitor_name="Visitor",
        visitor_server="America",
        visitor_uid=789,
    )
    fields[field] = value
    with pytest.raises(ManualSessionError):
        VisitorKey(**fields)


def test_native_profile_names_preserve_exact_request_permission_and_session_identity(
    store,
):
    from conquest.character_context import ProfileName

    target = "7b82943d-55f7-4368-a65c-d256efc9c5d0"

    def native(at):
        value = snapshot(
            at,
            character=ProfileName("Spiritual", target),
            server=ProfileName("America", "unused-server-id"),
        )
        value["request"]["participant"] = ProfileName(
            "Visitor", "untrusted-local-profile-id"
        )
        return value

    row = store.begin_request(target, native(100), now=100)
    assert (
        row["target_profile_id"] == target
        and row["character"]["character"] == "Spiritual"
    )
    expected = dict(
        target_profile_id=target,
        visitor_name="Visitor",
        visitor_server="America",
        visitor_uid=789,
    )
    assert row["visitor"] == expected and row["approval_binding"]["visitor"] == expected
    active = store.allow_and_activate(
        row["approval_binding"], native(101), operator="Floor", now=101
    )
    assert active["phase"] == "manual_active"
    assert store.allowed(
        VisitorKey(
            target, ProfileName("Visitor", "different-untrusted-id"), "America", 789
        )
    )
    for change in (
        {"target_profile_id": "another-target-uuid"},
        {"target_profile_id": "Spiritual"},
        {"visitor_name": "visitor"},
        {"visitor_server": "Europe"},
        {"visitor_uid": 790},
    ):
        assert not store.allowed(VisitorKey(**{**expected, **change}))
    store.revoke(
        VisitorKey(
            target, ProfileName("Visitor", "untrusted-local-profile-id"), "America", 789
        ),
        operator="Floor",
        now=102,
    )
    assert (
        not store.allowed(expected) and store.get(row["id"])["phase"] == "manual_active"
    )
    assert store.verify_audit()


def test_native_profile_name_open_trade_admission_keeps_distinct_target_and_visitor(
    store,
):
    from conquest.character_context import ProfileName

    value = snapshot(
        100,
        request=False,
        character=ProfileName("Spiritual", "merchant-id"),
        trade={
            "participant": ProfileName("Visitor", "other-id"),
            "participant_uid": 789,
        },
    )
    row = store.observe_target("merchant-id", value, now=100)
    assert (
        row["phase"] == "needs_attention" and row["target_profile_id"] == "merchant-id"
    )
    assert row["visitor"] == dict(
        target_profile_id="merchant-id",
        visitor_name="Visitor",
        visitor_server="America",
        visitor_uid=789,
    )
    assert not store.permissions() and store.verify_audit()


def test_allow_is_exact_machine_local_and_atomic(store):
    row = activate(store)
    assert row["phase"] == "manual_active"
    assert row["holds_automation"] is True
    key = VisitorKey("merchant-id", "Visitor", "America", 789)
    assert store.allowed(key)
    for field, value in [
        ("target_profile_id", "other"),
        ("visitor_name", "visitor"),
        ("visitor_server", "Europe"),
        ("visitor_uid", 790),
    ]:
        fields = dict(
            target_profile_id="merchant-id",
            visitor_name="Visitor",
            visitor_server="America",
            visitor_uid=789,
        )
        fields[field] = value
        assert not store.allowed(fields)
    assert len(store.permissions()) == 1
    with store.db() as db:
        assert not db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='trusted_sources'"
        ).fetchone()
    assert ManualSessionStore(store.path.parent / "other.sqlite3").permissions() == []


@pytest.mark.parametrize(
    "field",
    [
        "session_id",
        "request_id",
        "target_profile_id",
        "visitor",
        "game_process_identity",
        "character_identity",
        "request_fingerprint",
        "evidence_digest",
        "ownership_digest",
    ],
)
def test_each_approval_binding_component_must_match(store, field):
    row = pending(store)
    binding = deepcopy(row["approval_binding"])
    binding[field] = "different"
    with pytest.raises(ManualSessionError):
        store.allow_and_activate(binding, snapshot(101), operator="Floor", now=101)
    assert store.permissions() == []
    assert store.get(row["id"])["phase"] == "approval_pending"


@pytest.mark.parametrize(
    "change",
    [
        "visitor_name",
        "visitor_uid",
        "visitor_server",
        "process",
        "character",
        "character_uid",
        "quantity",
        "bound",
        "booth_price",
        "silver",
        "capacity",
        "request_missing",
        "trade_open",
    ],
)
def test_live_approval_rejects_changed_identity_or_ownership(store, change):
    row = pending(store)
    current = snapshot(101)
    if change == "visitor_name":
        current["request"].update(
            participant="Another", message="Another wishes to trade with you."
        )
    elif change == "visitor_uid":
        current["request"]["participant_uid"] = 790
    elif change == "visitor_server":
        current["request"]["server"] = "Europe"
    elif change == "process":
        current["identity"]["creation_time_100ns"] += 1
    elif change == "character":
        current["character"] = "Dutch"
    elif change == "character_uid":
        current["character_uid"] += 1
    elif change in ("quantity", "bound"):
        current["inventory"][0][change] = 2 if change == "quantity" else True
    elif change == "booth_price":
        current["booth"][0]["price"] += 1
    elif change in ("silver", "capacity"):
        current[change] -= 1
    elif change == "request_missing":
        current["request"] = None
    else:
        current["trade"] = {"participant": "Visitor", "participant_uid": 789}
    with pytest.raises(ManualSessionError):
        store.allow_and_activate(
            row["approval_binding"], current, operator="Floor", now=101
        )
    assert not store.permissions()


def test_activation_repeated_is_idempotent_and_requires_live_request(store):
    row = activate(store)
    result = store.allow_and_activate(
        row["approval_binding"], snapshot(102), operator="Floor", now=102
    )
    assert result["id"] == row["id"]
    assert (
        len([a for a in store.audit(row["id"]) if a["event"] == "request_approved"])
        == 1
    )
    with pytest.raises(ManualSessionError):
        store.allow_and_activate(
            row["approval_binding"],
            snapshot(103, request=False),
            operator="Floor",
            now=103,
        )


def test_permission_cannot_activate_wrong_visitor_and_revocation_is_audited(store):
    row = pending(store)
    with pytest.raises(ManualSessionError, match="permission"):
        store.activate_allowed(row["approval_binding"], snapshot(101), now=101)
    row = store.allow_and_activate(
        row["approval_binding"], snapshot(101), operator="Floor", now=101
    )
    store.revoke(row["visitor"], operator="Floor", now=102)
    assert not store.allowed(row["visitor"])
    assert store.get(row["id"])["phase"] == "manual_active"  # No retroactive input.
    assert store.audit()[-1]["event"] == "visitor_revoked"


@pytest.mark.parametrize("decision", ["timeout", "reject", "late_allow"])
def test_decline_intent_and_claim_are_exactly_once_across_restart(store, decision):
    row = pending(store, timeout_seconds=5)
    if decision == "timeout":
        for _ in range(2):
            row = store.expire(row["id"], now=105)
    elif decision == "reject":
        for _ in range(2):
            row = store.reject(row["approval_binding"], operator="Floor", now=105)
    else:
        row = store.allow_and_activate(
            row["approval_binding"], snapshot(105), operator="Floor", now=105
        )
    assert row["request_state"] == "decline_pending"
    assert not store.permissions()
    with pytest.raises(BindingMismatch):
        store.allow_and_activate(
            row["approval_binding"], snapshot(106), operator="Floor", now=106
        )
    claim = store.claim_decline(row["id"], snapshot(106), now=106)
    assert claim["request_id"] == row["current_request_id"]
    restarted = ManualSessionStore(store.path)
    assert restarted.claim_decline(row["id"], snapshot(107), now=107) is None
    observed = restarted.resume(row["id"], snapshot(108, request=False), now=108)
    assert observed["phase"] == "settlement_observed"
    observed = restarted.observe(row["id"], snapshot(113, request=False), now=113)
    assert observed["phase"] == "declined_verified"
    assert observed["terminal"]["sales_receipt"] is False
    with store.db() as db:
        assert db.execute("SELECT count(*) FROM manual_declines").fetchone()[0] == 1
        assert (
            db.execute("SELECT count(*) FROM manual_decline_claims").fetchone()[0] == 1
        )


def test_timeout_does_not_expire_early_and_cannot_race_approval(store):
    row = pending(store, timeout_seconds=10)
    assert store.expire(row["id"], now=109)["request_state"] == "pending"
    barrier = threading.Barrier(2)

    def approve():
        barrier.wait()
        try:
            return store.allow_and_activate(
                row["approval_binding"], snapshot(110), operator="Floor", now=110
            )
        except BindingMismatch:
            return None

    def expire():
        barrier.wait()
        return store.expire(row["id"], now=110)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(approve), pool.submit(expire)]
        for future in futures:
            future.result()
    assert store.get(row["id"])["request_state"] == "decline_pending"
    assert store.permissions() == []
    assert len([a for a in store.audit() if a["event"] == "decline_requested"]) == 1


def test_concurrent_allow_and_reject_have_one_winner(store):
    row = pending(store)
    barrier = threading.Barrier(2)

    def decide(allow):
        barrier.wait()
        try:
            if allow:
                store.allow_and_activate(
                    row["approval_binding"], snapshot(101), operator="Floor", now=101
                )
            else:
                store.reject(row["approval_binding"], operator="Floor", now=101)
        except BindingMismatch:
            pass

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(decide, [True, False]))
    state = store.get(row["id"])["request_state"]
    assert state in ("approved", "decline_pending")
    assert bool(store.permissions()) == (state == "approved")
    assert (
        len(
            [
                a
                for a in store.audit()
                if a["event"] in ("request_approved", "decline_requested")
            ]
        )
        == 1
    )


def test_decline_claim_revalidates_live_request(store):
    row = pending(store)
    store.reject(row["approval_binding"], operator="Floor", now=101)
    changed = snapshot(102)
    changed["request"]["participant_uid"] += 1
    with pytest.raises(BindingMismatch):
        store.claim_decline(row["id"], changed, now=102)
    with store.db() as db:
        assert (
            db.execute("SELECT count(*) FROM manual_decline_claims").fetchone()[0] == 0
        )


def test_ownership_digest_ignores_volatile_fields_but_retains_full_evidence(store):
    row = activate(store)
    first = snapshot(102, request=False)
    later = snapshot(107, request=False, hp=75, position=[202, 210], map_id=1002)
    later["inventory"][0]["name"] = "Cosmetic label"
    assert ownership_digest(first) == ownership_digest(later)
    assert store.observe(row["id"], first, now=102)["phase"] == "settlement_observed"
    terminal = store.observe(row["id"], later, now=107)
    assert terminal["phase"] == "completed"
    assert terminal["holds_automation"] is False
    assert store.evidence(row["id"])[-1]["snapshot"]["hp"] == 75
    assert store.evidence(row["id"])[-1]["snapshot"]["position"] == [202, 210]


@pytest.mark.parametrize(
    "change",
    [
        "quantity",
        "bound",
        "gem1",
        "gem2",
        "plus",
        "uid",
        "type_id",
        "price",
        "silver",
        "capacity",
        "own_booth_uid",
        "booth_open",
    ],
)
def test_ownership_changes_restart_stabilization(store, change):
    row = activate(store)
    store.observe(row["id"], snapshot(102, request=False), now=102)
    changed = snapshot(107, request=False)
    if change in ("quantity", "gem1", "gem2", "plus", "uid", "type_id"):
        changed["inventory"][0][change] += 10 if change == "uid" else 1
    elif change == "bound":
        changed["inventory"][0]["bound"] = True
    elif change == "price":
        changed["booth"][0]["price"] += 1
    elif change == "booth_open":
        changed["booth_open"] = False
    else:
        changed[change] -= 1
    result = store.observe(row["id"], changed, now=107)
    assert result["phase"] == "settlement_observed"
    assert result["stable_since"] == 107
    changed["timestamp"] = 112
    assert store.observe(row["id"], changed, now=112)["phase"] == "completed"


def test_two_distinct_observations_at_least_five_seconds_apart(store):
    row = activate(store)
    for at in (102, 102, 106.999):
        assert (
            store.observe(row["id"], snapshot(at, request=False), now=at)["phase"]
            == "settlement_observed"
        )
    assert (
        store.observe(row["id"], snapshot(107, request=False), now=107)["phase"]
        == "completed"
    )


def test_new_approved_request_resets_stabilization(store):
    row = activate(store)
    store.observe(row["id"], snapshot(102, request=False), now=102)
    new = store.begin_request(
        "merchant-id", snapshot(106), session_id=row["id"], now=106
    )
    assert new["current_request_id"] != row["current_request_id"]
    assert new["stable_since"] is None
    with pytest.raises(BindingMismatch):
        store.allow_and_activate(
            row["approval_binding"], snapshot(106), operator="Floor", now=106
        )
    store.activate_allowed(new["approval_binding"], snapshot(107), now=107)
    assert (
        store.observe(row["id"], snapshot(108, request=False), now=108)["phase"]
        == "settlement_observed"
    )
    assert (
        store.observe(row["id"], snapshot(112, request=False), now=112)["phase"]
        == "settlement_observed"
    )
    assert (
        store.observe(row["id"], snapshot(113, request=False), now=113)["phase"]
        == "completed"
    )


def test_same_process_restart_resumes_existing_stabilization(store):
    row = activate(store)
    store.observe(row["id"], snapshot(102, request=False), now=102)
    restarted = ManualSessionStore(store.path)
    assert (
        restarted.resume(row["id"], snapshot(107, request=False), now=107)["phase"]
        == "completed"
    )


@pytest.mark.parametrize(
    "change",
    [
        "process",
        "character_uid",
        "missing_inventory",
        "missing_request",
        "missing_bound",
        "missing_capacity",
        "missing_process_birth",
        "stale",
        "none",
    ],
)
def test_missing_evidence_and_rollover_stay_needs_attention_across_restart(
    store, change
):
    row = activate(store)
    current = snapshot(102, request=False)
    if change == "process":
        current["identity"]["creation_time_100ns"] += 1
    elif change == "character_uid":
        current["character_uid"] += 1
    elif change == "missing_bound":
        del current["inventory"][0]["bound"]
    elif change == "missing_process_birth":
        del current["identity"]["creation_time_100ns"]
    elif change.startswith("missing_"):
        del current[change.removeprefix("missing_")]
    elif change == "stale":
        current["timestamp"] = 98
    else:
        current = None
    result = store.resume(row["id"], current, now=102)
    assert result["phase"] == "needs_attention"
    assert result["holds_automation"] is True
    assert (
        ManualSessionStore(store.path).observe(
            row["id"], snapshot(110, request=False), now=110
        )["phase"]
        == "needs_attention"
    )


def test_unapproved_open_trade_never_becomes_manual_authority(store):
    row = pending(store)
    trade = {"participant": "Visitor", "participant_uid": 789}
    result = store.observe(
        row["id"], snapshot(101, request=False, trade=trade), now=101
    )
    assert result["phase"] == "needs_attention"
    assert not store.permissions()


def test_unapproved_open_trade_without_session_is_quarantined(store):
    result = store.observe_target(
        "merchant-id",
        snapshot(
            100, request=False, trade={"participant": "Visitor", "participant_uid": 789}
        ),
        now=100,
    )
    assert result["phase"] == "needs_attention"
    assert result["ever_approved"] is False
    assert store.active("merchant-id")["id"] == result["id"]


def test_approved_open_trade_must_match_exact_visitor(store):
    row = activate(store)
    good = snapshot(
        102, request=False, trade={"participant": "Visitor", "participant_uid": 789}
    )
    assert store.observe(row["id"], good, now=102)["phase"] == "manual_active"
    bad = snapshot(
        103, request=False, trade={"participant": "Visitor", "participant_uid": 790}
    )
    assert store.observe(row["id"], bad, now=103)["phase"] == "needs_attention"


def test_withdrawal_requires_unchanged_ownership_and_two_observations(store):
    row = pending(store)
    assert (
        store.observe(row["id"], snapshot(101, request=False), now=101)["phase"]
        == "settlement_observed"
    )
    assert (
        store.observe(row["id"], snapshot(106, request=False), now=106)["phase"]
        == "request_withdrawn"
    )


def test_unapproved_ownership_change_is_not_a_verified_decline(store):
    row = pending(store)
    store.reject(row["approval_binding"], operator="Floor", now=101)
    store.claim_decline(row["id"], snapshot(102), now=102)
    assert (
        store.observe(row["id"], snapshot(103, request=False, silver=2000), now=103)[
            "phase"
        ]
        == "needs_attention"
    )


def test_terminal_audit_and_evidence_are_immutable(store):
    row = activate(store)
    store.observe(row["id"], snapshot(102, request=False), now=102)
    terminal = store.observe(row["id"], snapshot(107, request=False), now=107)
    counts = (len(store.audit()), len(store.evidence(row["id"])))
    assert store.observe(row["id"], None, now=108) == terminal
    assert (len(store.audit()), len(store.evidence(row["id"]))) == counts
    for statement in (
        "UPDATE manual_evidence SET snapshot_json='{}'",
        "DELETE FROM manual_evidence",
        "UPDATE manual_audit SET event='changed'",
        "DELETE FROM manual_audit",
        "UPDATE manual_sessions SET phase='manual_active'",
        "DELETE FROM manual_sessions",
        "UPDATE manual_requests SET binding_json='{}'",
        "DELETE FROM manual_requests",
    ):
        with pytest.raises(sqlite3.IntegrityError):
            with store.db() as db:
                db.execute(statement)
    previous = ""
    for event in store.audit():
        assert event["previous_digest"] == previous
        previous = event["digest"]
    assert store.verify_audit()


def test_booth_stock_does_not_consume_bag_capacity():
    current = snapshot(
        request=False,
        inventory=[item(uid) for uid in range(1, 31)],
        booth=[item(uid, price=100) for uid in range(101, 121)],
        capacity=40,
    )
    assert len(canonical_ownership(current)["inventory"]) == 30
    assert ownership_digest(current)


@pytest.mark.parametrize(
    "trade",
    [
        {},
        {"participant": "Visitor"},
        {"participant": "Visitor", "participant_uid": None},
        {"participant_uid": 789},
    ],
)
def test_unknown_open_trade_visitor_is_durably_quarantined(store, trade):
    result = store.observe_target(
        "merchant-id", snapshot(100, request=False, trade=trade), now=100
    )
    assert result["phase"] == "needs_attention"
    assert result["visitor"] is None
    assert result["holds_automation"] is True
    assert not store.permissions()
    assert ManualSessionStore(store.path).active("merchant-id")["id"] == result["id"]


def test_incomplete_ownership_during_unapproved_trade_is_quarantined(store):
    current = snapshot(
        100, request=False, trade={"participant": "Visitor", "participant_uid": 789}
    )
    del current["inventory"]
    result = store.observe_target("merchant-id", current, now=100)
    assert result["phase"] == "needs_attention"
    assert store.evidence(result["id"])[-1]["error"]


def test_nonfinite_reader_result_persists_attention(store):
    row = activate(store)
    result = store.observe(row["id"], snapshot(float("nan"), request=False), now=102)
    assert result["phase"] == "needs_attention"
    assert "invalid_evidence_repr" in store.evidence(row["id"])[-1]["snapshot"]


def test_override_is_idempotent_disposition_and_never_sale(store):
    row = activate(store)
    store.observe(row["id"], None, now=102)
    args = dict(
        confirmation_reference="incident-1",
        operator="Floor",
        reason="Reviewed exact inventory",
        now=103,
    )
    terminal = store.operator_override(row["id"], **args)
    assert terminal["phase"] == "operator_overridden"
    assert terminal["terminal"]["sales_receipt"] is False
    assert store.operator_override(row["id"], **args) == terminal
    with pytest.raises(ManualSessionError):
        store.operator_override(
            row["id"], **{**args, "confirmation_reference": "incident-2"}
        )


def test_pending_and_terminal_intervals_are_exposed_for_sales_exclusion(store):
    row = activate(store)
    assert len(store.overlaps("merchant-id", 90, 110)) == 1
    assert store.overlaps("other", 90, 110) == []
    store.observe(row["id"], snapshot(102, request=False), now=102)
    store.observe(row["id"], snapshot(107, request=False), now=107)
    assert len(store.overlaps("merchant-id", 105, 108)) == 1
    assert store.overlaps("merchant-id", 108, 110) == []


def test_settlement_hook_commits_sales_state_and_audit_together(store):
    with store.db() as db:
        db.execute(
            "CREATE TABLE integration_receipts(session_id TEXT PRIMARY KEY, digest TEXT)"
        )
    calls = []

    def hook(db, session, evidence, receipt):
        assert session["phase"] == "completed"
        assert evidence["timestamp"] == 107
        assert session["terminal"]["ownership_delta"] == receipt["ownership_delta"]
        durable = db.execute(
            "SELECT terminal_json FROM manual_sessions WHERE id=?", (session["id"],)
        ).fetchone()[0]
        assert json.loads(durable)["ownership_delta"] == receipt["ownership_delta"]
        audited = db.execute(
            "SELECT payload_json FROM manual_audit WHERE session_id=? AND event='session_terminal'",
            (session["id"],),
        ).fetchone()[0]
        assert json.loads(audited)["ownership_delta"] == receipt["ownership_delta"]
        db.execute(
            "INSERT INTO integration_receipts VALUES(?,?)",
            (session["id"], receipt["ownership_digest"]),
        )
        calls.append(session["id"])

    integrated = ManualSessionStore(store.path, on_settlement=hook)
    row = activate(integrated)
    integrated.observe(row["id"], snapshot(102, request=False), now=102)
    integrated.observe(row["id"], snapshot(107, request=False), now=107)
    integrated.observe(row["id"], snapshot(108, request=False), now=108)
    assert calls == [row["id"]]
    with store.db() as db:
        assert (
            db.execute("SELECT session_id FROM integration_receipts").fetchone()[0]
            == row["id"]
        )


def test_settlement_hook_failure_rolls_back_terminal_audit_evidence_and_external_writes(
    store,
):
    with store.db() as db:
        db.execute("CREATE TABLE integration_receipts(session_id TEXT)")
    observed_deltas = []

    def hook(db, session, evidence, receipt):
        db.execute("INSERT INTO integration_receipts VALUES(?)", (session["id"],))
        observed_deltas.append(receipt["ownership_delta"])
        raise RuntimeError("sales write failed")

    integrated = ManualSessionStore(store.path, on_settlement=hook)
    row = activate(integrated)
    integrated.observe(row["id"], snapshot(102, request=False, silver=1250), now=102)
    count = len(integrated.evidence(row["id"]))
    with pytest.raises(RuntimeError, match="sales write"):
        integrated.observe(
            row["id"], snapshot(107, request=False, silver=1250), now=107
        )
    assert integrated.get(row["id"])["phase"] == "settlement_observed"
    assert integrated.get(row["id"])["terminal"] is None
    assert len(integrated.evidence(row["id"])) == count
    assert not any(a["event"] == "session_terminal" for a in integrated.audit())
    with store.db() as db:
        assert (
            db.execute("SELECT count(*) FROM integration_receipts").fetchone()[0] == 0
        )
    retried = store.observe(
        row["id"], snapshot(108, request=False, silver=1250), now=108
    )
    assert retried["terminal"]["ownership_delta"] == observed_deltas[0]
    assert retried["terminal"]["ownership_delta"]["silver"]["delta"] == 250
    assert store.verify_audit()


def test_terminal_delta_records_exact_added_removed_changed_and_moved_items(store):
    first = snapshot(
        100,
        inventory=[item(100), item(102), item(103)],
        booth=[item(101, price=200), item(104, price=500)],
    )
    row = store.begin_request("merchant-id", first, now=100)
    binding = row["approval_binding"]
    store.allow_and_activate(
        binding, {**first, "timestamp": 101}, operator="Floor", now=101
    )
    final = snapshot(
        102,
        request=False,
        inventory=[
            item(200),
            {**item(102), "quantity": 2, "bound": True, "gem1": 13},
            item(101),
        ],
        booth=[item(104, price=600), {**item(103, price=300), "plus": 2}],
        silver=1250,
    )
    store.observe(row["id"], final, now=102)
    terminal = store.observe(row["id"], {**final, "timestamp": 107}, now=107)[
        "terminal"
    ]
    delta = terminal["ownership_delta"]
    before = canonical_ownership(first, require_closed=False)
    after = canonical_ownership(final)
    assert terminal["baseline_request_id"] == binding["request_id"]
    assert terminal["baseline_evidence_digest"] == binding["evidence_digest"]
    assert delta["after_ownership_digest"] == terminal["ownership_digest"]
    assert delta["before_ownership_digest"] == ownership_digest(
        {**first, "request": None}
    )
    assert delta["items"]["added"] == [
        {"location": "inventory", "item": after["inventory"][2]}
    ]
    assert delta["items"]["removed"] == [
        {"location": "inventory", "item": before["inventory"][0]}
    ]
    changed = delta["items"]["changed"]
    assert [change["uid"] for change in changed] == [102, 104]
    assert changed[0] == {
        "uid": 102,
        "before": {"location": "inventory", "item": before["inventory"][1]},
        "after": {"location": "inventory", "item": after["inventory"][1]},
        "changed_fields": ["bound", "gem1", "quantity"],
    }
    assert changed[1]["before"]["item"]["price"] == 500
    assert changed[1]["after"]["item"]["price"] == 600
    assert changed[1]["changed_fields"] == ["price"]
    moved = delta["items"]["moved"]
    assert moved == [
        {
            "uid": 101,
            "before": {"location": "booth", "item": before["booth"][0]},
            "after": {"location": "inventory", "item": after["inventory"][0]},
            "changed_fields": ["price"],
        },
        {
            "uid": 103,
            "before": {"location": "inventory", "item": before["inventory"][2]},
            "after": {"location": "booth", "item": after["booth"][0]},
            "changed_fields": ["plus", "price"],
        },
    ]
    assert terminal["sales_receipt"] is False
    audited = [
        entry
        for entry in store.audit(row["id"])
        if entry["event"] == "session_terminal"
    ][0]
    assert audited["payload"]["ownership_delta"] == delta
    assert store.verify_audit()


def test_terminal_delta_records_silver_capacity_and_booth_state_without_attribution(
    store,
):
    row = activate(store)
    final = snapshot(
        102,
        request=False,
        silver=500,
        capacity=39,
        booth_open=False,
        own_booth_uid=0,
        inventory=[item(100), item(101)],
        booth=[],
    )
    store.observe(row["id"], final, now=102)
    delta = store.observe(row["id"], {**final, "timestamp": 107}, now=107)["terminal"][
        "ownership_delta"
    ]
    assert delta["silver"] == {"before": 1000, "after": 500, "delta": -500}
    assert delta["capacity"] == {"before": 40, "after": 39, "delta": -1}
    assert delta["booth_state"] == {
        "before": {"booth_open": True, "own_booth_uid": 456},
        "after": {"booth_open": False, "own_booth_uid": 0},
    }
    assert set(delta) == {
        "version",
        "before_ownership_digest",
        "after_ownership_digest",
        "items",
        "silver",
        "capacity",
        "booth_state",
    }


@pytest.mark.parametrize("approved", [False, True])
def test_unchanged_terminal_delta_is_explicit_and_empty(store, approved):
    row = activate(store) if approved else pending(store)
    store.observe(row["id"], snapshot(102, request=False), now=102)
    terminal = store.observe(row["id"], snapshot(107, request=False), now=107)[
        "terminal"
    ]
    delta = terminal["ownership_delta"]
    assert delta["before_ownership_digest"] == delta["after_ownership_digest"]
    assert delta["items"] == {"added": [], "removed": [], "changed": [], "moved": []}
    assert delta["silver"] == {"before": 1000, "after": 1000, "delta": 0}
    assert delta["capacity"] == {"before": 40, "after": 40, "delta": 0}
    assert delta["booth_state"]["before"] == delta["booth_state"]["after"]


def test_delta_keeps_session_start_across_additional_approved_requests(store):
    row = activate(store)
    first_request = row["current_request_id"]
    interim = snapshot(
        102, request=False, inventory=[item(100), item(200)], silver=1250
    )
    store.observe(row["id"], interim, now=102)
    new_request = {**interim, "timestamp": 103, "request": snapshot()["request"]}
    later = store.begin_request(
        "merchant-id", new_request, session_id=row["id"], now=103
    )
    store.activate_allowed(
        later["approval_binding"], {**new_request, "timestamp": 104}, now=104
    )
    store.observe(row["id"], {**interim, "timestamp": 105}, now=105)
    terminal = store.observe(row["id"], {**interim, "timestamp": 110}, now=110)[
        "terminal"
    ]
    assert terminal["baseline_request_id"] == first_request
    assert [
        entry["item"]["uid"] for entry in terminal["ownership_delta"]["items"]["added"]
    ] == [200]
    assert terminal["ownership_delta"]["silver"] == {
        "before": 1000,
        "after": 1250,
        "delta": 250,
    }


def test_canonical_ownership_requires_explicit_windows_and_exact_items():
    current = snapshot(request=False)
    assert canonical_ownership(current)["request"] is None
    del current["trade"]
    with pytest.raises(ManualSessionError):
        ownership_digest(current)
    current = snapshot(request=False)
    current["booth"][0]["uid"] = current["inventory"][0]["uid"]
    with pytest.raises(ManualSessionError, match="inventory and booth"):
        ownership_digest(current)
