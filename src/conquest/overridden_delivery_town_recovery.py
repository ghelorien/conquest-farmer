"""Read-only proof of a Market delivery settled by an explicit operator override.

An operator_overridden farmer transaction is a disposition, never a transfer
outcome.  It explains the missing post-exchange items only together with the
receiving merchant's own 'verified' delivery transaction for exactly those
items inside the farmer transaction window, the linked receiver reservation
and the override's exact original evidence.  Nothing is retried, overridden or
manufactured here; the caller continues only the unreturned storage/cash tail.
"""

import hashlib
import json
import sqlite3
from contextlib import closing
from copy import deepcopy
from pathlib import Path

from conquest.character_context import state_path
from conquest.discord_notify import read_json

KIND = "operator_delivery"
SCROLL_RESOLUTION = "merchant_verified_operator_override"
# Steps written by the override itself; everything else predates it.
OVERRIDE_STEPS = (
    ("transaction", "operator_overridden"),
    ("operator_override", "observed"),
)


def _ro(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    from conquest.merchants.journal import profile_row

    db.row_factory = profile_row
    return closing(db)


def _since(db, table, required_at):
    return list(
        db.execute(
            f"SELECT * FROM {table} WHERE created>=? OR updated>=?",
            (required_at, required_at),
        )
    )


def requested(row):
    """Select this proof only for a carried-nothing override; grants nothing."""
    from conquest.merchants.delivery_operation import JOURNAL

    with _ro(JOURNAL) as db:
        rows = _since(db, "transactions", row["required_at"])
    if len(rows) != 1 or rows[0]["phase"] != "operator_overridden":
        return False
    try:
        before = json.loads(rows[0]["before_json"])
        result = json.loads(rows[0]["result_json"] or "{}")
        farmer = result["operator_override"]["fresh_evidence"]["farmer"]
        carried = {i["uid"] for i in farmer["inventory"]}
        offered = {i["uid"] for i in before["items"]}
    except (KeyError, TypeError, ValueError):
        return False
    return bool(offered) and not offered & carried


def proof(row, meteor, market, failure, target, *, allow_terminal_active=False):
    from conquest.merchants import delivery_operation, delivery_route
    from conquest.merchants.delivery import exact_items
    from conquest.settled_delivery_town_recovery import (
        _bag_matches_snapshot,
        _same_intent,
    )

    route = read_json(delivery_route.STATE)
    active = route.get("active")
    if active and not allow_terminal_active or route.get("cleanup_pending"):
        raise ValueError("Original delivery must settle before overridden capture")
    origin = {
        "town_visit_id": row["town_visit_id"],
        "visit_id": market["visit_id"],
        "farmer_profile_id": row["farmer_profile_id"],
    }
    with _ro(delivery_operation.JOURNAL) as db:
        transactions = _since(db, "transactions", row["required_at"])
        admissions = _since(db, "delivery_admissions", row["required_at"])
        if len(transactions) != 1 or len(admissions) != 1:
            raise ValueError(
                "Overridden restock delivery is not the only admission since the visit"
            )
        tx, admission = transactions[0], admissions[0]
        key = tx["id"]
        steps = [
            dict(s)
            for s in db.execute(
                "SELECT stage,status,payload,timestamp FROM transaction_steps "
                "WHERE transaction_id=? ORDER BY id",
                (key,),
            )
        ]
    before = json.loads(tx["before_json"])
    result = json.loads(tx["result_json"] or "{}")
    override = result.get("operator_override") or {}
    fresh = override.get("fresh_evidence") or {}
    admitted = json.loads(admission["origin_json"])
    selected = exact_items(before["items"])
    expected = deepcopy(meteor["after"])
    expected["items"] = [i for i in expected["items"] if i["uid"] not in selected]
    if (
        tx["kind"] != "farmer_delivery"
        or tx["phase"] != "operator_overridden"
        or not meteor["started_at"]
        <= market["started_at"]
        <= admission["created"]
        <= tx["created"]
        <= failure["time"]
        <= tx["updated"]
        or admission["request_id"] != key
        or admission["phase"] != "transaction_started"
        or admission["character"] != tx["character"]
        or before.get("operation_id") != key
        or admitted.get("operation_id") != key
        or any(before.get(k) != v or admitted.get(k) != v for k, v in origin.items())
        or before["farmer"]["identity"] != target
        or result.get("outcome") != "operator_overridden"
        or result.get("delivered") != []
        or result.get("remaining") != []
        or result.get("cleanup_pending")
        or result.get("operator_additions")
        or result.get("sale_receipts")
        or not _bag_matches_snapshot(meteor["after"], before["farmer"])
        or not selected
        or len(expected["items"]) != len(meteor["after"]["items"]) - len(selected)
        or sorted(selected) != sorted(json.loads(admission["uids_json"]))
        or selected != exact_items(json.loads(admission["items_json"]))
    ):
        raise ValueError("Overridden restock delivery lacks its exact original chain")
    # The override binds the user's confirmation to the exact incident.
    original = override.get("original_evidence") or {}
    digest = hashlib.sha256(
        json.dumps(original, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    earlier = [
        s
        for s in steps
        if s["stage"] != "operator_recheck"
        and (s["stage"], s["status"]) not in OVERRIDE_STEPS
    ]
    uncertain = [
        s["timestamp"]
        for s in earlier
        if (s["stage"], s["status"]) == ("transaction", "uncertain")
    ]
    confirmed = [
        s["timestamp"]
        for s in earlier
        if (s["stage"], s["status"]) == ("farmer_confirm", "before_action")
    ]
    if (
        override.get("operator_confirmed") is not True
        or not isinstance(override.get("confirmation_reference"), str)
        or not override["confirmation_reference"].strip()
        or not isinstance(override.get("operator"), str)
        or not override["operator"].strip()
        or override.get("original_phase") != "uncertain"
        or original.get("id") != key
        or original.get("kind") != "farmer_delivery"
        or original.get("phase") != "uncertain"
        or original.get("before_json") != tx["before_json"]
        or original.get("steps") != earlier
        or digest != override.get("original_evidence_digest")
        or digest != result.get("proof_digest")
        or override.get("confirmed_at") != tx["updated"]
        or len(uncertain) != 1
        or len(confirmed) != 1
        or not tx["created"] <= confirmed[0] <= uncertain[0] <= failure["time"]
    ):
        raise ValueError(
            "Overridden restock delivery lacks an exact explicit operator confirmation"
        )
    window = (confirmed[0], uncertain[0])
    farmer, merchant = fresh.get("farmer") or {}, fresh.get("merchant") or {}
    held = exact_items(merchant.get("inventory", []) + merchant.get("booth", []))
    if (
        not failure["time"] <= fresh.get("observed_at", 0) <= override["confirmed_at"]
        or farmer.get("identity") != target
        or merchant.get("identity") != before["merchant"]["identity"]
        or any(
            snapshot.get(k) is not None
            for snapshot in (farmer, merchant)
            for k in ("trade", "request")
        )
        or {i["uid"] for i in farmer.get("inventory", [])} & set(selected)
        or not _bag_matches_snapshot(expected, farmer)
        or any(held.get(uid) != value for uid, value in selected.items())
    ):
        raise ValueError("Overridden restock delivery ownership is unexplained")
    name = before["merchant"]["character"]
    with _ro(state_path("reports/merchants/journal.sqlite3")) as peer:
        reservations = list(
            peer.execute(
                "SELECT state FROM delivery_reservations WHERE request_id=?", (key,)
            )
        )
        prefix = f"delivery:{name}:"
        # Every receipt of this merchant touching the attempt, not only the
        # expected one: a second candidate makes the transfer ambiguous.
        receipts = list(
            peer.execute(
                "SELECT * FROM transactions WHERE kind='delivery' AND "
                "substr(id,1,?)=? AND (created BETWEEN ? AND ? OR updated BETWEEN ? AND ?)",
                (len(prefix), prefix, *(tx["created"], failure["time"]) * 2),
            )
        )
    reservation = json.loads(reservations[0]["state"]) if len(reservations) == 1 else {}
    linked = reservation.get("operator_override") or {}
    disposition = reservation.get("disposition") or {}
    if (
        reservation.get("phase") != "operator_overridden"
        or not _same_intent(reservation.get("intent") or {}, before)
        or linked.get("operator_confirmed") is not True
        or linked.get("confirmation_reference") != override["confirmation_reference"]
        or linked.get("fresh_evidence") != fresh
        or disposition.get("outcome") != "operator_overridden"
        or disposition.get("delivered") != []
        or disposition.get("cleanup_pending")
        or not window[0] <= reservation.get("offer_verified_at", 0) <= window[1]
    ):
        raise ValueError("Receiver override is not linked to the farmer override")
    if len(receipts) != 1:
        raise ValueError("Overridden delivery lacks one verified merchant receipt")
    receipt = receipts[0]
    received = json.loads(receipt["before_json"])
    accepted = json.loads(receipt["result_json"] or "{}")
    trade = received.get("trade") or {}
    if (
        receipt["phase"] != "verified"
        or not reservation["offer_verified_at"]
        <= receipt["created"]
        <= receipt["updated"]
        <= window[1]
        or received.get("character") != name
        or received.get("identity") != before["merchant"]["identity"]
        or received.get("character_uid") != before["merchant"]["character_uid"]
        or trade.get("participant") != before["farmer"]["character"]
        or trade.get("participant_uid") != before["farmer"]["character_uid"]
        or trade.get("own_items")
        or trade.get("own_silver") != 0
        or trade.get("other_silver") != 0
        or accepted.get("silver") != 0
        or exact_items(trade.get("items", [])) != selected
        or exact_items(accepted.get("items", [])) != selected
        or set(exact_items(received.get("inventory", []))) & set(selected)
    ):
        raise ValueError("Merchant receipt differs from the overridden delivery")
    operations = [
        r
        for r in route.get("operations", [])
        if r.get("started_at", 0) >= row["required_at"]
    ]
    if any(r.get("request_id") == key for r in route.get("receipts", [])) or (
        market.get("attempts") or []
    ):
        raise ValueError("Overridden delivery was recorded as another Market outcome")
    if active:
        if (
            operations
            or active.get("request_id") != key
            or exact_items(active.get("items", [])) != selected
            or any(active.get(k) != v for k, v in origin.items())
            or active.get("merchant") != name
            or active.get("merchant_identity") != before["merchant"]["identity"]
            or active.get("merchant_uid") != before["merchant"]["character_uid"]
        ):
            raise ValueError("Active route differs from the overridden delivery")
        # This internal result authorizes only read-only route consumption.
        return expected, []
    if (
        len(operations) != 1
        or operations[0].get("request_id") != key
        or operations[0].get("outcome") != "operator_overridden"
        or operations[0].get("items") != []
        or operations[0].get("remaining") != []
        or operations[0].get("proof_digest") != digest
        or any(operations[0].get(k) != v for k, v in origin.items())
        or operations[0].get("verified_at", 0) < tx["updated"]
    ):
        raise ValueError("Original route has not consumed its overridden delivery")
    return expected, [
        {
            "request_id": key,
            "outcome": KIND,
            "proof_digest": digest,
            "confirmation_reference": override["confirmation_reference"],
            "uids": sorted(selected),
            "merchant_transaction": receipt["id"],
            "merchant_verified_at": receipt["updated"],
            "verified_at": operations[0]["verified_at"],
        }
    ]


def delivered_scroll(loop, state, stored):
    """The scroll's disposition from a captured, just re-proved override claim."""
    visit = getattr(loop, "town_visit", None)
    claim = (visit.state() if visit is not None else {}).get(
        "pre_admission_restock_tail"
    ) or {}
    proofs = claim.get("delivery_proofs") or []
    scroll = state.get("scroll_uid")
    if (
        claim.get("capture_kind") != KIND
        or claim.get("phase") != "captured"
        or len(proofs) != 1
        or proofs[0].get("outcome") != KIND
        or scroll not in proofs[0].get("uids", [])
        or any(i["uid"] == scroll for i in stored["items"])
    ):
        return None
    return deepcopy(proofs[0])
