import json
from types import SimpleNamespace as NS
import pytest
from conquest import meteor_banking as meteor, stored_scroll_queue as queue
from conquest.discord_notify import write_json, read_json
from conquest.merchants import delivery_route


def stored(uid=100, at=1000):
    return {
        "phase": "completed",
        "scroll_uid": uid,
        "exchange_verified": True,
        "market_verified_at": at,
        "completed_at": at + 10,
        "origin": 1011,
        "receipts": [{"stored": uid, "type_id": 720027, "verified_in_warehouse": True}],
        "before": {"identity": {"pid": 7}},
        "after": {"items": [{"uid": uid, "type_id": 720027}]},
    }


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(meteor, "JOURNAL", tmp_path / "meteor.json")
    monkeypatch.setattr(meteor, "POLICY", tmp_path / "policy.json")
    monkeypatch.setattr(delivery_route, "STATE", tmp_path / "delivery.json")
    monkeypatch.setattr(meteor, "_promoted_scroll_delivery", lambda *a, **kw: False)
    now = [3000]
    monkeypatch.setattr(meteor.time, "time", lambda: now[0])
    return now


def test_consolidation_captures_old_scroll_before_replacing_completed_journal(
    setup, monkeypatch
):
    write_json(meteor.JOURNAL, stored())
    write_json(
        meteor.POLICY,
        {
            "enabled": True,
            "qualified": True,
            "exchange": {"fee": 0},
            "origins": {
                "1011": {
                    "outbound": {"verified": True, "fare": 0},
                    "return": {"verified": True, "fare": 0},
                }
            },
        },
    )
    items = [
        {"uid": n, "type_id": 1088001, "amount": 1, "limit": 1} for n in range(1, 11)
    ]

    def town(action):
        if action == "supplies":
            return {"items": items, "silver": 100000, "capacity": 40}
        if action == "warehouse-items":
            return {"items": []}
        pytest.fail("Unexpected input " + action)

    loop = NS(
        town=town,
        living=lambda: {"embedded_controls": {"life": {"map_id": 1011}}},
        record=lambda *a, **kw: None,
    )

    def resume(loop):
        assert read_json(meteor.JOURNAL)["phase"] == "withdrawing"
        assert queue.pending(meteor.JOURNAL)[0]["evidence"] == stored()
        raise OSError("crash after replacement before input")

    monkeypatch.setattr(meteor, "resume", resume)
    with pytest.raises(OSError, match="crash after replacement"):
        meteor.consolidate(loop)
    assert (
        meteor.completed_stored_scroll() is None
    )  # Finish the interrupted batch before delivery selection.
    write_json(meteor.JOURNAL, stored(200, 2000))
    assert meteor.completed_stored_scroll() == 100
    assert [r["uid"] for r in queue.pending(meteor.JOURNAL)] == [100, 200]
    assert meteor.completed_stored_scroll() == 100
    assert len(queue.pending(meteor.JOURNAL)) == 2


def test_queue_commit_failure_prevents_replacement_or_any_town_action(
    setup, monkeypatch
):
    original = stored()
    write_json(meteor.JOURNAL, original)
    write_json(
        meteor.POLICY, {"enabled": True, "qualified": True, "exchange": {"fee": 0}}
    )

    def fail(*a):
        raise OSError("queue unavailable")

    monkeypatch.setattr(queue, "capture", fail)
    with pytest.raises(OSError, match="queue unavailable"):
        meteor.consolidate(
            NS(town=lambda *a: pytest.fail("No town action before queue commit"))
        )
    assert read_json(meteor.JOURNAL) == original


def test_verified_transfer_consumes_uid_despite_earlier_deferred_receipt(setup):
    write_json(meteor.JOURNAL, stored())
    assert meteor.completed_stored_scroll() == 100
    item = {"uid": 100, "type_id": 720027}
    write_json(
        delivery_route.STATE,
        {
            "receipts": [
                {"outcome": "deferred", "items": [item]},
                {
                    "outcome": "transferred",
                    "items": [item],
                    "proof_digest": "actual-receipt-digest",
                },
            ]
        },
    )
    assert meteor.completed_stored_scroll() is None
    write_json(delivery_route.STATE, {"receipts": []})
    assert (
        meteor.completed_stored_scroll() is None
    )  # Durable terminal proof survives source rollover.
    with queue.database(meteor.JOURNAL) as db:
        row = db.execute("SELECT * FROM scrolls").fetchone()
        assert (
            row["phase"] == "delivered"
            and json.loads(row["disposition"])["proof_digest"]
            == "actual-receipt-digest"
        )


def test_promoted_receipt_is_saved_and_not_requeued_after_qualification_rollover(
    setup, monkeypatch
):
    write_json(meteor.JOURNAL, stored())
    calls = []
    proof = {
        "phase": "delivery_verified",
        "selected_uids": [100],
        "immutable_receipt": "test-proof",
    }

    def promoted(state, uid, *, evidence=False):
        calls.append((uid, evidence))
        assert state == stored()
        return proof if evidence else True

    monkeypatch.setattr(meteor, "_promoted_scroll_delivery", promoted)
    assert meteor.completed_stored_scroll() is None and calls == [(100, True)]
    monkeypatch.setattr(meteor, "_promoted_scroll_delivery", lambda *a, **kw: False)
    assert meteor.completed_stored_scroll() is None
    with queue.database(meteor.JOURNAL) as db:
        row = db.execute("SELECT * FROM scrolls").fetchone()
        assert row["phase"] == "delivered" and json.loads(row["disposition"]) == proof


def test_old_uid_rebank_cooldown_is_independent_of_current_consolidation(setup):
    write_json(meteor.JOURNAL, stored())
    assert meteor.completed_stored_scroll() == 100
    write_json(meteor.JOURNAL, stored(200, 2000))
    meteor.defer_stored_scroll(100)
    assert meteor.completed_stored_scroll() == 200
    assert read_json(meteor.JOURNAL) == stored(200, 2000)
    setup[0] += 901
    assert meteor.completed_stored_scroll() == 100


def test_changed_provenance_cannot_rebind_same_uid(setup):
    write_json(meteor.JOURNAL, stored())
    assert meteor.completed_stored_scroll() == 100
    changed = stored()
    changed["before"]["identity"]["pid"] = 8
    write_json(meteor.JOURNAL, changed)
    with pytest.raises(ValueError, match="provenance changed"):
        meteor.completed_stored_scroll()


def test_explicit_exact_disposition_preserved_and_unknown_disposition_held(setup):
    state = stored()
    write_json(meteor.JOURNAL, state)
    assert meteor.completed_stored_scroll() == 100
    state["user_confirmed_scroll_transfer"] = {
        "uid": 100,
        "type_id": 720027,
        "confirmed": True,
        "source": "explicit user confirmation",
        "destination": "another_character",
    }
    write_json(meteor.JOURNAL, state)
    assert meteor.completed_stored_scroll() is None
    state = stored(200, 2000)
    state["user_confirmed_scroll_consumption"] = {"confirmed": True}
    write_json(meteor.JOURNAL, state)
    with pytest.raises(ValueError, match="not exact"):
        meteor.completed_stored_scroll()


def test_unverified_storage_never_becomes_delivery_intent(setup):
    for changes in (
        {"exchange_verified": False},
        {"phase": "stored_in_market"},
        {"receipts": []},
        {
            "receipts": [
                {"stored": 100, "type_id": 1088001, "verified_in_warehouse": True}
            ]
        },
    ):
        write_json(meteor.JOURNAL, {**stored(), **changes})
        assert meteor.completed_stored_scroll() is None
    assert not queue.path(meteor.JOURNAL).exists()
