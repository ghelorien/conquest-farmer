"""Resume a restock tail after proved delivery and a later pre-input failure.

This preserves the original Meteor/Market journals and their expired budget.
Only existing terminal bilateral receipts may explain missing post-exchange
items; no transaction is retried, overridden or manufactured here.
"""

from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import time

from conquest.character_context import state_path
from conquest.discord_notify import read_json


FAILURE = "Merchant delivery requires a memory-verified safe stopped farmer"


def _same_intent(left, right):
    from conquest.merchants.delivery import exact_items, exact_listings

    if any(
        left.get(k) != right.get(k)
        for k in ("operation_id", "town_visit_id", "visit_id", "farmer_profile_id")
    ):
        return False
    if exact_items(left["items"]) != exact_items(right["items"]):
        return False
    for role in ("farmer", "merchant"):
        a, b = left[role], right[role]
        if (
            any(
                a.get(k) != b.get(k)
                for k in (
                    "identity",
                    "character",
                    "character_uid",
                    "server",
                    "map_id",
                    "position",
                    "silver",
                    "capacity",
                    "trade",
                    "request",
                    "own_booth_uid",
                    "booth_open",
                )
            )
            or exact_items(a["inventory"]) != exact_items(b["inventory"])
            or exact_listings(a.get("booth", [])) != exact_listings(b.get("booth", []))
        ):
            return False
    return True


def _bag_matches_snapshot(bag, snapshot):
    items = {i["uid"]: i for i in bag["items"]}
    inventory = {i["uid"]: i for i in snapshot["inventory"]}
    if (
        len(items) != len(bag["items"])
        or len(inventory) != len(snapshot["inventory"])
        or set(items) != set(inventory)
        or bag["silver"] != snapshot["silver"]
        or bag["capacity"] != snapshot["capacity"]
    ):
        return False
    for uid, item in items.items():
        peer = inventory[uid]
        if any(item.get(k) != peer.get(k) for k in ("type_id", "plus")):
            return False
        # Supplies expose equipment durability in amount. Both native merchant
        # readers expose one owned equipment item, and amount for consumables.
        quantity = 1 if 100000 <= item["type_id"] < 600000 else item.get("amount")
        if quantity != peer.get("quantity"):
            return False
        if any(item[k] != peer.get(k) for k in ("gem1", "gem2", "bound") if k in item):
            return False
    return True


def _delivery_proof(row, meteor, market, failure, target):
    from conquest.merchants import delivery_operation, delivery_route
    from conquest.merchants.delivery import reconciliation_outcome, exact_items
    from conquest.merchants.journal import profile_row

    route = read_json(delivery_route.STATE)
    if route.get("active") or route.get("cleanup_pending"):
        raise ValueError("Delivery ownership remains unresolved")
    origin = {
        "town_visit_id": row["town_visit_id"],
        "visit_id": market["visit_id"],
        "farmer_profile_id": row["farmer_profile_id"],
    }
    expected = deepcopy(meteor["after"])
    proofs = []
    seen = set()
    with closing(
        sqlite3.connect(
            delivery_operation.JOURNAL.resolve().as_uri() + "?mode=ro", uri=True
        )
    ) as db:
        db.row_factory = profile_row
        transactions = list(
            db.execute(
                "SELECT * FROM transactions WHERE created>=? ORDER BY created",
                (row["required_at"],),
            )
        )
        admissions = list(
            db.execute(
                "SELECT * FROM delivery_admissions WHERE created>=?",
                (row["required_at"],),
            )
        )
        if (
            not transactions
            or len(transactions) != len(admissions)
            or {r["id"] for r in transactions} != {r["request_id"] for r in admissions}
        ):
            raise ValueError("Restock delivery admission history is not fully settled")
        admission_by_id = {r["request_id"]: r for r in admissions}
        receiver_path = Path(state_path("reports/merchants/journal.sqlite3"))
        with closing(
            sqlite3.connect(receiver_path.resolve().as_uri() + "?mode=ro", uri=True)
        ) as peer_db:
            for tx in transactions:
                before = json.loads(tx["before_json"])
                result = json.loads(tx["result_json"] or "{}")
                key = tx["id"]
                admission = admission_by_id[key]
                admission_origin = json.loads(admission["origin_json"])
                if (
                    tx["kind"] != "farmer_delivery"
                    or tx["phase"] != "verified"
                    or not meteor["started_at"]
                    <= tx["created"]
                    <= tx["updated"]
                    < failure["time"]
                    or any(
                        before.get(k) != v or admission_origin.get(k) != v
                        for k, v in origin.items()
                    )
                    or before.get("operation_id") != key
                    or admission_origin.get("operation_id") != key
                    or admission["phase"] != "transaction_started"
                    or admission["character"] != tx["character"]
                    or before["farmer"]["identity"] != target
                    or result.get("outcome") != "delivered"
                    or result.get("remaining")
                    or result.get("cleanup_pending")
                    or result.get("operator_additions")
                    or result.get("sale_receipts")
                    or not _bag_matches_snapshot(expected, before["farmer"])
                ):
                    raise ValueError(
                        "Restock delivery lacks an exact terminal ownership chain"
                    )
                offered = exact_items(before["items"])
                if (
                    set(offered) & seen
                    or sorted(offered) != sorted(json.loads(admission["uids_json"]))
                    or offered != exact_items(json.loads(admission["items_json"]))
                    or offered != exact_items(result.get("delivered", []))
                ):
                    raise ValueError("Restock delivered item identities differ")
                trace = [
                    {**dict(s), "payload": json.loads(s["payload"])}
                    for s in db.execute(
                        "SELECT stage,status,payload,timestamp FROM transaction_steps WHERE transaction_id=? ORDER BY id",
                        (key,),
                    )
                ]
                proved = reconciliation_outcome(
                    before,
                    result["farmer"],
                    result["merchant"],
                    trace=trace,
                    now=result["reconciled_at"],
                )
                if (
                    proved["outcome"] != "delivered"
                    or proved["cleanup_pending"]
                    or proved["proof_digest"] != result.get("proof_digest")
                    or not any(
                        s["stage"] == "receiver_receipt"
                        and s["status"] == "observed"
                        and (s["payload"] or {}).get("phase") == "verified"
                        for s in trace
                    )
                ):
                    raise ValueError("Bilateral restock delivery proof is incomplete")
                receiver = peer_db.execute(
                    "SELECT state FROM delivery_reservations WHERE request_id=?", (key,)
                ).fetchall()
                peer = json.loads(receiver[0][0]) if len(receiver) == 1 else {}
                if (
                    peer.get("phase") != "verified"
                    or not _same_intent(peer.get("intent") or {}, before)
                    or not tx["created"] <= peer.get("verified_at", 0) < failure["time"]
                ):
                    raise ValueError(
                        "Receiver terminal delivery receipt is unavailable"
                    )
                records = [
                    r for r in route.get("receipts", []) if r.get("request_id") == key
                ]
                if (
                    len(records) != 1
                    or records[0].get("outcome") != "transferred"
                    or records[0].get("proof_digest") != proved["proof_digest"]
                    or any(records[0].get(k) != v for k, v in origin.items())
                    or exact_items(records[0]["items"]) != offered
                    or not tx["updated"]
                    <= records[0].get("verified_at", 0)
                    < failure["time"]
                ):
                    raise ValueError(
                        "Route delivery receipt is not linked to the bilateral proof"
                    )
                expected["items"] = [
                    i for i in expected["items"] if i["uid"] not in offered
                ]
                if not _bag_matches_snapshot(expected, result["farmer"]):
                    raise ValueError("Post-delivery Farmer ownership is unexplained")
                seen.update(offered)
                proofs.append(
                    {
                        "request_id": key,
                        "proof_digest": proved["proof_digest"],
                        "uids": sorted(offered),
                        "verified_at": records[0]["verified_at"],
                    }
                )
    attempts = market.get("attempts") or []
    if (
        len(attempts) != len(proofs)
        or any(a.get("outcome") != "transferred" for a in attempts)
        or any(
            not market["started_at"] <= a.get("at", 0) < failure["time"]
            for a in attempts
        )
    ):
        raise ValueError("Market attempt history differs from settled deliveries")
    return expected, proofs


def capture(
    loop,
    row,
    meteor,
    *,
    no_transfer=False,
    operator_warehouse=False,
    operator_delivery=False,
):
    from conquest import restock_town_recovery as recovery
    from conquest.merchants.service_visit import MarketVisit
    from conquest.merchants.bridge import request
    from conquest.merchants.handoff import qualified_listing_request
    from conquest.overnight import needs_town, supply_counts

    target = row.get("restock_target")
    visit = loop.town_visit
    if (
        row.get("phase") != "town_work"
        or row.get("reasons") != ["restock"]
        or not recovery._process_identity(target)
        or row.get("route_id") != loop.route.id
        or row.get("hunt_map_id") != loop.route.map_id
        or meteor.get("phase") != "storing_scroll"
        or meteor.get("origin") != loop.route.restock_map_id
        or meteor.get("exchange_verified") is not True
        or not row["required_at"] <= meteor.get("started_at", 0)
        or meteor.get("return_submitted_at")
        or meteor.get("receipts")
        or meteor.get("user_confirmed_scroll_consumption")
        or meteor.get("user_confirmed_scroll_transfer")
    ):
        raise ValueError(
            "Settled-delivery restock has no original native identity and exchange chain"
        )
    events = [
        e
        for e in recovery._rows(recovery.EVENTS)
        if e.get("town_visit_id") == row["town_visit_id"]
    ]
    from conquest.no_transfer_town_recovery import (
        FAILURE as NO_TRANSFER_FAILURE,
        proof as no_transfer_proof,
    )

    expected_failure = NO_TRANSFER_FAILURE if no_transfer else FAILURE
    failure = next(
        (
            e
            for e in reversed(events)
            if e.get("event") == "failed" and e.get("detail") == expected_failure
        ),
        {},
    )
    trace = {
        (Path(f.get("file", "")).name, f.get("function"))
        for f in failure.get("failure_trace", [])
    }
    if (
        failure.get("error_type") != "builtins.ValueError"
        or not {
            ("banking.py", "after_shopping"),
            ("meteor_banking.py", "resume"),
            ("meteor_banking.py", "market_bank"),
            ("delivery_route.py", "_market_storage"),
        }
        <= trace
        or no_transfer
        and ("delivery_route.py", "settle") not in trace
    ):
        raise ValueError("Restock failure is not the pre-admission safety boundary")
    for event in events:
        if event.get("time", 0) <= failure["time"] or event.get("event") in (
            "started",
            "stopped",
        ):
            continue
        last = (event.get("failure_trace") or [{}])[-1]
        if (
            event.get("event") == "failed"
            and Path(last.get("file", "")).name
            in (
                "restock_town_recovery.py",
                "settled_delivery_town_recovery.py",
                "no_transfer_town_recovery.py",
                "overridden_delivery_town_recovery.py",
            )
            and last.get("function")
            in ("capture_pre_admission_tail", "capture", "_delivery_proof", "proof")
        ):
            continue
        raise ValueError("Gameplay followed the pre-admission delivery interruption")
    market = read_json(MarketVisit().path)
    if (
        market.get("phase") != "active"
        or market.get("town_visit_id") != row["town_visit_id"]
        or market.get("parent_visit_id") != row["town_visit_id"]
        or market.get("farmer_profile_id") != visit.profile
        or not meteor["started_at"]
        <= market.get("started_at", 0)
        <= failure["time"]
        <= market.get("deadline", 0)
        < time.time()
    ):
        raise ValueError("Original expired Market visit is unavailable")
    if no_transfer:
        from conquest.no_transfer_town_recovery import settle_before_capture

        settle_before_capture(
            loop,
            row,
            meteor,
            market,
            failure,
            target,
            operator_warehouse=operator_warehouse,
            operator_delivery=operator_delivery,
        )
    recovery._native_tail_safe(loop, target, 1036)
    if operator_delivery:
        from conquest.overridden_delivery_town_recovery import proof as overridden

        expected, proofs = overridden(row, meteor, market, failure, target)
    elif no_transfer:
        expected, proofs = no_transfer_proof(
            row, meteor, market, failure, target, operator_warehouse=operator_warehouse
        )
    else:
        expected, proofs = _delivery_proof(row, meteor, market, failure, target)
    status = request({"action": "status"})
    manual = request({"action": "manual-status"}).get("farmer") or {}
    observation = manual.get("observation") or {}
    pending = status.get("handoff_requested")
    if (
        status.get("input_owner") is not None
        or status.get("handoff_granted") is not False
        or pending is not None
        and qualified_listing_request(status) is None
        or manual.get("session")
        or manual.get("input_fenced")
        or observation.get("available") is not True
        or observation.get("windows_absent") is not True
        or not 0 <= time.time() - observation.get("observed_at", 0) <= 5
        or recovery._other_holds()
    ):
        raise ValueError("Merchant or manual ownership holds settled-delivery restock")
    loop.adopt_ammunition()
    bag = loop.town("supplies")
    if recovery._ownership(bag) != recovery._ownership(expected) or needs_town(
        supply_counts(bag, loop.route), loop.route
    ):
        raise ValueError("Fresh post-delivery ownership or restock supplies changed")
    recovery._native_tail_safe(loop, target, 1036)
    row["pre_admission_restock_tail"] = {
        "capture_kind": (
            "operator_delivery"
            if operator_delivery
            else "operator_warehouse"
            if operator_warehouse
            else "no_transfer"
            if no_transfer
            else "settled_delivery"
        ),
        "target": target,
        "failure": failure,
        "market": market,
        "meteor": meteor,
        "bag": bag,
        "delivery_proofs": proofs,
        "captured_at": time.time(),
        "phase": "captured",
    }
    recovery._save_tail(visit, row)
    return True
