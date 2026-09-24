"""Verified warehouse-stock outbox; notification only, never delivery/travel."""

import copy
import hashlib
import json
import time

from conquest.merchants.journal import Journal


def _exact(item):
    return {
        k: item.get(k)
        for k in (
            "uid",
            "type_id",
            "plus",
            "gem1",
            "gem2",
            "quantity",
            "bound",
            "name",
            "slot",
        )
    }


def record(loop):
    """Best-effort native evidence after a completed bank visit.

    This deliberately does nothing if a delivery journey/trade is active or
    automatic delivery is enabled: it must not reinterpret protected/active
    transfer stock as a notification candidate.
    """
    from conquest.merchants.farmer_preferences import rollout_enabled
    from conquest.merchants.delivery_journey import pending as journey_pending
    from conquest.merchants.delivery_route import pending as trade_pending

    if rollout_enabled() or journey_pending() or trade_pending():
        return []
    from conquest.merchants.bridge import request

    source = request({"action": "delivery-source"}).get("farmer") or {}
    before = getattr(loop, "identity", None)
    if (
        not source.get("identity")
        or not 0 <= time.time() - source.get("timestamp", 0) <= 5
    ):
        raise ValueError("Fresh farmer source evidence is unavailable")
    if before is None or source["identity"] != before:
        raise ValueError("Bank source process does not match the active farmer")
    warehouse = loop.town("warehouse-items", rich=True)
    loop.health()  # post-rich-read process/identity recheck, no focus/input.
    if getattr(loop, "identity", None) != before:
        raise ValueError("Farmer process changed during rich warehouse observation")
    from conquest.character_context import current, farmer_name

    context = current()
    if source.get("character") != farmer_name():
        raise ValueError("Bank source character differs from selected farmer")
    if (
        context is None
        or context.profile.role != "Farmer"
        or context.profile.name != source.get("character")
    ):
        raise ValueError("Bank source profile differs from selected farmer")
    from conquest.merchants.delivery import eligible
    from conquest.valuables import DRAGONBALL_TYPES
    from conquest.banking import URGENT_EQUIPMENT_FAMILIES

    candidates = []
    for item in warehouse.get("items", []):
        candidate = copy.deepcopy(item)
        candidate["slot"] = 0
        family = int(item.get("type_id", 0)) // 1000
        if (
            item.get("type_id") in DRAGONBALL_TYPES
            or item.get("type_id") == 1088001
            or family in URGENT_EQUIPMENT_FAMILIES
            or not eligible(candidate)
        ):
            continue
        candidates.append(_exact(item))
    if not candidates:
        clear_issue()
        return []
    journal = Journal()
    with journal.db() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS bank_stock_outbox(
            id TEXT PRIMARY KEY, created_at REAL NOT NULL, source_json TEXT NOT NULL,
            warehouse_json TEXT NOT NULL, phase TEXT NOT NULL, message_id TEXT)""")
        db.execute(
            "CREATE TABLE IF NOT EXISTS bank_stock_seen(farmer TEXT NOT NULL,item_hash TEXT NOT NULL,first_seen_at REAL NOT NULL,PRIMARY KEY(farmer,item_hash))"
        )
        farmer = context.profile.id
        exact = lambda item: hashlib.sha256(
            json.dumps(
                {
                    "farmer": farmer,
                    **{
                        k: item.get(k)
                        for k in (
                            "uid",
                            "type_id",
                            "plus",
                            "gem1",
                            "gem2",
                            "quantity",
                            "bound",
                        )
                    },
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        current = {exact(item) for item in candidates}
        previous = {
            row["item_hash"]
            for row in db.execute(
                "SELECT item_hash FROM bank_stock_seen WHERE farmer=?", (farmer,)
            )
        }
        newly = [item for item in candidates if exact(item) not in previous]
        if newly:
            # One compact notification per completed verified-bank snapshot;
            # the baseline keeps old stock out of later batches.
            event = hashlib.sha256(
                json.dumps(sorted(exact(item) for item in newly)).encode()
            ).hexdigest()
            db.execute(
                "INSERT OR IGNORE INTO bank_stock_outbox VALUES(?,?,?,?,?,NULL)",
                (
                    event,
                    time.time(),
                    json.dumps(
                        {
                            "identity": source["identity"],
                            "map_id": source.get("map_id"),
                            "character": source.get("character"),
                            "profile_id": farmer,
                            "observed_at": source.get("timestamp"),
                        }
                    ),
                    json.dumps(newly),
                    "pending",
                ),
            )
        db.executemany(
            "INSERT OR IGNORE INTO bank_stock_seen VALUES(?,?,?)",
            [(farmer, key, time.time()) for key in current],
        )
        if db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='bank_stock_outbox_issue'"
        ).fetchone():
            db.execute("DELETE FROM bank_stock_outbox_issue WHERE id=1")
    return candidates


def record_failure(reason):
    journal = Journal()
    with journal.db() as db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS bank_stock_outbox_issue(id INTEGER PRIMARY KEY CHECK(id=1),note TEXT NOT NULL,updated_at REAL NOT NULL)"
        )
        db.execute(
            "INSERT OR REPLACE INTO bank_stock_outbox_issue VALUES(1,?,?)",
            (str(reason)[:180], time.time()),
        )


def clear_issue():
    journal = Journal()
    with journal.db() as db:
        if db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='bank_stock_outbox_issue'"
        ).fetchone():
            db.execute("DELETE FROM bank_stock_outbox_issue WHERE id=1")


def issue():
    journal = Journal()
    with journal.db() as db:
        if not db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='bank_stock_outbox_issue'"
        ).fetchone():
            return None
        row = db.execute(
            "SELECT note FROM bank_stock_outbox_issue WHERE id=1"
        ).fetchone()
        return row["note"] if row else None


def pending():
    journal = Journal()
    with journal.db() as db:
        if not db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='bank_stock_outbox'"
        ).fetchone():
            return []
        return [
            dict(row)
            for row in db.execute(
                "SELECT * FROM bank_stock_outbox WHERE phase='pending' ORDER BY created_at"
            )
        ]


def acknowledge(key, message_id):
    journal = Journal()
    with journal.db() as db:
        db.execute(
            "UPDATE bank_stock_outbox SET phase='sent',message_id=? WHERE id=? AND phase='pending'",
            (str(message_id), key),
        )
