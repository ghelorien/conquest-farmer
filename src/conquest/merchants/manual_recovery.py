"""Atomic manual settlement bookkeeping and observation-only replan signals."""

import json
import time

from conquest.merchants.manual_sessions import (
    ITEM_FIELDS,
    canonical_ownership,
    ownership_digest,
)


SCHEMA = """CREATE TABLE IF NOT EXISTS manual_replans(
 session_id TEXT PRIMARY KEY, target_profile_id TEXT NOT NULL, settled_at REAL NOT NULL,
 merchant_pending INTEGER NOT NULL DEFAULT 1, farmer_pending INTEGER NOT NULL DEFAULT 1,
 merchant_evidence TEXT, farmer_evidence TEXT);
CREATE TABLE IF NOT EXISTS manual_rebaseline(
 id TEXT PRIMARY KEY, target_profile_id TEXT NOT NULL, source_session_id TEXT NOT NULL,
 phase TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
 process_json TEXT, character_json TEXT, stable_digest TEXT, stable_since REAL,
 last_observed_at REAL, reason TEXT, snapshot_json TEXT);
"""


def rebaseline_views(journal):
    with journal.db() as db:
        rows = list(
            db.execute("SELECT * FROM manual_rebaseline WHERE phase!='completed'")
        )
    return [
        {
            **dict(row),
            "rebaseline": True,
            "ever_approved": False,
            "holds_automation": True,
            "approval_binding": None,
            "request_state": None,
            "expires_at": None,
            "visitor": None,
        }
        for row in rows
    ]


def start_rebaseline(journal, target, session_id, *, now=None):
    now = time.time() if now is None else now
    key = "rebaseline:" + session_id
    with journal.db() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            "INSERT OR IGNORE INTO manual_rebaseline(id,target_profile_id,source_session_id,phase,created_at,updated_at,reason) VALUES(?,?,?,'settlement_observed',?,?,?)",
            (
                key,
                target,
                session_id,
                now,
                now,
                "Waiting for stable fresh ownership after operator disposition",
            ),
        )
    return key


def reset_rebaseline(
    journal, key, *, operator, confirmation_reference, reason, now=None
):
    now = time.time() if now is None else now
    with journal.db() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT * FROM manual_rebaseline WHERE id=?", (key,)
        ).fetchone()
        if row is None:
            raise ValueError("Rebaseline hold not found")
        if row["phase"] == "completed":
            return
        if db.execute(
            "SELECT 1 FROM events WHERE event='manual_rebaseline_operator_retry' AND json_extract(payload,'$.id')=? AND json_extract(payload,'$.confirmation_reference')=?",
            (key, confirmation_reference),
        ).fetchone():
            return
        db.execute(
            "UPDATE manual_rebaseline SET phase='settlement_observed',process_json=NULL,character_json=NULL,stable_digest=NULL,stable_since=NULL,last_observed_at=NULL,snapshot_json=NULL,updated_at=?,reason=? WHERE id=?",
            (now, "Explicit rebaseline retry: " + reason, key),
        )
        db.execute(
            "INSERT INTO events(character,event,payload,timestamp) VALUES(?,?,?,?)",
            (
                row["target_profile_id"],
                "manual_rebaseline_operator_retry",
                json.dumps(
                    {
                        "id": key,
                        "operator": operator,
                        "confirmation_reference": confirmation_reference,
                        "reason": reason,
                    }
                ),
                now,
            ),
        )


def observe_rebaseline(journal, target, snapshot, *, now=None, target_role="Merchant"):
    """A disposition releases only into a target-local, durable evidence hold."""
    now = time.time() if now is None else now
    with journal.db() as db:
        db.execute("BEGIN IMMEDIATE")
        rows = list(
            db.execute(
                "SELECT * FROM manual_rebaseline WHERE target_profile_id=? AND phase!='completed'",
                (target,),
            )
        )
        if not rows:
            return False
        for row in rows:
            if row["phase"] == "needs_attention":
                continue
            try:
                proof = canonical_ownership(snapshot, require_closed=False)
                if not 0 <= now - snapshot["timestamp"] <= 2:
                    raise ValueError("Fresh ownership evidence is required")
                process = json.dumps(proof["identity"], sort_keys=True)
                character = json.dumps(
                    {
                        key: proof[key]
                        for key in ("character", "character_uid", "server")
                    },
                    sort_keys=True,
                )
                if row["process_json"] is not None and (
                    row["process_json"] != process or row["character_json"] != character
                ):
                    raise ValueError(
                        "Process or character changed during post-override baseline observation"
                    )
            except (ValueError, KeyError, TypeError) as error:
                db.execute(
                    "UPDATE manual_rebaseline SET phase='needs_attention',updated_at=?,reason=?,snapshot_json=? WHERE id=?",
                    (now, str(error), json.dumps(snapshot), row["id"]),
                )
                continue
            if (
                row["last_observed_at"] is not None
                and snapshot["timestamp"] <= row["last_observed_at"]
            ):
                continue
            if snapshot["request"] is not None or snapshot["trade"] is not None:
                db.execute(
                    "UPDATE manual_rebaseline SET process_json=?,character_json=?,stable_digest=NULL,stable_since=NULL,last_observed_at=?,updated_at=?,snapshot_json=? WHERE id=?",
                    (
                        process,
                        character,
                        snapshot["timestamp"],
                        now,
                        json.dumps(snapshot),
                        row["id"],
                    ),
                )
                continue
            digest = ownership_digest(snapshot)
            if (
                row["stable_digest"] == digest
                and snapshot["timestamp"] - row["stable_since"] >= 5
            ):
                # No counterpart or transfer conclusion: establish the same
                # exclusion/baseline/replan transaction as ordinary settlement.
                settlement(
                    db,
                    {
                        "id": row["id"],
                        "target_profile_id": target,
                        "created_at": row["created_at"],
                    },
                    snapshot,
                    {"sales_receipt": False},
                    target_role=target_role,
                )
                # Missing original ownership cannot establish new stock.
                db.execute(
                    "UPDATE manual_rebaseline SET phase='completed',updated_at=?,last_observed_at=?,snapshot_json=?,reason=NULL WHERE id=?",
                    (now, snapshot["timestamp"], json.dumps(snapshot), row["id"]),
                )
            else:
                since = (
                    row["stable_since"]
                    if row["stable_digest"] == digest
                    else snapshot["timestamp"]
                )
                db.execute(
                    "UPDATE manual_rebaseline SET process_json=?,character_json=?,stable_digest=?,stable_since=?,last_observed_at=?,updated_at=?,snapshot_json=? WHERE id=?",
                    (
                        process,
                        character,
                        digest,
                        since,
                        snapshot["timestamp"],
                        now,
                        json.dumps(snapshot),
                        row["id"],
                    ),
                )
        return True


def settlement(db, session, snapshot, receipt, *, target_role="Merchant"):
    """Called only inside ManualSessionStore's terminal evidence transaction."""
    target = session["target_profile_id"]
    at = snapshot["timestamp"]
    prior = db.execute(
        "SELECT before_json FROM manual_requests WHERE session_id=? ORDER BY created_at,id LIMIT 1",
        (session["id"],),
    ).fetchone()
    before = json.loads(prior[0]) if prior else {}
    owned = {
        row["uid"]: tuple(row[field] for field in ITEM_FIELDS)
        for row in before.get("inventory", []) + before.get("booth", [])
    }
    added = bool(prior) and any(
        owned.get(row["uid"]) != tuple(row[field] for field in ITEM_FIELDS)
        for row in snapshot["inventory"]
    )
    baseline = db.execute(
        "SELECT started_at FROM sales_baseline WHERE character=?", (target,)
    ).fetchone()
    current = {
        key: snapshot[key]
        for key in (
            "identity",
            "timestamp",
            "inventory",
            "booth",
            "silver",
            "request",
            "trade",
        )
    }
    db.execute(
        "INSERT INTO events(character,event,payload,timestamp) VALUES(?,?,?,?)",
        (
            target,
            "sales_observation_gap",
            json.dumps(
                {
                    "from": session["created_at"],
                    "to": at,
                    "reason": "manual_session",
                    "session_id": session["id"],
                    "sales_receipt": False,
                }
            ),
            at,
        ),
    )
    db.execute(
        "INSERT OR REPLACE INTO sales_baseline VALUES(?,?,?)",
        (target, json.dumps(current), baseline[0] if baseline else at),
    )
    if added and target_role == "Merchant":
        db.execute(
            "INSERT OR REPLACE INTO state VALUES(?,?,?)", (target, "new_stock", "true")
        )
    # Audit/evidence already appended by the domain in this same transaction.
    # These signals convey only a need to observe again, never input authority.
    if target_role not in ("Merchant", "Farmer"):
        raise ValueError("A known target role is required for manual recovery")
    db.execute(
        "INSERT INTO manual_replans(session_id,target_profile_id,settled_at,merchant_pending,farmer_pending) VALUES(?,?,?,?,?)",
        (
            session["id"],
            target,
            at,
            int(target_role == "Merchant"),
            int(target_role == "Farmer"),
        ),
    )


def consume_merchant(journal, character, snapshot, *, now=None):
    now = time.time() if now is None else now
    proof = canonical_ownership(snapshot)
    if not 0 <= now - snapshot["timestamp"] <= 2:
        raise ValueError("Fresh merchant memory is required after a manual session")
    target = getattr(character, "profile_id", character)
    with journal.db() as db:
        db.execute("BEGIN IMMEDIATE")
        rows = list(
            db.execute(
                "SELECT session_id,settled_at FROM manual_replans WHERE target_profile_id=? AND merchant_pending=1",
                (target,),
            )
        )
        if not rows:
            return False
        if snapshot["timestamp"] <= max(row["settled_at"] for row in rows):
            return False
        old = db.execute(
            "SELECT value FROM state WHERE character=? AND name='refill'", (target,)
        ).fetchone()
        refill = json.loads(old[0]) if old else {}
        refill["next_check"] = min(refill.get("next_check", now), now)
        db.execute(
            "INSERT OR REPLACE INTO state VALUES(?,?,?)",
            (target, "refill", json.dumps(refill)),
        )
        db.execute(
            "UPDATE manual_replans SET merchant_pending=0,merchant_evidence=? WHERE target_profile_id=? AND merchant_pending=1",
            (
                json.dumps({"observed_at": snapshot["timestamp"], "ownership": proof}),
                target,
            ),
        )
    return True


def consume_farmer(journal, evidence, *, now=None):
    """Record a fresh farmer replan; callers keep all control/stop holds intact."""
    now = time.time() if now is None else now
    if (
        not 0 <= now - evidence.get("timestamp", 0) <= 2
        or type(evidence.get("map_id")) is not int
        or evidence["map_id"] <= 0
        or not isinstance(evidence.get("inventory"), list)
        or type(evidence.get("urgent_banking")) is not bool
    ):
        raise ValueError(
            "Fresh farmer map, inventory and valuable evidence is required"
        )
    with journal.db() as db:
        db.execute("BEGIN IMMEDIATE")
        rows = list(
            db.execute(
                "SELECT session_id,settled_at FROM manual_replans WHERE farmer_pending=1"
            )
        )
        if not rows:
            return []
        if evidence["timestamp"] <= max(row["settled_at"] for row in rows):
            return []
        db.execute(
            "UPDATE manual_replans SET farmer_pending=0,farmer_evidence=? WHERE farmer_pending=1",
            (json.dumps(evidence),),
        )
        return [row["session_id"] for row in rows]
