import json
import pytest
from conquest import recovery_override as r
from conquest.discord_notify import write_json, read_json


def test_confirmed_json_override_recovers_after_interrupted_write(
    tmp_path, monkeypatch
):
    path = tmp_path / "meteor.json"
    original = {
        "phase": "travelling",
        "departure_attempted": True,
        "meteor_uids": [11, 12],
    }
    write_json(path, original)
    real_write = r.write_json

    def fail_state(target, value):
        if target == path:
            raise OSError("simulated crash")
        return real_write(target, value)

    monkeypatch.setattr(r, "write_json", fail_state)
    with pytest.raises(OSError, match="simulated crash"):
        r.operator_override(
            path,
            pending_phases=["travelling"],
            operator_confirmed=True,
            confirmation_reference="click-1",
            incident_digest=r.evidence_digest(original),
        )
    assert read_json(path) == original
    audit = tmp_path / "meteor.json.audit.jsonl"
    record = json.loads(audit.read_text())
    monkeypatch.setattr(r, "write_json", real_write)
    result = r.read_recovered(path)
    assert result["phase"] == "operator_overridden"
    assert result["operator_override"] == record
    assert result["operator_override"]["original_state"] == original
    assert len(audit.read_text().splitlines()) == 1
    assert r.read_recovered(path) == result


def test_stale_incident_confirmation_cannot_close_replacement(tmp_path):
    path = tmp_path / "meteor.json"
    original = {"phase": "travelling", "started_at": 1}
    write_json(path, {"phase": "travelling", "started_at": 2})
    with pytest.raises(ValueError, match="changed"):
        r.operator_override(
            path,
            pending_phases=["travelling"],
            operator_confirmed=True,
            confirmation_reference="old",
            incident_digest=r.evidence_digest(original),
        )
    assert read_json(path)["started_at"] == 2
    assert not (tmp_path / "meteor.json.audit.jsonl").exists()


def test_recovery_never_overwrites_new_journal_state(tmp_path, monkeypatch):
    path = tmp_path / "meteor.json"
    write_json(path, {"phase": "travelling", "started_at": 1})
    complete = r._complete
    monkeypatch.setattr(
        r, "_complete", lambda *a: (_ for _ in ()).throw(OSError("crash"))
    )
    with pytest.raises(OSError):
        r.operator_override(
            path,
            pending_phases=["travelling"],
            operator_confirmed=True,
            confirmation_reference="one",
        )
    write_json(path, {"phase": "travelling", "started_at": 2})
    monkeypatch.setattr(r, "_complete", complete)
    assert r.read_recovered(path)["started_at"] == 2
    assert read_json(path)["started_at"] == 2
    assert list(tmp_path.glob("*.superseded"))


def test_reservation_override_does_not_replace_other_active_work(tmp_path):
    from conquest.merchants.journal import Journal
    from conquest.merchants.delivery_reservation import operator_override

    journal = Journal(tmp_path / "merchant.sqlite3")
    current = {"request_id": "new", "phase": "reserved"}
    journal.set("Dutch", "delivery_reservation", current)
    with journal.db() as db:
        db.execute(
            "INSERT INTO delivery_reservations VALUES(?,?,?)",
            ("Dutch", "old", json.dumps({"request_id": "old", "phase": "reserved"})),
        )
    with pytest.raises(ValueError, match="Another delivery"):
        operator_override(
            journal,
            "Dutch",
            "old",
            operator_confirmed=True,
            confirmation_reference="old-click",
        )
    assert journal.get("Dutch", "delivery_reservation") == current
