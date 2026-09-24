"""Shared durable market observations, never repeatedly discounted targets."""

from conquest.character_context import state_path
from collections import defaultdict
from dataclasses import asdict, replace
import json
from pathlib import Path
import sqlite3

from conquest.merchants.pricing import ItemKey, price_item


class PriceHistory:
    def __init__(self, path=state_path("reports/merchants/price-history.sqlite3")):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path, timeout=5) as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS quotes(key TEXT PRIMARY KEY, unit_price TEXT NOT NULL,
                    observed_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS catalog(id INTEGER PRIMARY KEY, data TEXT NOT NULL,
                    observed_at REAL NOT NULL);
            """)

    def remember(self, market):
        """Accept an already validated complete snapshot; preserve original age."""
        at = market.data["observed_at"]
        groups = defaultdict(list)
        for raw, row in market.entries:
            groups[row.key].append(row)
            exact = replace(
                row, key=replace(row.key, category=f"{row.key.category}:{raw['name']}")
            )
            groups[exact.key].append(exact)
        quotes = []
        for key, rows in groups.items():
            decision = price_item(key, rows)
            if decision.reference is not None:
                quotes.append(
                    (json.dumps(asdict(key), sort_keys=True), decision.reference, at)
                )
        with sqlite3.connect(self.path, timeout=5) as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute(
                "SELECT data,observed_at FROM catalog WHERE id=1"
            ).fetchone()
            if previous and previous[1] >= at:
                return  # Idempotent across merchants/restarts; no age laundering.
            catalog = json.loads(previous[0]) if previous else {}
            mappings = catalog.get("equipment_categories", {})
            mappings.update(market.data.get("equipment_categories", {}))
            rows = {
                (r["name"], r["category"], r["quality"]): r
                for r in catalog.get("rows", [])
            }
            rows.update(
                {
                    (r["name"], r["category"], r["quality"]): {
                        k: r[k] for k in ("name", "category", "quality")
                    }
                    for r in market.rows
                }
            )
            ambiguous = set(catalog.get("ambiguous_equipment_types", []))
            ambiguous.update(market.data.get("ambiguous_equipment_types", []))
            catalog = dict(
                equipment_categories=mappings,
                rows=list(rows.values()),
                ambiguous_equipment_types=sorted(ambiguous),
            )
            db.executemany(
                """INSERT INTO quotes VALUES(?,?,?) ON CONFLICT(key) DO UPDATE
                SET unit_price=excluded.unit_price, observed_at=excluded.observed_at
                WHERE excluded.observed_at > quotes.observed_at""",
                quotes,
            )
            db.execute(
                "INSERT OR REPLACE INTO catalog VALUES(1,?,?)",
                (json.dumps(catalog), at),
            )

    def quotes(self):
        with sqlite3.connect(self.path, timeout=5) as db:
            rows = db.execute(
                "SELECT key,unit_price,observed_at FROM quotes"
            ).fetchall()
        result = {}
        for encoded, price, at in rows:
            key = json.loads(encoded)
            key["sockets"] = tuple(key["sockets"])
            result[ItemKey(**key)] = {"unit_price": price, "observed_at": at}
        return result

    def catalog(self):
        with sqlite3.connect(self.path, timeout=5) as db:
            row = db.execute("SELECT data FROM catalog WHERE id=1").fetchone()
        return json.loads(row[0]) if row else {}
