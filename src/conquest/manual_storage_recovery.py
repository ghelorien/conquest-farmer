"""Explicit manual Phoenix deposit report followed by native ownership proof.

The report is testimony, not a deposit receipt. Only a later user-started route
may open the warehouse, observe the recorded assets, and finish the cash tail.
Old exchange/Market records remain auditable and are never reported as a
successful automatic delivery or return.
"""

from copy import deepcopy
from pathlib import Path
import sqlite3
import time

from conquest.character_context import state_path
from conquest.discord_notify import read_json, process_alive
from conquest.recovery_override import evidence_digest
from conquest.town_visit import TownVisit, _process_identity

REPORT = Path(state_path("reports/banking/manual-storage-recovery.json"))
BASIC = ("uid", "type_id", "amount", "limit", "plus")
OPTIONAL = ("gem1", "gem2", "bound", "quantity")


def pending():
    record = read_json(REPORT)
    return bool(record and record.get("phase") != "completed")


def _save(record):
    from conquest.meteor_banking import _durable_json

    _durable_json(REPORT, record)


def _no_admissions(since):
    from conquest.merchants.delivery_operation import JOURNAL
    from conquest.restock_town_recovery import _other_holds

    if _other_holds():
        raise ValueError("Another transaction holds manual storage recovery")
    if JOURNAL.exists():
        with sqlite3.connect(JOURNAL.resolve().as_uri() + "?mode=ro", uri=True) as db:
            for table in ("delivery_admissions", "transactions"):
                if db.execute(
                    f"SELECT 1 FROM {table} WHERE created>=? LIMIT 1", (since,)
                ).fetchone():
                    raise ValueError(
                        "Delivery admission prevents manual storage recovery"
                    )


def _originals(visit, meteor, market, body):
    from conquest.town_trade import stash_candidate

    if (
        visit.get("town_visit_id") != body["town_visit_id"]
        or visit.get("phase") != "town_work"
        or visit.get("town_work_completed_at")
        or visit.get("reasons") != ["urgent_banking", "restock"]
        or visit.get("urgent_target") != body["target"]
        or not _process_identity(body["target"])
        or not visit.get("required_at", float("inf"))
        <= visit.get("urgent_banking_tail_completed_at", 0)
        < meteor.get("started_at", 0)
        or meteor.get("phase") != "storing_scroll"
        or meteor.get("origin") != 1011
        or meteor.get("exchange_verified") is not True
        or meteor.get("return_submitted_at")
        or meteor.get("receipts")
        or meteor.get("user_confirmed_scroll_consumption")
        or meteor.get("user_confirmed_scroll_transfer")
        or evidence_digest(meteor) != body["meteor_digest"]
        or market.get("phase") != "active"
        or market.get("attempts")
        or market.get("town_visit_id") != visit["town_visit_id"]
        or market.get("farmer_profile_id") != visit["farmer_profile_id"]
        or market.get("deadline", float("inf")) >= time.time()
    ):
        raise ValueError(
            "Manual storage report does not match the interrupted native trip"
        )
    items = [
        item
        for item in meteor.get("after", {}).get("items", [])
        if stash_candidate(item)
    ]
    if (
        not items
        or len({i.get("uid") for i in items}) != len(items)
        or not any(
            i.get("uid") == meteor.get("scroll_uid") and i.get("type_id") == 720027
            for i in items
        )
        or any(any(type(i.get(k)) is not int for k in BASIC) for i in items)
    ):
        raise ValueError("Original manual storage item fingerprints are incomplete")
    return [{k: item[k] for k in (*BASIC, *OPTIONAL) if k in item} for item in items]


def report_command(ui, body):
    """Record only an explicit report while Off; never start or send input."""
    if (
        set(body) != {"action", "town_visit_id", "meteor_digest", "target", "statement"}
        or body["action"] != "report-manual-phoenix-storage"
        or not isinstance(body["statement"], str)
        or not 1 <= len(body["statement"].strip()) <= 1000
    ):
        raise ValueError("Exact manual storage report is required")
    from conquest.worker import request
    from conquest.meteor_banking import JOURNAL
    from conquest.merchants.service_visit import MarketVisit

    with ui.coordinator.lock:
        app = ui.app
        route = read_json(state_path("reports/overnight/status.json"))
        if (
            app.control.snapshot().get("enabled") is not False
            or ui.coordinator.owner is not None
            or ui.grant is not None
            or getattr(app, "thread", None)
            and app.thread.is_alive()
            or route.get("pid")
            and process_alive(route["pid"]) is not False
        ):
            raise ValueError(
                "Manual storage report requires Farming Off and an exited route"
            )
        info = app.last.get("worker_info_path")
        if not info:
            raise ValueError("Attached Farmer identity is unavailable")
        health = request(info, "health")
        life = (health.get("embedded_controls") or {}).get("life") or {}
        visit = TownVisit().state()
        meteor = read_json(JOURNAL)
        market = read_json(MarketVisit().path)
        if (
            health.get("target") != body["target"]
            or health.get("profile_id") != visit.get("farmer_profile_id")
            or life.get("map_id") != 1011
            or life.get("dead_candidate") is not False
            or life.get("current_hp", 0) <= 0
        ):
            raise ValueError(
                "Manual storage report requires the original living Farmer in Phoenix"
            )
        existing = read_json(REPORT)
        if existing and existing.get("phase") != "completed":
            if existing.get("operator_report", {}).get("command") == body:
                return {
                    "reported": True,
                    "phase": existing["phase"],
                    "report_id": existing["report_id"],
                    "verified_storage": False,
                }
            raise ValueError("Another manual storage report is unresolved")
        expected = _originals(visit, meteor, market, body)
        _no_admissions(visit["required_at"])
        testimony = {
            "source": "explicit_user_report",
            "command": deepcopy(body),
            "reported_at": time.time(),
            "destination_map": 1011,
            "transaction_receipt": False,
        }
        record = {
            "phase": "reported",
            "operator_report": testimony,
            "report_id": evidence_digest(testimony),
            "target": deepcopy(body["target"]),
            "town_visit_id": visit["town_visit_id"],
            "farmer_profile_id": visit["farmer_profile_id"],
            "original_visit": visit,
            "original_meteor": meteor,
            "original_market": market,
            "expected_items": expected,
            "unknown_historical_fields": {
                str(i["uid"]): [k for k in OPTIONAL if k not in i] for i in expected
            },
        }
        _save(record)
        return {
            "reported": True,
            "phase": "reported",
            "report_id": record["report_id"],
            "expected_items": expected,
            "verified_storage": False,
        }


def _safe(loop, record):
    from conquest.restock_town_recovery import _native_tail_safe

    _native_tail_safe(loop, record["target"], 1011)
    row = loop.town_visit.state()
    testimony = record.get("operator_report") or {}
    if (
        testimony.get("source") != "explicit_user_report"
        or evidence_digest(testimony) != record.get("report_id")
        or record.get("expected_items")
        != _originals(
            record["original_visit"],
            record["original_meteor"],
            record["original_market"],
            testimony["command"],
        )
    ):
        raise ValueError("Manual storage report binding changed")
    if (
        row.get("town_visit_id") != record["town_visit_id"]
        or row.get("farmer_profile_id") != record["farmer_profile_id"]
        or row.get("urgent_target") != record["target"]
        or row.get("route_id") != loop.route.id
        or loop.route.restock_map_id != 1011
        or row.get("reasons") != ["urgent_banking", "restock"]
    ):
        raise ValueError(
            "Manual storage continuation belongs to a different town visit"
        )
    _no_admissions(row["required_at"])


def _sample(loop, record):
    from conquest.restock_town_recovery import _ownership

    _safe(loop, record)
    source = loop.town("warehouse-locate")
    if (
        source.get("map_id") != 1011
        or tuple(source.get("position", ())) != (227, 246)
        or not source.get("npc_id")
    ):
        raise ValueError("Manual storage requires the exact Phoenix Warehouseman")
    bag = loop.town("supplies")
    basic = loop.town("warehouse-items")
    rich = loop.town("warehouse-items", rich=True)
    second_bag = loop.town("supplies")
    if (
        _ownership(bag) != _ownership(second_bag)
        or loop.town("warehouse-locate") != source
    ):
        raise ValueError("Manual storage ownership changed during observation")
    native = {i["uid"]: i for i in basic["items"]}
    full = {i["uid"]: i for i in rich["items"]}
    carried = {i["uid"] for i in bag["items"]}
    if (
        len(native) != len(basic["items"])
        or len(full) != len(rich["items"])
        or set(native) != set(full)
        or basic["bank_silver"] != rich["bank_silver"]
        or basic["capacity"] != rich["capacity"]
    ):
        raise ValueError("Manual warehouse observations disagree")
    for expected in record["expected_items"]:
        uid = expected["uid"]
        if (
            uid in carried
            or uid not in native
            or any(native[uid].get(k) != expected[k] for k in BASIC)
            or any(full[uid].get(k) != expected[k] for k in OPTIONAL if k in expected)
        ):
            raise ValueError("Reported manual deposit lacks the exact warehouse item")
    _safe(loop, record)
    return {"source": source, "bag": bag, "warehouse": basic, "rich_warehouse": rich}


def _proof(loop, record):
    from conquest.restock_town_recovery import _ownership

    first = _sample(loop, record)
    second = _sample(loop, record)
    if _ownership(first["bag"]) != _ownership(second["bag"]) or any(
        first[key] != second[key] for key in ("source", "warehouse", "rich_warehouse")
    ):
        raise ValueError(
            "Manual warehouse ownership is not stable across two observations"
        )
    return {
        "kind": "manual_deposit_observed",
        "observed_at": time.time(),
        "target": record["target"],
        "report_id": record["report_id"],
        "transaction_receipt": False,
        "native_cycle_verified": False,
        "first_sample": first,
        **second,
    }


def _settle_old_records(loop, record):
    from conquest import meteor_banking
    from conquest.merchants.service_visit import MarketVisit
    from conquest.recovery_override import operator_override

    original = record["original_meteor"]
    current = read_json(meteor_banking.JOURNAL)
    reference = "manual-storage-report:" + record["report_id"]
    if current.get("phase") == "operator_overridden":
        saved = current.get("operator_override") or {}
        if (
            saved.get("original_evidence_digest") != evidence_digest(original)
            or saved.get("confirmation_reference") != reference
        ):
            raise ValueError("Meteor manual observation settlement changed")
    else:
        if evidence_digest(current) != evidence_digest(original):
            raise ValueError(
                "Original Meteor trip changed before manual storage settlement"
            )
        operator_override(
            meteor_banking.JOURNAL,
            pending_phases=("storing_scroll",),
            operator_confirmed=True,
            confirmation_reference=reference,
            incident_digest=evidence_digest(original),
            incident="manual-phoenix-storage",
            fresh_evidence={
                "operator_report": record["operator_report"],
                "manual_storage_observation": record["observation"],
            },
        )
    market_path = MarketVisit().path
    market = read_json(market_path)
    if any(
        market.get(k) != record["original_market"].get(k)
        for k in (
            "visit_id",
            "town_visit_id",
            "farmer_profile_id",
            "started_at",
            "deadline",
            "attempts",
        )
    ):
        raise ValueError(
            "Original Market budget changed before manual departure settlement"
        )
    if market.get("phase") == "active":
        market.update(
            phase="departed",
            departed_at=time.time(),
            arrival_map=1011,
            departure_source="manual_return_observed",
            manual_storage_report=record["report_id"],
            native_return_verified=False,
        )
        meteor_banking._durable_json(market_path, market)
    elif (
        market.get("phase") != "departed"
        or market.get("manual_storage_report") != record["report_id"]
    ):
        raise ValueError("Market visit lacks the matching manual departure observation")


def resume(loop):
    """Called by the explicitly started native route, before old Meteor capture."""
    record = read_json(REPORT)
    if not record or record.get("phase") == "completed":
        return False
    if record.get("phase") not in (
        "reported",
        "observed",
        "cash_submitting",
        "cash_verified",
    ):
        raise ValueError("Manual storage continuation has an unknown phase")
    loop.stop_farm()
    _safe(loop, record)
    if record["phase"] == "cash_submitting":
        raise ValueError("Manual storage cash outcome is uncertain; no replay")
    from conquest import banking
    from conquest.overnight import needs_town, supply_counts
    from conquest.town_trade import stash_candidate
    from conquest.restock_town_recovery import _ownership

    row = loop.town_visit.state()
    if row.get("town_work_completed_at"):
        if (
            record["phase"] != "cash_verified"
            or row.get("manual_storage_recovery", {}).get("report_id")
            != record["report_id"]
        ):
            raise ValueError("Town completion lacks its manual storage boundary")
        record.update(phase="completed", completed_at=row["town_work_completed_at"])
        _save(record)
        return True
    loop.adopt_ammunition()
    banking.open_warehouse(loop)
    observation = _proof(loop, record)
    bag = observation["bag"]
    if any(stash_candidate(i) for i in bag["items"]) or needs_town(
        supply_counts(bag, loop.route), loop.route
    ):
        raise ValueError(
            "Manual return still requires storage or supplies; no old purchase replay"
        )
    if record["phase"] == "reported":
        record.update(phase="observed", observation=observation)
        _save(record)
    _settle_old_records(loop, record)
    if record["phase"] == "cash_verified" and _ownership(bag) != _ownership(
        record["tail_bag"]
    ):
        raise ValueError("Manual return ownership changed after its cash boundary")
    if record["phase"] == "observed":
        _safe(loop, record)
        money = loop.town("warehouse-money")
        if money["silver"] != bag["silver"]:
            raise ValueError("Manual return cash changed before submission")
        amount = money["silver"] - banking.transport_reserve()
        direction = "deposit" if amount > 0 else "withdraw"
        amount = amount if amount > 0 else min(-amount, money["stored_silver"])
        receipt = None
        if amount:
            record.update(
                phase="cash_submitting",
                cash_before=money,
                cash_direction=direction,
                cash_amount=amount,
                cash_attempted_at=time.time(),
            )
            _save(record)
            _safe(loop, record)
            receipt = banking.transfer(loop, direction, amount)
            if not isinstance(receipt, dict) or receipt.get("verified") is not True:
                raise ValueError("Manual storage cash outcome is uncertain; no replay")
        tail_bag = loop.town("supplies")
        expected = deepcopy(bag)
        expected["silver"] += amount if direction == "withdraw" else -amount
        if _ownership(tail_bag) != _ownership(expected):
            raise ValueError("Manual return ownership changed across its cash boundary")
        record.update(
            phase="cash_verified",
            cash_receipt=receipt,
            cash_verified_at=time.time(),
            tail_bag=tail_bag,
        )
        _save(record)
    fresh = loop.town("supplies")
    if any(stash_candidate(i) for i in fresh["items"]) or needs_town(
        supply_counts(fresh, loop.route), loop.route
    ):
        raise ValueError("Manual return tail still needs supplies or banking")
    banking.close_warehouse(loop)
    loop.town("close", window="Shop")
    _safe(loop, record)
    if _ownership(loop.town("supplies")) != _ownership(fresh):
        raise ValueError("Manual return ownership changed during panel cleanup")
    from conquest.town_visit import checkpoint_verified_tail

    checkpoint_verified_tail(loop, "restock")
    row = loop.town_visit.state()
    row["manual_storage_recovery"] = {
        "report_id": record["report_id"],
        "observation": record["observation"],
        "native_cycle_verified": False,
    }
    from conquest.meteor_banking import _durable_json

    _durable_json(loop.town_visit.path, row)
    loop.town_visit.complete_town_work("restock")
    record.update(phase="completed", completed_at=time.time())
    _save(record)
    loop.record(
        "manual_storage_reconciled",
        report_id=record["report_id"],
        native_cycle_verified=False,
        activity="Observed manual Phoenix storage; town cash tail complete",
    )
    return True
