"""Validate complete browser-collected market snapshots before pricing."""

from conquest.character_context import state_path
from dataclasses import asdict
import json
import re
from pathlib import Path
import time
from conquest.discord_notify import write_json
from conquest.merchants.pricing import ItemKey, Listing, quality, socket_name

SOURCE = "https://conqueronline.net/market"


def market_integer(raw):
    """Parse a complete localized nonnegative integer, never a trailing fragment."""
    raw = raw.strip()
    if not re.fullmatch(r"[0-9]+(?:[,\u00a0\u202f ][0-9]{3})*", raw):
        raise ValueError("Market number format changed")
    return int(re.sub(r"[,\u00a0\u202f ]", "", raw))


class MarketSnapshot:
    def __init__(self, data, *, now=None, max_age=900):
        now = time.time() if now is None else now
        if (
            data.get("source") != SOURCE
            or data.get("server") != "America"
            or data.get("complete") is not True
        ):
            raise ValueError("A complete America market snapshot is required")
        at = data.get("observed_at")
        if type(at) not in (int, float) or not 0 <= now - at <= max_age:
            raise ValueError("Market comparison expired; request a fresh browser scan")
        rows = data.get("listings")
        if (
            not isinstance(rows, list)
            or not 0 < len(rows) <= 50000
            or data.get("total") != len(rows)
        ):
            raise ValueError("Market pagination is incomplete")
        self.data = data
        self.history_catalog = {}
        self.rows, self.listings, self.entries = [], [], []
        for row in rows:
            if (
                row.get("server") != "America"
                or not isinstance(row.get("name"), str)
                or not row["name"]
            ):
                raise ValueError("Market row has wrong server or missing identity")
            key = ItemKey(
                row["category"],
                row["quality"],
                row["plus"],
                tuple(row["sockets"]),
                row.get("currency", "silver"),
            )
            self.rows.append(row)
            if row.get("quantity", 1) is None:
                continue  # A price without an equivalent quantity cannot compete.
            listing = Listing(
                row["seller"], key, row["price"], row.get("quantity", 1), row["server"]
            )
            self.listings.append(listing)
            self.entries.append((row, listing))

    def key_for(self, item):
        equipment = 100000 <= item["type_id"] < 600000
        expected_quality = quality(item["type_id"]) if equipment else None
        sockets = (socket_name(item["gem1"]), socket_name(item["gem2"]))
        # Quality/level/name are not equipment TYPE. Build the type mapping
        # from official item definitions and the observed market subcategories.
        prefix = str(item["type_id"] // 1000)
        if equipment and prefix in self.data.get(
            "ambiguous_equipment_types", []
        ) + self.history_catalog.get("ambiguous_equipment_types", []):
            raise ValueError("Equipment type maps to conflicting market categories")
        mapped = (
            (
                self.data.get("equipment_categories", {}).get(prefix)
                or self.history_catalog.get("equipment_categories", {}).get(prefix)
            )
            if equipment
            else None
        )
        named = [r for r in self.rows if r["name"] == item["name"]]
        if not named:
            named = [
                r
                for r in self.history_catalog.get("rows", [])
                if r["name"] == item["name"]
            ]
        matches = {mapped} if mapped else {r["category"] for r in named}
        if len(matches) != 1:
            raise ValueError("Item category absent or ambiguous in current market")
        category = next(iter(matches))
        if not equipment:
            variants = {r["quality"] for r in named if r["category"] == category}
            if 700001 <= item["type_id"] <= 700073 and item["type_id"] % 10 in (
                1,
                2,
                3,
            ):
                expected_quality = {1: "Normal", 2: "Refined", 3: "Super"}[
                    item["type_id"] % 10
                ]
                if expected_quality not in variants:
                    raise ValueError("Gem quality absent from current market")
            elif len(variants) != 1:
                raise ValueError("Non-equipment quality needs an exact verified match")
            else:
                expected_quality = next(iter(variants))
            # Exact-name grouping for non-equipment, even if a website category
            # puts multiple unrelated consumables together.
            category = f"{category}:{item['name']}"
        return ItemKey(category, expected_quality, item["plus"], sockets)

    def comparisons(self, item):
        if 100000 <= item["type_id"] < 600000:
            return self.listings
        return [
            Listing(
                r.seller,
                ItemKey(
                    f"{r.key.category}:{raw['name']}",
                    r.key.quality,
                    r.key.plus,
                    r.key.sockets,
                ),
                r.price,
                r.quantity,
                r.server,
            )
            for raw, r in self.entries
            if raw["name"] == item["name"]
        ]


def browser_pages(data, definitions):
    """Normalize visible table cells, rejecting changed/incomplete pagination.

    The website does not expose stack counts in its table. Only equipment or
    item types with an official single-item capacity get an inferred count of
    one. Stackable/unknown products remain unavailable for comparisons.
    """
    if (
        data.get("server") != "America"
        or data.get("source") != SOURCE
        or data.get("last_page") is not True
        or data.get("initial_total") != data.get("final_total")
        or data.get("initial_change") != data.get("final_change")
    ):
        raise ValueError(
            "Market changed during collection or the last page was not reached; refresh and retry"
        )
    pages = data.get("pages", [])
    if not pages or [p["page"] for p in pages] != list(range(1, len(pages) + 1)):
        raise ValueError("Market pages are missing, duplicated or out of order")
    rows = [cells for page in pages for cells in page["rows"]]
    if (
        len(rows) != data["initial_total"]
        or any(len(p["rows"]) != 50 for p in pages[:-1])
        or not 1 <= len(pages[-1]["rows"]) <= 50
    ):
        raise ValueError("Market pagination did not match the reported total")
    names = {}
    for definition in definitions:
        names.setdefault(definition["name"], []).append(definition)
    listings = []
    for cells in rows:
        if len(cells) != 7:
            raise ValueError("Market table schema changed")
        labels = [v.strip() for v in cells[0].splitlines() if v.strip()]
        sockets = [v.strip() for v in cells[3].splitlines() if v.strip()]
        if len(labels) != 2 or len(sockets) != 2 or cells[5].strip() != "America":
            raise ValueError("Market item attributes or server are incomplete")
        name, category = labels
        known = names.get(name, [])
        quantity = (
            1
            if known
            and (
                all(100000 <= d["id"] < 600000 for d in known)
                or all(
                    d.get("amountLimit") == 1 and d.get("amount") == 1 for d in known
                )
            )
            else None
        )
        plus = cells[2].strip()
        listings.append(
            {
                "name": name,
                "category": category,
                "quality": cells[1].strip(),
                "plus": 0 if plus == "—" else int(plus.removeprefix("+")),
                "sockets": sockets,
                "seller": cells[4].strip(),
                "server": "America",
                "price": market_integer(cells[6]),
                "quantity": quantity,
                "currency": "silver",
            }
        )
    result = {
        "source": SOURCE,
        "server": "America",
        "complete": True,
        "observed_at": data["observed_at"],
        "total": len(listings),
        "listings": listings,
    }
    equipment_categories = {}
    for row in listings:
        for definition in names.get(row["name"], []):
            if 100000 <= definition["id"] < 600000:
                prefix = str(definition["id"] // 1000)
                equipment_categories.setdefault(prefix, set()).add(row["category"])
    result["equipment_categories"] = {
        p: next(iter(categories))
        for p, categories in equipment_categories.items()
        if len(categories) == 1
    }
    result["ambiguous_equipment_types"] = [
        p for p, categories in equipment_categories.items() if len(categories) != 1
    ]
    MarketSnapshot(result)
    return result


def import_snapshot(source, destination=state_path("reports/merchants/market.json")):
    data = json.loads(Path(source).read_text(encoding="utf-8"))
    snapshot = MarketSnapshot(data)
    write_json(destination, data)
    return {"listings": len(snapshot.rows), "observed_at": data["observed_at"]}
