"""Profile-scoped edit readiness over the durable transaction journals.

Presentation preferences do not use this module.  Identity-affecting local
changes use the adapters below so work owned by another profile, and terminal
history owned by this profile, cannot accidentally become global blockers.
"""

from contextlib import closing
from pathlib import Path
import json
import sqlite3


JSON_TERMINAL = frozenset(
    (
        "complete",
        "completed",
        "verified",
        "aborted",
        "cancelled",
        "cancelled_before_input",
        "expired",
        "idle",
        "skipped",
        "withdrawn",
        "no_transfer",
        "no_transfer_reconciled",
        "partial_aborted_reconciled",
        "operator_overridden",
    )
)


def _read_only(path):
    return sqlite3.connect(
        Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=2
    )


def _tables(db):
    return {
        row[0]
        for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }


def _json_blockers(root, profile_id):
    base = Path(root) / "characters" / profile_id / "reports" / "banking"
    if not base.exists():
        return []
    blockers = []
    for path in sorted(base.glob("*.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            # A transaction journal that cannot be interpreted cannot be
            # declared idle.  Ordinary report JSON without a phase is ignored.
            blockers.append({"journal": str(path), "kind": "unreadable_journal"})
            continue
        if not isinstance(value, dict) or not value:
            continue
        phase = value.get("phase")
        active = value.get("active")
        if active:
            blockers.append(
                {"journal": str(path), "kind": "active_route", "phase": phase}
            )
        elif isinstance(phase, str) and phase and phase not in JSON_TERMINAL:
            blockers.append(
                {"journal": str(path), "kind": "unfinished_phase", "phase": phase}
            )
    return blockers


def _farmer_sqlite_blockers(root, profile_id):
    base = Path(root) / "characters" / profile_id / "reports" / "banking"
    if not base.exists():
        return []
    blockers = []
    for path in sorted(base.glob("*.sqlite3")) + sorted(base.glob("*.sqlite")):
        try:
            with closing(_read_only(path)) as db:
                tables = _tables(db)
                if (
                    "transactions" in tables
                    and db.execute(
                        "SELECT id,phase FROM transactions WHERE phase NOT IN "
                        "('verified','aborted','operator_overridden') LIMIT 1"
                    ).fetchone()
                ):
                    row = db.execute(
                        "SELECT id,phase FROM transactions WHERE phase NOT IN "
                        "('verified','aborted','operator_overridden') LIMIT 1"
                    ).fetchone()
                    blockers.append(
                        {
                            "journal": str(path),
                            "kind": "transaction",
                            "id": row[0],
                            "phase": row[1],
                        }
                    )
                if "operations" in tables:
                    row = db.execute(
                        "SELECT operation_id,phase FROM operations WHERE phase NOT IN "
                        "('withdrawn','no_transfer','operator_overridden') LIMIT 1"
                    ).fetchone()
                    if row:
                        blockers.append(
                            {
                                "journal": str(path),
                                "kind": "operation",
                                "id": row[0],
                                "phase": row[1],
                            }
                        )
                if "delivery_admissions" in tables:
                    row = db.execute(
                        "SELECT request_id,phase FROM delivery_admissions "
                        "WHERE phase='admitted' OR (phase='transaction_started' AND NOT EXISTS "
                        "(SELECT 1 FROM transactions t WHERE t.id=delivery_admissions.request_id "
                        "AND t.phase IN ('verified','aborted','operator_overridden'))) LIMIT 1"
                    ).fetchone()
                    if row:
                        blockers.append(
                            {
                                "journal": str(path),
                                "kind": "delivery_admission",
                                "id": row[0],
                                "phase": row[1],
                            }
                        )
        except sqlite3.Error:
            blockers.append({"journal": str(path), "kind": "unreadable_journal"})
    return blockers


def _merchant_blockers(root, profile_id):
    path = Path(root) / "machine-state" / "reports" / "merchants" / "journal.sqlite3"
    if not path.exists():
        return []
    blockers = []
    try:
        with closing(_read_only(path)) as db:
            tables = _tables(db)
            if "transactions" in tables:
                row = db.execute(
                    "SELECT id,phase FROM transactions WHERE character=? AND phase NOT IN "
                    "('verified','aborted','operator_overridden') LIMIT 1",
                    (profile_id,),
                ).fetchone()
                if row:
                    blockers.append(
                        {
                            "journal": str(path),
                            "kind": "transaction",
                            "id": row[0],
                            "phase": row[1],
                        }
                    )
            if "delivery_admissions" in tables:
                row = db.execute(
                    "SELECT request_id,phase FROM delivery_admissions a WHERE character=? AND "
                    "(phase='admitted' OR (phase='transaction_started' AND NOT EXISTS "
                    "(SELECT 1 FROM transactions t WHERE t.id=a.request_id AND "
                    "t.phase IN ('verified','aborted','operator_overridden')))) LIMIT 1",
                    (profile_id,),
                ).fetchone()
                if row:
                    blockers.append(
                        {
                            "journal": str(path),
                            "kind": "delivery_admission",
                            "id": row[0],
                            "phase": row[1],
                        }
                    )
            if "delivery_reservations" in tables:
                for request_id, encoded in db.execute(
                    "SELECT request_id,state FROM delivery_reservations WHERE character=?",
                    (profile_id,),
                ):
                    try:
                        phase = json.loads(encoded).get("phase")
                    except (ValueError, TypeError, AttributeError):
                        phase = None
                    if phase not in (
                        "verified",
                        "cancelled_before_input",
                        "no_transfer_reconciled",
                        "partial_aborted_reconciled",
                        "operator_overridden",
                    ):
                        blockers.append(
                            {
                                "journal": str(path),
                                "kind": "delivery_reservation",
                                "id": request_id,
                                "phase": phase,
                            }
                        )
                        break
            if "manual_sessions" in tables:
                row = db.execute(
                    "SELECT id,phase FROM manual_sessions WHERE target_profile_id=? AND phase "
                    "NOT IN ('completed','request_withdrawn','declined_verified',"
                    "'operator_overridden') LIMIT 1",
                    (profile_id,),
                ).fetchone()
                if row:
                    blockers.append(
                        {
                            "journal": str(path),
                            "kind": "manual_session",
                            "id": row[0],
                            "phase": row[1],
                        }
                    )
            if "manual_rebaseline" in tables:
                row = db.execute(
                    "SELECT id,phase FROM manual_rebaseline WHERE target_profile_id=? "
                    "AND phase!='completed' LIMIT 1",
                    (profile_id,),
                ).fetchone()
                if row:
                    blockers.append(
                        {
                            "journal": str(path),
                            "kind": "manual_rebaseline",
                            "id": row[0],
                            "phase": row[1],
                        }
                    )
            if "manual_replans" in tables:
                row = db.execute(
                    "SELECT session_id,merchant_pending,farmer_pending FROM manual_replans "
                    "WHERE target_profile_id=? AND (merchant_pending!=0 OR farmer_pending!=0) "
                    "LIMIT 1",
                    (profile_id,),
                ).fetchone()
                if row:
                    blockers.append(
                        {
                            "journal": str(path),
                            "kind": "manual_replan",
                            "id": row[0],
                            "phase": "pending",
                        }
                    )
            if "state" in tables:
                for name, kind in (
                    ("manual_reader_hold", "manual_reader_hold"),
                    ("accepted_request", "accepted_request"),
                ):
                    row = db.execute(
                        "SELECT value FROM state WHERE character=? AND name=?",
                        (profile_id, name),
                    ).fetchone()
                    if row:
                        try:
                            value = json.loads(row[0])
                        except (ValueError, TypeError):
                            value = {"unreadable": True}
                        if value is not None:
                            blockers.append(
                                {
                                    "journal": str(path),
                                    "kind": kind,
                                    "phase": value.get("phase")
                                    if isinstance(value, dict)
                                    else None,
                                }
                            )
                row = db.execute(
                    "SELECT value FROM state WHERE character=? AND name='unrelated_request_decline'",
                    (profile_id,),
                ).fetchone()
                if row:
                    try:
                        decline = json.loads(row[0])
                    except (ValueError, TypeError):
                        decline = {"phase": "unreadable"}
                    if decline is not None and (
                        not isinstance(decline, dict)
                        or decline.get("phase") != "verified"
                    ):
                        blockers.append(
                            {
                                "journal": str(path),
                                "kind": "unrelated_request_decline",
                                "phase": decline.get("phase")
                                if isinstance(decline, dict)
                                else None,
                            }
                        )
                row = db.execute(
                    "SELECT value FROM state WHERE character=? AND name='scan'",
                    (profile_id,),
                ).fetchone()
                if row:
                    try:
                        scan = json.loads(row[0])
                    except (ValueError, TypeError):
                        scan = {"pending": True}
                    if scan.get("pending"):
                        blockers.append(
                            {
                                "journal": str(path),
                                "kind": "merchant_scan",
                                "id": scan.get("request_id"),
                                "phase": "pending",
                            }
                        )
    except sqlite3.Error:
        blockers.append({"journal": str(path), "kind": "unreadable_journal"})
    return blockers


class ProfileReadiness:
    """Read-only, profile-scoped readiness used by profile management APIs."""

    def __init__(self, root):
        self.root = Path(root)

    def blockers(self, profile_id):
        return (
            _json_blockers(self.root, profile_id)
            + _farmer_sqlite_blockers(self.root, profile_id)
            + _merchant_blockers(self.root, profile_id)
        )

    def transaction_idle(self, profile_id):
        return not self.blockers(profile_id)

    def require_transaction_idle(self, profile_id):
        blockers = self.blockers(profile_id)
        if blockers:
            first = blockers[0]
            detail = first.get("phase") or first["kind"]
            raise ValueError(
                f"Reconcile unfinished work for this profile before changing local authority ({detail})"
            )


def profile_transaction_blockers(root, profile_id):
    """Public detached blocker records for management UI presentation."""
    return ProfileReadiness(root).blockers(profile_id)
