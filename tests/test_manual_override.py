import json
import time
import pytest

from conquest.merchants.journal import Journal, TERMINAL_PHASES
from conquest.merchants.delivery_reservation import (
    reserve,
    operator_override as override_reservation,
)


def _snapshot(name, uid, item):
    return {
        "character": name,
        "character_uid": uid,
        "identity": {"pid": uid},
        "server": "America",
        "timestamp": 100,
        "map_id": 1036,
        "hp": 100,
        "silver": 100,
        "capacity": 40,
        "inventory": [item],
        "booth": [],
        "request": None,
        "trade": None,
    }


def _item(uid, plus=1):
    return {
        "uid": uid,
        "type_id": 130403,
        "plus": plus,
        "gem1": 0,
        "gem2": 0,
        "quantity": 1,
        "bound": False,
        "slot": 0,
    }


def test_operator_override_is_terminal_distinct_and_idempotent(tmp_path):
    journal = Journal(tmp_path / "source.sqlite3")
    farmer = _snapshot("Parasite", 1, _item(10))
    merchant = _snapshot("Spiritual", 2, _item(20))
    intent = {
        "operation_id": "incident",
        "farmer_profile_id": "Parasite",
        "farmer": farmer,
        "merchant": merchant,
        "items": [_item(10)],
    }
    journal.begin("incident", "Spiritual", "farmer_delivery", intent)
    journal.step("incident", "action_trace", "initialized")
    before = journal.trace("incident")
    with pytest.raises(ValueError, match="confirmation"):
        journal.operator_override("incident", confirmation_reference="incident")
    result = journal.operator_override(
        "incident",
        operator_confirmed=True,
        confirmation_reference="incident",
        operator="operator",
        fresh_evidence={"recheck_unavailable": "merchant_disconnected"},
        now=105,
    )
    assert result["outcome"] == "operator_overridden"
    assert result["operator_override"]["original_phase"] == "prepared"
    assert "operator_overridden" in TERMINAL_PHASES
    assert not journal.pending("Spiritual")
    with journal.db() as db:
        row = db.execute(
            "SELECT before_json,phase FROM transactions WHERE id=?", ("incident",)
        ).fetchone()
    assert json.loads(row["before_json"]) == intent
    assert row["phase"] == "operator_overridden"
    assert (
        journal.operator_override(
            "incident",
            operator_confirmed=True,
            confirmation_reference="incident",
            now=106,
        )
        == result
    )
    assert journal.trace("incident")[: len(before)] == before


def test_override_closes_reservation_without_claiming_transfer(tmp_path):
    journal = Journal(tmp_path / "receiver.sqlite3")
    farmer = _snapshot("Parasite", 1, _item(10))
    merchant = _snapshot("Spiritual", 2, _item(20))
    stamp = time.time()
    farmer["timestamp"] = merchant["timestamp"] = stamp
    state = reserve(
        journal,
        "incident",
        farmer,
        merchant,
        [_item(10)],
        origin={"operation_id": "incident", "farmer_profile_id": "Parasite"},
        now=stamp,
    )
    result = override_reservation(
        journal,
        "Spiritual",
        "incident",
        operator_confirmed=True,
        confirmation_reference="incident",
        fresh_evidence={"recheck_unavailable": "offline"},
        now=stamp + 5,
    )
    assert result["phase"] == "operator_overridden"
    assert result["disposition"]["outcome"] == "operator_overridden"
    assert result["disposition"]["delivered"] == []
    assert result["operator_override"]["original_evidence"]["phase"] == "reserved"
    assert (
        journal.get("Spiritual", "delivery_reservation")["phase"]
        == "operator_overridden"
    )
    assert journal.get("Spiritual", "new_stock") is True
    assert (
        override_reservation(
            journal,
            "Spiritual",
            "incident",
            operator_confirmed=True,
            confirmation_reference="incident",
            now=stamp + 6,
        )
        == result
    )


def test_override_repairs_missing_linked_reservation_from_persisted_intent(tmp_path):
    journal = Journal(tmp_path / "receiver.sqlite3")
    intent = {
        "operation_id": "incident",
        "created_at": 10,
        "farmer": {"character": "Parasite", "character_uid": 1},
        "merchant": {"character": "Spiritual", "character_uid": 2},
        "items": [],
    }
    result = override_reservation(
        journal,
        "Spiritual",
        "incident",
        operator_confirmed=True,
        confirmation_reference="incident",
        intent=intent,
        now=11,
    )
    assert result["phase"] == "operator_overridden"
    assert result["operator_override"]["original_phase"] == "missing_reservation"
    assert (
        result["operator_override"]["original_evidence"]["operation_id"] == "incident"
    )
    assert journal.get("Spiritual", "new_stock") is True


def test_observed_empty_trade_close_does_not_hold_operator_warehouse_exit(tmp_path):
    from conquest.merchants.delivery_operation import status

    journal = Journal(tmp_path / "source.sqlite3")
    farmer = _snapshot("Parasite", 1, _item(10))
    merchant = _snapshot("Spiritual", 2, _item(20))
    journal.begin(
        "incident",
        "Spiritual",
        "farmer_delivery",
        {
            "operation_id": "incident",
            "farmer_profile_id": "Parasite",
            "farmer": farmer,
            "merchant": merchant,
            "items": [_item(10)],
        },
    )
    journal.transition("incident", "uncertain", {"reason": "interrupted empty trade"})
    journal.step("incident", "cleanup_trade", "before_action", {"point": [1, 2]})
    journal.step(
        "incident",
        "cleanup_trade",
        "observed",
        {"farmer_trade": False, "merchant_trade": False},
    )
    journal.operator_override(
        "incident",
        operator_confirmed=True,
        confirmation_reference="warehouse-only",
        fresh_evidence={"farmer": farmer, "merchant": merchant},
    )
    receipt = status(journal, "incident")
    assert receipt["outcome"] == "operator_overridden"
    assert receipt["cleanup_pending"] == []
    assert receipt["next_action"] == "release_route"
    assert receipt["operator_override"]["confirmation_reference"] == "warehouse-only"
