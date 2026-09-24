"""Hold a merchant's stock until an exact farmer batch reconciles both sides."""

from conquest.merchants.capacity import available_slots
import time
import json
import hashlib
import copy
from conquest.capture import CaptureUnavailable
from conquest.merchants.delivery import (
    prepare,
    exact_items,
    validate_offers,
    reconciliation_outcome,
    ReconciliationBlocked,
)

KEY = "delivery_reservation"
TERMINAL = (
    "verified",
    "cancelled_before_input",
    "no_transfer_reconciled",
    "partial_aborted_reconciled",
    "operator_overridden",
)


def active(journal, character):
    state = journal.get(character, KEY)
    return state if state and state.get("phase") not in TERMINAL else None


def reserve(journal, request_id, farmer, merchant, items, *, origin=None, now=None):
    now = time.time() if now is None else now
    if not isinstance(request_id, str) or not 1 <= len(request_id) <= 100:
        raise ValueError("Invalid delivery reservation ID")
    character = merchant["character"]
    origin = origin or {}
    if set(origin) - {"operation_id", "town_visit_id", "visit_id", "farmer_profile_id"}:
        raise ValueError("Unknown delivery origin context")
    if origin and (
        origin.get("operation_id") != request_id
        or not isinstance(origin.get("farmer_profile_id"), str)
        or not origin["farmer_profile_id"]
        or any(
            origin.get(name) is not None and not isinstance(origin[name], str)
            for name in ("town_visit_id", "visit_id")
        )
    ):
        raise ValueError("Invalid delivery origin context")
    with journal.db() as db:
        row = db.execute(
            "SELECT state FROM delivery_reservations WHERE character=? AND request_id=?",
            (character, request_id),
        ).fetchone()
    if row:
        previous = json.loads(row[0])
        if (
            exact_items(previous["intent"]["items"]) != exact_items(items)
            or previous["intent"]["farmer"]["character_uid"] != farmer["character_uid"]
            or previous["intent"]["merchant"]["character_uid"]
            != merchant["character_uid"]
            or any(
                previous["intent"].get(name) != origin.get(name)
                for name in (
                    "operation_id",
                    "town_visit_id",
                    "visit_id",
                    "farmer_profile_id",
                )
            )
        ):
            raise ValueError("Delivery reservation ID reused for different items")
        return previous
    previous = active(journal, character)
    if previous:
        if previous["request_id"] == request_id and exact_items(
            previous["intent"]["items"]
        ) == exact_items(items):
            return previous
        raise ValueError("Reconcile the previous delivery reservation first")
    if journal.pending(character):
        raise ValueError("Merchant has an unfinished transaction")
    intent = prepare(farmer, merchant, items, now=now)
    intent.update(origin)
    state = {
        "request_id": request_id,
        "phase": "reserved",
        "created_at": now,
        "expires_at": now + 120,
        "intent": intent,
    }
    save(journal, character, state, creating=True)
    journal.event(
        character,
        "delivery_reserved",
        request_id=request_id,
        uids=[i["uid"] for i in items],
    )
    return state


def save(journal, character, state, *, creating=False):
    """Persist the active hold and request receipt in the same durable commit."""
    with journal.db() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT value FROM state WHERE character=? AND name=?", (character, KEY)
        ).fetchone()
        current = json.loads(row[0]) if row else None
        if (
            current
            and current["request_id"] != state["request_id"]
            and current.get("phase") not in TERMINAL
        ):
            raise ValueError("Another delivery reservation is active")
        if not creating and (
            not current or current["request_id"] != state["request_id"]
        ):
            raise ValueError("Delivery reservation changed before persistence")
        if (
            creating
            and db.execute(
                "SELECT 1 FROM delivery_reservations WHERE character=? AND request_id=?",
                (character, state["request_id"]),
            ).fetchone()
        ):
            raise ValueError(
                "Delivery reservation was already created; reconcile its receipt"
            )
        encoded = json.dumps(state)
        db.execute(
            "INSERT OR REPLACE INTO state VALUES(?,?,?)", (character, KEY, encoded)
        )
        db.execute(
            "INSERT OR REPLACE INTO delivery_reservations VALUES(?,?,?)",
            (character, state["request_id"], encoded),
        )
        if state["phase"] in (
            "verified",
            "partial_aborted_reconciled",
            "operator_overridden",
        ):
            db.execute(
                "INSERT OR REPLACE INTO state VALUES(?,?,?)",
                (character, "new_stock", "true"),
            )


def require(journal, character, request_id, *, now=None):
    state = active(journal, character)
    if not state or state["request_id"] != request_id:
        raise ValueError("Delivery reservation mismatch")
    now = time.time() if now is None else now
    if now > state["expires_at"]:
        raise ValueError("Delivery reservation expired; reconcile before further input")
    return state


def ready(journal, character, request_id, farmer, merchant, *, now=None):
    state = require(journal, character, request_id, now=now)
    if state["phase"] not in ("reserved", "offer_ready"):
        raise ValueError("Delivery cannot be made ready in this phase")
    validate_offers(state["intent"], farmer, merchant, now=now)
    state.update(
        phase="offer_ready", offer_verified_at=time.time() if now is None else now
    )
    save(journal, character, state)
    return state


def validate_receiver(journal, snapshot, *, now=None):
    state = active(journal, snapshot["character"])
    if not state:
        return None
    require(journal, snapshot["character"], state["request_id"], now=now)
    intent = state["intent"]
    before = intent["merchant"]
    trade = snapshot.get("trade")
    if (
        snapshot["identity"] != before["identity"]
        or snapshot.get("character_uid") != before["character_uid"]
        or snapshot["silver"] != before["silver"]
        or exact_items(snapshot["inventory"]) != exact_items(before["inventory"])
        or exact_items(snapshot.get("booth", []))
        != exact_items(before.get("booth", []))
    ):
        raise ValueError("Reserved merchant inventory or identity changed")
    if not trade:
        return state
    if (
        trade.get("participant") != intent["farmer"]["character"]
        or trade.get("participant_uid") != intent["farmer"]["character_uid"]
        or trade.get("own_silver") != 0
        or trade.get("other_silver") != 0
        or trade.get("own_items")
    ):
        raise ValueError("Reserved delivery participant or currency changed")
    if state["phase"] != "offer_ready":
        raise CaptureUnavailable("Waiting for the complete reserved farmer offer")
    if exact_items(trade["items"]) != exact_items(intent["items"]):
        raise ValueError("Reserved delivery offer changed")
    if len(trade["items"]) > available_slots(snapshot):
        raise ValueError("Reserved delivery capacity changed")
    return state


def finish(journal, character, request_id, farmer, merchant, *, now=None):
    # Reconciliation is read-only and remains permitted after the work deadline.
    with journal.db() as db:
        row = db.execute(
            "SELECT state FROM delivery_reservations WHERE character=? AND request_id=?",
            (character, request_id),
        ).fetchone()
    if row and json.loads(row[0]).get("phase") == "verified":
        return json.loads(row[0])
    state = journal.get(character, KEY)
    if not state or state["request_id"] != request_id:
        raise ValueError("Delivery reservation mismatch")
    from conquest.merchants.sales import qualified_delivery_receipts

    sales = qualified_delivery_receipts(journal, state["intent"], merchant)
    try:
        outcome = reconciliation_outcome(
            state["intent"], farmer, merchant, sale_receipts=sales, now=now
        )["outcome"]
    except ReconciliationBlocked as error:
        raise ValueError("Both delivery inventories have not reconciled") from error
    if outcome != "delivered":
        raise ValueError("Both delivery inventories have not reconciled")
    if state["phase"] == "verified":
        return state
    state.update(phase="verified", verified_at=time.time() if now is None else now)
    save(journal, character, state)
    journal.event(
        character,
        "delivery_reconciled",
        request_id=request_id,
        uids=[i["uid"] for i in state["intent"]["items"]],
    )
    return state


def disposition(
    journal, character, request_id, farmer, merchant, *, trace, intent=None, now=None
):
    """Commit an exact no-transfer or partial terminal state without input."""
    with journal.db() as db:
        row = db.execute(
            "SELECT state FROM delivery_reservations WHERE character=? AND request_id=?",
            (character, request_id),
        ).fetchone()
    if not row:
        if not intent or intent.get("operation_id") != request_id:
            raise ReconciliationBlocked("Delivery reservation is missing")
        if active(journal, character):
            raise ReconciliationBlocked("Another delivery reservation is active")
        from conquest.merchants.sales import qualified_delivery_receipts

        sales = qualified_delivery_receipts(journal, intent, merchant)
        result = reconciliation_outcome(
            intent, farmer, merchant, trace=trace, sale_receipts=sales, now=now
        )
        actions = [step for step in trace if step.get("status") == "before_action"]
        if result["outcome"] != "no_transfer" or actions:
            raise ReconciliationBlocked(
                "Missing reservation can only prove a no-input no-transfer"
            )
        current = {
            "request_id": request_id,
            "phase": "no_transfer_reconciled",
            "created_at": intent.get("created_at", time.time() if now is None else now),
            "intent": intent,
            "disposition": result,
        }
        save(journal, character, current, creating=True)
        journal.event(
            character,
            "delivery_no_transfer_reconciled",
            request_id=request_id,
            delivered=[],
            remaining=[i["uid"] for i in result["remaining"]],
            proof_digest=result["proof_digest"],
            reservation_missing=True,
        )
        return result
    saved = json.loads(row[0])
    if saved.get("phase") in TERMINAL:
        result = saved.get("disposition") or {}
        if result.get("outcome") == "operator_overridden":
            return result
        if result.get("outcome") not in ("no_transfer", "partial_transfer"):
            raise ReconciliationBlocked(
                "Delivery reservation has a different terminal result"
            )
        return result
    current = journal.get(character, KEY)
    if not current or current.get("request_id") != request_id:
        raise ReconciliationBlocked("Delivery reservation changed before disposition")
    from conquest.merchants.sales import qualified_delivery_receipts

    sales = qualified_delivery_receipts(journal, current["intent"], merchant)
    result = reconciliation_outcome(
        current["intent"], farmer, merchant, trace=trace, sale_receipts=sales, now=now
    )
    if result["outcome"] not in ("no_transfer", "partial_transfer"):
        raise ReconciliationBlocked(
            "Delivery completed and requires the verified finish path"
        )
    phase = (
        "no_transfer_reconciled"
        if result["outcome"] == "no_transfer"
        else "partial_aborted_reconciled"
    )
    current.update(phase=phase, disposition=result)
    save(journal, character, current)
    journal.event(
        character,
        "delivery_" + result["outcome"] + "_reconciled",
        request_id=request_id,
        delivered=[i["uid"] for i in result["delivered"]],
        remaining=[i["uid"] for i in result["remaining"]],
        proof_digest=result["proof_digest"],
    )
    return result


def operator_override(
    journal,
    character,
    request_id,
    *,
    operator_confirmed=False,
    confirmation_reference=None,
    operator=None,
    fresh_evidence=None,
    cleanup_pending=None,
    intent=None,
    now=None,
):
    """Close a reserved merchant hold with explicit operator testimony.

    No ownership outcome is inferred and no transfer is recorded.  The saved
    reservation remains the immutable original evidence; the override only
    appends a linked disposition and marks current stock for an ordinary fresh
    eligibility scan.  A repeated call with the same incident confirmation is
    idempotent, which also repairs a source/reservation closure split after a
    restart.
    """
    if operator_confirmed is not True:
        raise ValueError("Operator confirmation is required for this incident")
    if (
        not isinstance(confirmation_reference, str)
        or not confirmation_reference.strip()
    ):
        raise ValueError("A non-empty incident confirmation reference is required")
    if operator is not None and (not isinstance(operator, str) or not operator.strip()):
        raise ValueError("Operator must be a non-empty string when supplied")
    now = time.time() if now is None else now
    fresh_evidence = fresh_evidence or {}
    missing = False
    with journal.db() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT state FROM delivery_reservations WHERE character=? AND request_id=?",
            (character, request_id),
        ).fetchone()
        current_row = db.execute(
            "SELECT value FROM state WHERE character=? AND name=?", (character, KEY)
        ).fetchone()
        current = json.loads(current_row[0]) if current_row else {}
        if (
            current
            and current.get("request_id") != request_id
            and current.get("phase") not in TERMINAL
        ):
            raise ValueError("Another delivery reservation is active")
        if not row:
            if not intent or intent.get("operation_id") != request_id:
                raise ValueError("Delivery reservation not found")
            if current and current.get("phase") not in TERMINAL:
                raise ValueError("Another delivery reservation is active")
            state = {
                "request_id": request_id,
                "phase": "operator_overridden",
                "created_at": intent.get("created_at", now),
                "intent": intent,
            }
            missing = True
            original_phase = "missing_reservation"
            original_value = intent
        else:
            state = json.loads(row[0])
            if state.get("phase") == "operator_overridden":
                prior = state.get("operator_override") or {}
                if (
                    prior.get("confirmation_reference")
                    != confirmation_reference.strip()
                ):
                    raise ValueError(
                        "Incident was already overridden with a different confirmation"
                    )
                return state
            if state.get("phase") in TERMINAL:
                raise ValueError(
                    "A completed delivery reservation cannot be overridden"
                )
            original_phase = state.get("phase")
            original_value = state
        original_digest = hashlib.sha256(
            json.dumps(original_value, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        override = {
            "operator_confirmed": True,
            "confirmation_reference": confirmation_reference.strip(),
            "operator": operator.strip() if isinstance(operator, str) else None,
            "confirmed_at": now,
            "original_phase": original_phase,
            "original_evidence_digest": original_digest,
            "original_evidence": copy.deepcopy(original_value),
            "fresh_evidence": fresh_evidence,
        }
        state.update(
            phase="operator_overridden",
            operator_override=override,
            disposition={
                "outcome": "operator_overridden",
                "next_action": "release_route",
                "proof_digest": original_digest,
                "delivered": [],
                "remaining": [],
                "cleanup_pending": cleanup_pending or [],
                "replan_required": True,
                "known_stock_recheck_required": True,
            },
        )
        encoded = json.dumps(state, sort_keys=True)
        db.execute(
            "INSERT OR REPLACE INTO state VALUES(?,?,?)", (character, KEY, encoded)
        )
        db.execute(
            "INSERT OR REPLACE INTO delivery_reservations VALUES(?,?,?)",
            (character, request_id, encoded),
        )
        db.execute(
            "INSERT OR REPLACE INTO state VALUES(?,?,?)",
            (character, "new_stock", "true"),
        )
    journal.event(
        character,
        "delivery_operator_overridden",
        request_id=request_id,
        original_evidence_digest=original_digest,
        confirmation_reference=confirmation_reference.strip(),
        reservation_missing=missing,
    )
    return state
