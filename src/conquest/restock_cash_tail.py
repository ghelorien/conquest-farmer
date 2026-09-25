"""Once-only cash tail of a restock whose Meteor batch finished on restart.

Exact class: the original restock's banking.after_shopping() was interrupted
inside Meteor consolidation, before its deposit/withdraw lines.  A restart's
native Meteor resume then completed the Market work (verified receipts) and
returned to the restock city, and the route correctly refused to leave town
because the visit never ran its cash step.

This continuation performs only that remaining tail: one transfer to the
transport reserve with a verified receipt, the normal panel/service close,
the verified-tail checkpoint and complete_town_work("restock").  Purchases,
deposits of items, Meteor packing and deliveries are never replayed.  A
durable claim is written before the warehouse is opened; any later start
that finds it without a completed visit refuses (uncertain money).
"""

import math
import sqlite3
import time
from contextlib import closing
from copy import deepcopy
from pathlib import Path

from conquest.discord_notify import read_json

CLAIM = "completed_meteor_cash_tail"
# Markers owned by other continuation kinds; their handlers keep precedence.
OTHER_KINDS = (
    "pre_admission_restock_tail",
    "restock_recovery_claim",
    "restock_recovery_attempted_at",
    "verified_tail",
    "urgent_intent",
    "urgent_target",
    "urgent_recovery_claim",
    "urgent_recovery_attempted_at",
    "urgent_banking_tail_completed_at",
    "urgent_followup_completed_at",
)
TERMINAL = ("verified", "aborted", "operator_overridden")
# after_shopping() calls stash_valuables() before its cash lines, and
# stash_valuables() consolidates Meteors first.
INTERRUPTED = (
    ("banking.py", "after_shopping"),
    ("banking.py", "stash_valuables"),
    ("meteor_banking.py", "consolidate"),
)
TRANSACTIONS = {
    "purchase",
    "sale",
    "valuable_stored",
    "restock_complete",
    "restock_recovery_complete",
    "restock_cash_tail_complete",
    "urgent_banking_complete",
}
TRANSACTION_PREFIXES = (
    "silver_",
    "merchant_",
    "meteor_",
    "warehouse_",
    "storage_overflow",
    "urgent_banking",
)


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _frames(event):
    return [
        (Path(f.get("file", "")).name, f.get("function"))
        for f in event.get("failure_trace") or []
    ]


def _inside_consolidation(frames):
    if INTERRUPTED[0] not in frames:
        return False
    at = frames.index(INTERRUPTED[0])
    return tuple(frames[at : at + len(INTERRUPTED)]) == INTERRUPTED


def _transactional(event):
    name = str(event.get("event"))
    return name in TRANSACTIONS or name.startswith(TRANSACTION_PREFIXES)


def _meteor_proof(loop, row, meteor):
    """Exact completed Market batch of this visit; returns expected ownership."""
    from conquest import meteor_banking

    started = meteor["started_at"]
    verified = meteor.get("market_verified_at")
    submitted = meteor.get("return_submitted_at")
    finished = meteor.get("completed_at")
    receipts = meteor.get("receipts")
    before = meteor.get("return_before")
    if (
        meteor.get("exchange_verified") is not True
        or meteor.get("origin") != loop.route.restock_map_id
        or not all(_number(v) for v in (verified, submitted, finished))
        or not row["required_at"] <= started <= verified <= submitted <= finished
        or finished > time.time()
        or type(meteor.get("scroll_uid")) is not int
        or not isinstance(before, dict)
        or not isinstance(receipts, list)
        or not receipts
        or meteor.get("user_confirmed_scroll_consumption")
        or meteor.get("user_confirmed_scroll_transfer")
    ):
        raise ValueError("Completed Meteor batch lacks its verified Market chain")
    stored = [r.get("stored") for r in receipts if isinstance(r, dict)]
    if (
        len(stored) != len(receipts)
        or len(set(stored)) != len(stored)
        or any(
            r.get("verified_in_warehouse") is not True
            or type(r.get("stored")) is not int
            or r["stored"] <= 0
            for r in receipts
        )
        or [r.get("type_id") for r in receipts if r["stored"] == meteor["scroll_uid"]]
        != [meteor_banking.SCROLL]
    ):
        raise ValueError("Meteor Market storage receipts are incomplete")
    fare = (
        read_json(meteor_banking.POLICY)
        .get("origins", {})
        .get(str(meteor["origin"]), {})
        .get("return", {})
        .get("fare")
    )
    if type(fare) is not int or fare < 0 or type(before.get("silver")) is not int:
        raise ValueError("Meteor return fare is not verified")
    expected = deepcopy(before)
    expected["silver"] -= fare
    return expected


def _event_proof(row, meteor):
    """Original cash lines never ran and nothing transacted after the batch."""
    from conquest import restock_town_recovery as recovery

    events = [
        e
        for e in recovery._rows(recovery.EVENTS)
        if e.get("town_visit_id") == row["town_visit_id"]
    ]
    if any(not _number(e.get("time")) for e in events):
        raise ValueError("Restock event audit has an invalid time")
    starts = [e for e in events if e.get("event") == "meteor_consolidation_started"]
    completes = [e for e in events if e.get("event") == "meteor_loop_complete"]
    if (
        len(starts) != 1
        or len(completes) != 1
        or not meteor["started_at"] <= starts[0]["time"]
        or not meteor["completed_at"] <= completes[0]["time"]
    ):
        raise ValueError("Restock events do not show one exact Meteor batch")
    done = completes[0]["time"]
    failures = [
        _frames(e) for e in events if e.get("event") == "failed" and e["time"] < done
    ]
    original = [
        e
        for e in events
        if e.get("event") == "failed"
        and starts[0]["time"] <= e["time"] < meteor["market_verified_at"]
        and ("overnight.py", "restock") in _frames(e)
        and _inside_consolidation(_frames(e))
    ]
    if not original or any(
        INTERRUPTED[0] in frames and not _inside_consolidation(frames)
        for frames in failures
    ):
        raise ValueError(
            "Original restock banking was not interrupted inside Meteor consolidation"
        )
    if any(str(e.get("event")).startswith("silver_") for e in events):
        raise ValueError(
            "A silver transfer was recorded for this restock; reconcile it"
        )
    if any(
        e["time"] >= done and e is not completes[0] and _transactional(e)
        for e in events
    ):
        raise ValueError("A transaction followed the completed Meteor batch")


def _money_and_delivery_proof(row):
    from conquest import banking
    from conquest import restock_town_recovery as recovery
    from conquest.merchants.delivery_operation import JOURNAL as deliveries
    from conquest.merchants.service_visit import MarketVisit

    required = row["required_at"]
    policy = banking.policy()
    if not policy.get("enabled") or not policy.get("deposit_after_shopping", True):
        raise ValueError("Silver banking after shopping is disabled; no cash tail")
    if any(
        not _number(entry.get("time")) or entry["time"] >= required
        for entry in recovery._rows(Path(banking.LEDGER), allow_missing=True)
    ):
        raise ValueError("Money transfer during this restock needs reconciliation")
    if deliveries.exists():
        try:
            with closing(
                sqlite3.connect(deliveries.resolve().as_uri() + "?mode=ro", uri=True)
            ) as db:
                phases = dict(
                    db.execute(
                        "SELECT id, phase FROM transactions WHERE created>=? OR updated>=?",
                        (required, required),
                    ).fetchall()
                )
                admissions = db.execute(
                    "SELECT request_id, phase FROM delivery_admissions "
                    "WHERE created>=? OR updated>=?",
                    (required, required),
                ).fetchall()
                for request_id, phase in admissions:
                    if phase == "transaction_started" and request_id not in phases:
                        found = db.execute(
                            "SELECT phase FROM transactions WHERE id=?", (request_id,)
                        ).fetchone()
                        phases[request_id] = found[0] if found else None
        except sqlite3.Error as error:
            raise ValueError("Merchant delivery journal is unreadable") from error
        if any(phase not in TERMINAL for phase in phases.values()) or any(
            phase != "rejected"
            and not (
                phase == "transaction_started" and phases.get(request_id) in TERMINAL
            )
            for request_id, phase in admissions
        ):
            raise ValueError("A merchant delivery during this restock is unsettled")
    market = read_json(MarketVisit().path)
    if (
        market.get("town_visit_id") == row["town_visit_id"]
        and market.get("phase") != "departed"
    ):
        raise ValueError("This restock's Market service visit is still active")


def _input_holds():
    from conquest import restock_town_recovery as recovery
    from conquest.merchants.bridge import request
    from conquest.merchants.handoff import qualified_listing_request

    status = request({"action": "status"})
    manual = request({"action": "manual-status"}).get("farmer") or {}
    pending = status.get("handoff_requested")
    if (
        status.get("input_owner") is not None
        or status.get("handoff_granted") is not False
        or pending is not None
        and qualified_listing_request(status) is None
        or manual.get("session")
        or manual.get("input_fenced")
        or recovery._other_holds()
    ):
        raise ValueError("Merchant, manual or transaction holds block the cash tail")


def resume(loop):
    """Finish the cash tail of exactly one proved class; never replay it."""
    from conquest import banking, meteor_banking
    from conquest import restock_town_recovery as recovery
    from conquest.merchants import handoff
    from conquest.overnight import needs_town, supply_counts
    from conquest.town_trade import stash_candidate
    from conquest.town_visit import _process_identity, checkpoint_verified_tail

    visit = loop.town_visit
    row = visit.state()
    if (
        row.get("phase") != "town_work"
        or row.get("reasons") != ["restock"]
        or row.get("town_work_completed_at")
    ):
        return False
    if row.get(CLAIM):
        raise ValueError(
            "Restock cash tail was already attempted; reconcile before any replay"
        )
    if any(row.get(field) is not None for field in OTHER_KINDS):
        return False
    meteor = read_json(meteor_banking.JOURNAL)
    started = meteor.get("started_at")
    if (
        meteor.get("phase") != "completed"
        or not _number(started)
        or started < row["required_at"]
    ):
        return False
    loop.check_stop()
    target = row.get("restock_target")
    if (
        not _process_identity(target)
        or row.get("route_id") != loop.route.id
        or row.get("hunt_map_id") != loop.route.map_id
    ):
        raise ValueError("Restock cash tail lacks its original process and route")
    expected = _meteor_proof(loop, row, meteor)
    _event_proof(row, meteor)
    _money_and_delivery_proof(row)
    _input_holds()
    health = loop.health()
    if health.get("target") != target or loop.identity != target:
        raise ValueError("Restock cash tail process differs from the original restock")
    loop.stop_farm()  # A completed Meteor skips the pending-trip stop on restart.
    recovery._native_tail_safe(loop, target, loop.route.restock_map_id)
    if getattr(loop.terrain, "map_id", None) != loop.route.restock_map_id:
        from conquest.character_context import installation_path
        from conquest.navigation import read_terrain

        loop.terrain = read_terrain(
            installation_path(r"C:\Program Files\Classic Conquer 2.0"),
            loop.route.restock_map_id,
        )
    loop.adopt_ammunition()
    bag = loop.town("supplies")
    if recovery._ownership(bag) != recovery._ownership(expected):
        raise ValueError("Carried ownership changed since the verified Meteor return")
    if any(stash_candidate(i) for i in bag["items"]) or needs_town(
        supply_counts(bag, loop.route), loop.route
    ):
        raise ValueError("Restock still needs storage or supplies; no cash tail")
    recovery._native_tail_safe(loop, target, loop.route.restock_map_id)
    if visit.state() != row:
        raise ValueError("Restock visit changed during cash-tail proof")
    claim = {
        "phase": "cash_attempted",
        "target": deepcopy(target),
        "meteor": {
            k: meteor.get(k)
            for k in ("started_at", "scroll_uid", "market_verified_at", "completed_at")
        },
        "expected": expected,
        "bag": bag,
        "attempted_at": time.time(),
    }
    row[CLAIM] = claim
    recovery._save_tail(visit, row)  # Durable before the warehouse or money input.
    bank = banking.open_warehouse(loop)
    recovery._native_tail_safe(loop, target, loop.route.restock_map_id)
    if (
        recovery._ownership(loop.town("supplies")) != recovery._ownership(expected)
        or not isinstance(bank, dict)
        or bank.get("silver") != expected["silver"]
        or type(bank.get("stored_silver")) is not int
    ):
        raise ValueError("Cash tail ownership changed before money input")
    claim["cash_before"] = bank
    recovery._save_tail(visit, row)
    excess = bank["silver"] - banking.transport_reserve()
    moved = excess > 0 or excess < 0 and bank["stored_silver"] > 0
    receipt = None
    if excess > 0:
        receipt = banking.transfer(loop, "deposit", excess)
    elif moved:
        receipt = banking.transfer(
            loop, "withdraw", min(-excess, bank["stored_silver"])
        )
    if moved and (not isinstance(receipt, dict) or receipt.get("verified") is not True):
        raise ValueError("Restock cash transfer lacks a verified receipt")
    claim.update(phase="cash_verified", cash_receipt=receipt, verified_at=time.time())
    recovery._save_tail(visit, row)
    banking.close_warehouse(loop)
    loop.town("close", window="Shop")
    handoff.service_window(loop, town=True)
    recovery._native_tail_safe(loop, target, loop.route.restock_map_id)
    bag = loop.town("supplies")
    if any(stash_candidate(i) for i in bag["items"]) or needs_town(
        supply_counts(bag, loop.route), loop.route
    ):
        raise ValueError("Restock cash tail still needs storage or supplies")
    checkpoint_verified_tail(loop, "restock")
    visit.complete_town_work("restock")
    loop.cycles += 1
    loop.record(
        "restock_cash_tail_complete",
        receipt=receipt,
        activity="Finished the verified restock cash tail after Meteor banking",
    )
    loop.record(
        "restock_complete",
        supplies=supply_counts(bag, loop.route),
        activity="Restock complete; returning to hunt",
    )
    return True
