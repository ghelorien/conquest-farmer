"""One old legacy refill cursor, reconciled without merchant input.

This is an explicit authenticated operation, never a scheduler action.  The
1078 input fence stays in place.  Absence of a journaled listing attempt is
checked again inside the transaction which archives the pending cursor.
It applies to whichever merchant's journal actually holds that exact legacy
cursor; any other merchant receives an inert blocker before any game read.
"""

import hashlib
import json
import sqlite3
import time

from conquest.character_context import resolve_merchant
from conquest.memory_build_layout import CLIENT_SHA256_1078
from conquest.merchants.journal import character_name
from conquest.merchants.market_arrival_1078 import _manual_fenced, _pending_bot_work
from conquest.merchants.observe_1078 import observe
from conquest.merchants.refill_preview_1078 import _same_stock
from conquest.merchants.restoration_preview_1078 import _journal_image, _preview


_CURSOR = frozenset((295705626, 294891157))
_STOCK_FIELDS = ("type_id", "name", "plus", "gem1", "gem2", "bound", "quantity")


def _blocker(reason):
    return {
        "reconciled": False,
        "blocker": reason,
        "game_input": False,
        "journal_updated": False,
        "input_qualified": False,
    }


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _state(db, character, name):
    row = db.execute(
        "SELECT value FROM state WHERE character=? AND name=?", (character, name)
    ).fetchone()
    return row[0] if row else None


def _no_listing_work(db, character, since):
    # Even a prepared listing needs review: a lost response or old step must
    # never be interpreted as proof that no confirmation was sent.
    return not (
        db.execute(
            "SELECT 1 FROM transactions WHERE character=? AND kind='listing' "
            "AND (created>=? OR updated>=?) LIMIT 1",
            (character, since, since),
        ).fetchone()
        or db.execute(
            "SELECT 1 FROM transaction_steps s JOIN transactions t "
            "ON t.id=s.transaction_id WHERE t.character=? AND t.kind='listing' "
            "AND s.timestamp>=? LIMIT 1",
            (character, since),
        ).fetchone()
    )


def _qualified(snapshot, profile_id):
    return (
        snapshot.get("client_sha256") == CLIENT_SHA256_1078
        and snapshot.get("profile_id") == profile_id
        and snapshot.get("profile_uid_verified") is True
        and snapshot.get("source") == "read_only_memory"
        and snapshot.get("read_only") is True
        and snapshot.get("closed_modal") is True
        and not snapshot.get("trade_open")
        and not snapshot.get("request_open")
        and snapshot.get("map_id") == 1036
        and type(snapshot.get("hp")) is int
        and snapshot["hp"] > 0
        and snapshot.get("own_booth_uid")
        and snapshot.get("booth_open")
    )


def _holds_legacy_cursor(refill):
    cursor = refill.get("cursor")
    return (
        refill.get("pending") is True
        and isinstance(cursor, list)
        and len(cursor) == len(_CURSOR)
        and all(type(uid) is int for uid in cursor)
        and set(cursor) == _CURSOR
    )


def reconcile(runtime, character):
    """Archive only this cursor after two exact, matching memory observations."""
    try:
        target = resolve_merchant(character)
        profile_id = target.profile_id
        # Evidence, not a character name, selects the merchant.  Without the
        # exact legacy cursor nothing is read from the game or the fence.
        if not _holds_legacy_cursor(
            _journal_image(runtime.journal.path, profile_id).get("refill") or {}
        ):
            return _blocker("no_legacy_refill_cursor")
        owner = character_name(target)
        if not runtime.read_only_1078(target, force=True):
            return _blocker("1078_input_fence_not_active")
        if _manual_fenced(runtime, target):
            return _blocker("manual_handoff_or_stop_active")
        state = _journal_image(runtime.journal.path, profile_id)
        refill = state.get("refill") or {}
        incident = state.get("shop_return") or {}
        safety = state.get("recovery_safety") or {}
        cursor = refill.get("cursor")
        since = refill.get("attempt_started_at")
        before = incident.get("before") or {}
        if (
            refill.get("pending") is not True
            or refill.get("status") not in ("checking", "paused_budget")
            or not isinstance(cursor, list)
            or len(cursor) != len(_CURSOR)
            or set(cursor) != _CURSOR
            or any(type(uid) is not int for uid in cursor)
            or refill.get("listed") != 0
            or type(since) not in (int, float)
            or refill.get("source_delivery_operation_id") is not None
            or since <= 0
            or since > time.time()
            or type(before.get("timestamp")) not in (int, float)
            or before["timestamp"] < since
            or incident.get("phase") != "returning"
            or safety.get("active") is not False
            or safety.get("phase") != "market_arrived"
        ):
            return _blocker("old_pending_cursor_or_market_arrival_not_qualified")
        first = observe(runtime, target)
        if not _qualified(first, profile_id):
            return _blocker("first_exact_1078_ownership_not_qualified")
        restoration = _preview(first, state)
        origins = {row["uid"]: row["origin"] for row in restoration["refill"]["cursor"]}
        old_inventory = {item["uid"]: item for item in before["inventory"]}
        live_inventory = {item["uid"]: item for item in first["inventory"]}
        if (
            set(origins) != _CURSOR
            or any(origins[uid] != "prior_inventory" for uid in _CURSOR)
            or any(
                uid not in live_inventory
                or uid not in old_inventory
                or any(
                    live_inventory[uid][key] != old_inventory[uid][key]
                    for key in _STOCK_FIELDS
                )
                for uid in _CURSOR
            )
        ):
            return _blocker("cursor_items_not_unchanged_prior_inventory")
        if safety.get("market_arrival_1078", {}).get("identity") != {
            key: first["identity"][key]
            for key in ("pid", "creation_time_100ns", "path")
        }:
            return _blocker("merchant_identity_differs_from_market_arrival")
        second = observe(runtime, target)
        if (
            not _qualified(second, profile_id)
            or not _same_stock(first, second)
            or not 0 <= time.time() - first["timestamp"] <= 5
            or not 0 <= time.time() - second["timestamp"] <= 5
        ):
            return _blocker("fresh_merchant_ownership_changed")
        if _manual_fenced(runtime, target):
            return _blocker("manual_handoff_or_stop_active")
        if _journal_image(runtime.journal.path, profile_id) != state:
            return _blocker("merchant_journal_changed")
        evidence = {
            "cursor_uids": sorted(_CURSOR),
            "attempt_started_at": since,
            "incident_started_at": incident["started_at"],
            "client_sha256": CLIENT_SHA256_1078,
            "identity": second["identity"],
            "character_uid": second["character_uid"],
            "own_booth_uid": second["own_booth_uid"],
            "ownership_digest": _digest(
                {"inventory": second["inventory"], "booth": second["booth"]}
            ),
            "observations": 2,
            "finding": "no_journaled_listing_attempt_since_cursor_start",
            "journaled_listing_submission_observed": False,
            "refill_check_completed": False,
        }
        when = time.time()
        updated = dict(refill)
        updated["old_cursor_reconciliation_1078"] = {
            "at": when,
            "prior_state": refill,
            "evidence": evidence,
        }
        updated.update(pending=False, status="reconciled_no_listing", cursor=[])
        # Retain the due time and last completed check; this does not claim a
        # fifteen-minute refill check or advance its timer.
        with runtime.journal.db() as db:
            db.execute("BEGIN IMMEDIATE")
            if (
                time.time() - second["timestamp"] > 5
                or _manual_fenced(runtime, target)
                or _pending_bot_work(db, owner)
                or not _no_listing_work(db, owner, since)
            ):
                return _blocker("manual_fence_or_listing_work_changed")
            for name in ("refill", "shop_return", "recovery_safety", "connect_hold"):
                expected = state.get(name)
                raw = _state(db, owner, name)
                if (json.loads(raw) if raw is not None else None) != expected:
                    return _blocker("merchant_journal_changed_before_commit")
            encoded = json.dumps(updated, sort_keys=True)
            raw_refill = _state(db, owner, "refill")
            if (
                db.execute(
                    "UPDATE state SET value=? WHERE character=? AND name=? AND value=?",
                    (encoded, owner, "refill", raw_refill),
                ).rowcount
                != 1
            ):
                return _blocker("refill_cursor_changed_before_commit")
            db.execute(
                "INSERT INTO events(character,event,payload,timestamp) VALUES(?,?,?,?)",
                (
                    owner,
                    "refill_cursor_reconciled_1078",
                    json.dumps(evidence, sort_keys=True),
                    when,
                ),
            )
        return {
            "reconciled": True,
            "status": updated["status"],
            "cursor_uids": sorted(_CURSOR),
            "evidence": evidence,
            "game_input": False,
            "journal_updated": True,
            "input_qualified": False,
        }
    except (OSError, sqlite3.Error, ValueError, KeyError, TypeError, AttributeError):
        return _blocker("reconciliation_evidence_unavailable")
