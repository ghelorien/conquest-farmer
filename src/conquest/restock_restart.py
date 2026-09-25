"""Once-only restart of a restock whose route failed before any transaction.

Exact class (live 2026-09-25, visit 4d2f63a0...): restock() began a town
visit, then failed while still walking to the Warehouseman inside
fund_restock (restock -> fund_restock -> open_warehouse -> travel -> _travel).
Nothing had been bought, sold, deposited, transferred, packed or delivered,
yet every restart was refused because no continuation kind covered "nothing
happened yet".

This continuation proves that from durable evidence only (the visit's event
audit and recorded failure trace, the money ledger, the delivery journal, the
Meteor journal, holds and the original process identity), lets the existing
living()/travel-care path revive a dead farmer, writes a durable once-only
marker, and then runs the normal restock() again for the same visit.  The
restart begins from the beginning; nothing is replayed.  Any later start that
finds the marker without a completed visit refuses.
"""

import sqlite3
import time
from contextlib import closing
from copy import deepcopy
from pathlib import Path

from conquest.discord_notify import read_json

MARKER = "zero_transaction_restart"
# A carried-ownership snapshot taken when the visit began.  Restock visits do
# not record one today; when present it must equal fresh ownership.
START_OWNERSHIP = "restock_start_ownership"
# Events that record movement, care or bookkeeping only.  Anything else,
# including an event this module does not know, means the visit may have
# transacted and refuses.
NON_TRANSACTIONAL = frozenset(
    (
        "started",
        "stopped",
        "failed",
        "manual_handoff_wait",
        "travel",
        "runback_progress",
        "runback_finished",
        "runback_evading",
        "town_path_retry",
        "town_movement_stalled",
        "town_movement_recovery",
        "market_movement_recovery",
        "travel_progress_recovery",
        "route_movement_retry",
        "town_corner_recovery",
        "town_corner_recovered",
        "travel_heal",
        "travel_heal_verified",
        "travel_heal_unconfirmed",
        "travel_heal_close_deferred",
        "travel_healing_empty",
        "travel_panel_closed",
        "travel_revive",
        "xp_skill_state",
        "xp_fly_attempt",
        "xp_fly_verified",
        "town_observation_retry",
    )
)
READ_ONLY_RETRIES = frozenset(
    (
        "supplies",
        "shop",
        "gear",
        "vendor-status",
        "service-locate",
        "service-dialog",
        "warehouse-items",
        "warehouse-locate",
        "close",
        "clear-travel-panels",
        "consume-healing",
    )
)
# restock() -> ... -> the walk to the Warehouseman, before its panel opens.
PRE_TRANSACTION_CHAIN = (
    ("overnight.py", "restock"),
    ("banking.py", "fund_restock"),
    ("banking.py", "open_warehouse"),
    ("overnight.py", "travel"),
    ("overnight.py", "_travel"),
)
# Frames that may lie beneath _travel: movement, observation and care only.
TRAVEL_MODULES = frozenset(
    (
        "travel_care.py",
        "route_input.py",
        "worker.py",
        "travel_progress.py",
        "navigation.py",
        "scene_input.py",
        "viewport.py",
        "runback_monitor.py",
        "town_corner.py",
        "market_navigation.py",
        "xp_skill.py",
        "memory_health.py",
        "memory_life.py",
        "addressing.py",
    )
)
TRAVEL_LOOP_METHODS = frozenset(
    ("living", "health", "town", "focus", "check_stop", "refresh", "record")
)
# Read-only gates in _run_route whose refusal can follow the original failure.
RESTART_REFUSALS = (
    ("town_visit.py", "require_town_work_complete"),
    ("restock_restart.py", None),
)


def _number(value):
    import math

    return type(value) in (int, float) and math.isfinite(value)


def _frames(event):
    return [
        (Path(str(f.get("file", ""))).name, f.get("function"))
        for f in event.get("failure_trace") or []
        if isinstance(f, dict)
    ]


def _visit_events(visit_id):
    from conquest import restock_town_recovery as recovery

    path = Path(recovery.EVENTS)
    if not path.is_file():
        raise ValueError("Restock restart needs the route event audit")
    import json

    rows = []
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if visit_id not in line:
                continue  # Cheap pre-filter of a large append-only audit.
            event = json.loads(line)
            if event.get("town_visit_id") == visit_id:
                rows.append(event)
    return rows


def _pre_transaction_failure(frames):
    """The original failure happened while walking to the Warehouseman."""
    if frames.count(PRE_TRANSACTION_CHAIN[0]) != 1:
        return False
    at = frames.index(PRE_TRANSACTION_CHAIN[0])
    chain = tuple(frames[at : at + len(PRE_TRANSACTION_CHAIN)])
    if chain != PRE_TRANSACTION_CHAIN:
        return False
    return all(
        name in TRAVEL_MODULES
        or name == "overnight.py"
        and function in TRAVEL_LOOP_METHODS
        for name, function in frames[at + len(PRE_TRANSACTION_CHAIN) :]
    )


def _restart_refusal(frames):
    if ("overnight.py", "_run_route") not in frames or PRE_TRANSACTION_CHAIN[
        0
    ] in frames:
        return False
    at = frames.index(("overnight.py", "_run_route"))
    if at + 1 >= len(frames):
        return False
    name, function = frames[at + 1]
    return any(
        name == gate and (wanted is None or function == wanted)
        for gate, wanted in RESTART_REFUSALS
    )


def _event_proof(row):
    """Exactly one pre-transaction route failure and nothing transactional."""
    events = _visit_events(row["town_visit_id"])
    if any(not _number(e.get("time")) for e in events):
        raise ValueError("Restock event audit has an invalid time")
    for event in events:
        name = event.get("event")
        if name not in NON_TRANSACTIONAL:
            raise ValueError(
                f"Restock visit recorded {name!r}; it may have transacted, reconcile it"
            )
        if name == "town_observation_retry" and (
            event.get("action") not in READ_ONLY_RETRIES
        ):
            raise ValueError("Restock visit retried a transactional town action")
    failures = sorted(
        (e for e in events if e.get("event") == "failed"), key=lambda e: e["time"]
    )
    if not failures:
        raise ValueError("Restock visit has no recorded route failure")
    original = failures[0]
    if original["time"] < row["required_at"] or not _pre_transaction_failure(
        _frames(original)
    ):
        raise ValueError(
            "Restock failure is not a pre-transaction Warehouseman approach"
        )
    if any(not _restart_refusal(_frames(e)) for e in failures[1:]):
        raise ValueError("A later restock failure needs reconciliation")
    return original


def _journal_proof(row):
    """No money, delivery, Meteor, Market or other hold since the visit began."""
    from conquest import banking, meteor_banking
    from conquest import restock_town_recovery as recovery
    from conquest.merchants import delivery_operation
    from conquest.merchants.service_visit import MarketVisit

    required = row["required_at"]
    if any(
        not _number(entry.get("time")) or entry["time"] >= required
        for entry in recovery._rows(Path(banking.LEDGER), allow_missing=True)
    ):
        raise ValueError("A money transfer during this restock needs reconciliation")
    journal = Path(delivery_operation.JOURNAL)
    if journal.exists():
        try:
            with closing(
                sqlite3.connect(journal.resolve().as_uri() + "?mode=ro", uri=True)
            ) as db:
                touched = db.execute(
                    "SELECT 1 FROM transactions WHERE created>=? OR updated>=? "
                    "UNION ALL SELECT 1 FROM delivery_admissions "
                    "WHERE created>=? OR updated>=? LIMIT 1",
                    (required, required, required, required),
                ).fetchone()
        except sqlite3.Error as error:
            raise ValueError("Merchant delivery journal is unreadable") from error
        if touched:
            raise ValueError("A merchant delivery touched this restock visit")
    if delivery_operation.pending():
        raise ValueError("A merchant delivery is unsettled")
    if meteor_banking.pending():
        raise ValueError("Meteor banking is pending")
    started = read_json(meteor_banking.JOURNAL).get("started_at")
    if started is not None and (not _number(started) or started >= required):
        raise ValueError("Meteor banking started during this restock visit")
    if read_json(MarketVisit().path).get("town_visit_id") == row["town_visit_id"]:
        raise ValueError("This restock visit has a Market service visit")
    if recovery._other_holds():
        raise ValueError("Transaction or storage holds block the restock restart")


def _safe_living(loop, target):
    """Fresh, living, unfenced original farmer on the hunt or restock map."""
    from conquest import restock_town_recovery as recovery

    life = (loop.health().get("embedded_controls") or {}).get("life") or {}
    maps = (loop.route.map_id, loop.route.restock_map_id)
    if life.get("map_id") not in maps:
        raise ValueError("Revived farmer is not on the hunt or restock map")
    recovery._native_tail_safe(loop, target, life["map_id"])
    return life


def resume(loop):
    """Restart one proved zero-transaction restock visit, at most once."""
    from conquest import restock_town_recovery as recovery
    from conquest.restock_cash_tail import CLAIM, OTHER_KINDS
    from conquest.town_visit import _process_identity

    visit = loop.town_visit
    row = visit.state()
    if (
        row.get("phase") != "town_work"
        or row.get("reasons") != ["restock"]
        or row.get("town_work_completed_at")
    ):
        return False
    if row.get(MARKER):
        raise ValueError(
            "Zero-transaction restock restart was already attempted; "
            "reconcile before any replay"
        )
    if any(row.get(field) is not None for field in (*OTHER_KINDS, CLAIM)):
        return False
    if not _number(row.get("required_at")) or not row.get("town_visit_id"):
        raise ValueError("Restock visit lacks its start time or identity")
    failure = _event_proof(row)
    _journal_proof(row)
    target = row.get("restock_target")
    if (
        not _process_identity(target)
        or row.get("route_id") != loop.route.id
        or row.get("hunt_map_id") != loop.route.map_id
    ):
        raise ValueError("Restock restart lacks its original process and route")
    loop.check_stop()
    health = loop.health()
    if health.get("target") != target or loop.identity != target:
        raise ValueError("Restock restart process differs from the original restock")
    loop.stop_farm()
    # A dead farmer is revived here by the existing living()/travel-care path
    # (or the app's native recovery while Farming is On) before any proof
    # that needs a living character.  Reviving transfers nothing.
    loop.living()
    _safe_living(loop, target)
    snapshot = row.get(START_OWNERSHIP)
    if snapshot is not None and recovery._ownership(
        loop.town("supplies")
    ) != recovery._ownership(snapshot):
        raise ValueError("Carried ownership differs from the restock visit start")
    # Re-prove everything that could have changed while reviving.
    if visit.state() != row:
        raise ValueError("Restock visit changed during restart proof")
    if _event_proof(row) != failure:
        raise ValueError("Restock failure evidence changed during restart proof")
    _journal_proof(row)
    life = _safe_living(loop, target)
    row[MARKER] = {
        "target": deepcopy(target),
        "failure": {
            k: failure.get(k) for k in ("time", "detail", "error_type", "phase")
        },
        "map_id": life["map_id"],
        "position": list(life.get("position") or []),
        "claimed_at": time.time(),
    }
    recovery._save_tail(visit, row)  # Durable before restock()'s first input.
    loop.record(
        "restock_restart_started",
        failed_at=failure["time"],
        activity="Restarting a restock that stopped before any transaction",
    )
    loop.restock()
    return True
