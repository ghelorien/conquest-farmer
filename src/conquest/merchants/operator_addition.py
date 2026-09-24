"""Explicit operator evidence for stock added during an untouched delivery."""

import json
import math
import time

STAGE = "operator_inventory_addition"


def additions(intent, farmer, merchant, trace, now):
    from conquest.merchants.delivery import exact_items, ReconciliationBlocked

    receipts = [
        s.get("payload") or {}
        for s in trace or ()
        if s.get("stage") == STAGE and s.get("status") == "observed"
    ]
    if not receipts:
        return {}, []
    # An operator annotation never explains a submitted offer or confirmation.
    if (
        not any(
            s.get("stage") == "action_trace" and s.get("status") == "initialized"
            for s in trace or ()
        )
        or any(
            s.get("status") == "before_action"
            and s.get("stage") not in ("trade_target_mode", "trade_request")
            for s in trace or ()
        )
        or exact_items(farmer["inventory"])
        != exact_items(intent["farmer"]["inventory"])
    ):
        raise ReconciliationBlocked(
            "Operator addition requires an untouched farmer batch"
        )
    original = set()
    for role in ("farmer", "merchant"):
        for field in ("inventory", "booth"):
            original.update(exact_items(intent[role].get(field, [])))
    result = {}
    for receipt in receipts:
        stamp = receipt.get("confirmed_at")
        if (
            not intent.get("operation_id")
            or receipt.get("operation_id") != intent["operation_id"]
            or receipt.get("operator_confirmed") is not True
            or not isinstance(receipt.get("confirmation_reference"), str)
            or not receipt["confirmation_reference"].strip()
            or type(stamp) not in (int, float)
            or not math.isfinite(stamp)
            or not max(intent[r]["timestamp"] for r in ("farmer", "merchant"))
            <= stamp
            <= now
            or receipt.get("merchant_identity") != intent["merchant"]["identity"]
            or receipt.get("merchant_uid") != intent["merchant"]["character_uid"]
        ):
            raise ReconciliationBlocked(
                "Operator addition evidence is not bound to this delivery"
            )
        items = exact_items(receipt.get("items", []))
        if not items or set(items) & (original | set(result)):
            raise ReconciliationBlocked(
                "Operator addition overlaps existing or reserved stock"
            )
        result.update(items)
    current = exact_items(merchant["inventory"])
    if any(current.get(uid) != details for uid, details in result.items()):
        raise ReconciliationBlocked(
            "Operator-added inventory no longer matches its evidence"
        )
    return result, receipts


def record_confirmation(
    journal,
    key,
    farmer,
    merchant,
    items,
    *,
    confirmation_reference,
    operator_confirmed=False,
    sale_receipts=(),
    now=None,
):
    """Record explicit user confirmation; never transfer, release, or resume.

    Call only after the operator identifies the external addition. Fresh memory
    must account for every other asset/currency change through normal checks.
    """
    from conquest.merchants.delivery import (
        reconciliation_outcome,
        ReconciliationBlocked,
    )

    now = time.time() if now is None else now
    with journal.db() as db:
        row = db.execute("SELECT * FROM transactions WHERE id=?", (key,)).fetchone()
    if (
        not row
        or row["kind"] != "farmer_delivery"
        or row["phase"] not in ("prepared", "uncertain")
    ):
        raise ValueError("Operator evidence requires an unresolved delivery")
    intent = json.loads(row["before_json"])
    trace = [
        {**s, "payload": json.loads(s["payload"])}
        for s in journal.trace(key)
        if s["stage"] != "transaction"
    ]
    receipt = dict(
        operation_id=key,
        merchant_identity=intent["merchant"]["identity"],
        merchant_uid=intent["merchant"]["character_uid"],
        items=items,
        operator_confirmed=operator_confirmed,
        confirmed_at=now,
        confirmation_reference=confirmation_reference,
    )
    step = dict(stage=STAGE, status="observed", payload=receipt)
    # Validate the proposed annotation without writing it first. Stable
    # terminal settlement remains a separate, mandatory recovery step.
    try:
        result = reconciliation_outcome(
            intent,
            farmer,
            merchant,
            trace=[*trace, step],
            sale_receipts=sale_receipts,
            now=now,
        )
        if result["outcome"] != "no_transfer":
            raise ValueError("Operator addition cannot qualify a transferred batch")
    except ReconciliationBlocked as error:
        if error.code != "settlement_pending":
            raise
    journal.step(key, STAGE, "observed", receipt)
    return receipt
