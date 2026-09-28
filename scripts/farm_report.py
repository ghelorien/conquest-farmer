"""Per-trip farming report from a character's route events.

A trip runs from one completed restock to the last hunting snapshot before
the next town visit. For each trip: route, level, minutes, kills, XP per
minute (level-ups included through the requirement the snapshots carry),
silver picked up, arrows and potions used at their last verified prices,
net silver per minute, and deaths.

    python -B scripts/farm_report.py <character data dir> [hours]

Built from the one-off analyses of 2026-09-27 that decided jump-Scatter
spacing, the WingedSnake move and the IronArrow question.
"""

import json
import sys
import time
from pathlib import Path

LUCKY = 1050000
ARROW_PACK = {1050000: 200, 1050001: 1000, 1050002: 5000}
DEATH_EVENTS = ("travel_revive", "death_detected", "revived", "death_returned")


def read_events(path, since):
    rows = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("time", 0) >= since:
                rows.append(event)
    return rows


def catalog_prices(path=Path(__file__).resolve().parents[1] / "profiles" / "archer-shop-catalog.json"):
    """Arrow pack prices from the recorded Blacksmith catalog."""
    try:
        cities = json.loads(Path(path).read_text(encoding="utf-8"))["cities"]
    except (OSError, ValueError, KeyError):
        return {}
    found = {}
    for city in cities.values():
        for product in (city.get("5") or {}).get("products") or []:
            if product.get("type_id") in ARROW_PACK and type(product.get("price")) is int:
                found.setdefault(product["type_id"], product["price"])
    return found


def prices(rows):
    """Last verified purchase price of each type (per purchase unit). The
    Blacksmith review buys upgraded arrows without a purchase event, so the
    recorded catalog fills arrow prices."""
    found = catalog_prices()
    for event in rows:
        receipt = event.get("receipt") or {}
        if "purchase" in event.get("event", "") and type(receipt.get("price")) is int:
            found[receipt.get("bought")] = receipt["price"]
    return found


def xp_gained(snapshots):
    """XP across snapshots, adding the rest of each level at a level-up."""
    gained = 0
    for before, after in zip(snapshots, snapshots[1:]):
        a, b = before.get("experience") or {}, after.get("experience") or {}
        xa, xb = a.get("experience_candidate"), b.get("experience_candidate")
        if xa is None or xb is None:
            continue
        if a.get("level") == b.get("level"):
            gained += max(0, xb - xa)
        elif (b.get("level") or 0) == (a.get("level") or 0) + 1 and a.get("experience_required"):
            gained += max(0, a["experience_required"] - xa) + xb
    return gained


def trips(rows):
    """Split the route events into trips (restock_complete to the next town)."""
    result, current, route, arrow_type = [], None, None, LUCKY
    for event in rows:
        kind = event.get("event")
        named = None
        if kind in ("supply_plan", "level_route_changed"):
            named = event.get("route")
        elif kind == "route_hold_active":
            named = (event.get("route_hold") or {}).get("route_id")
        elif kind == "level_bracket_checked" and route is None:
            # Only a first guess: the bracket names the level's desired
            # route, which a route hold (Toxic at 36, 2026-09-28) or a
            # missing saved route keeps from being hunted.
            named = event.get("level_bracket")
        if named:
            route = named
            if current is not None and not current["hunts"]:
                current["route"] = route
            elif current is not None and route != current["route"].split(">")[-1]:
                current["route"] += ">" + route  # moved on during the trip
        elif kind == "ammunition_selected":
            arrow_type = event.get("arrow_type", arrow_type)
        elif kind == "arrows_upgraded":
            arrow_type = (event.get("receipt") or {}).get("type_id", arrow_type)
        if current is not None and not current["hunts"]:
            current["arrow_type"] = arrow_type
        if kind == "restock_complete":
            if current and current["hunts"]:
                result.append(current)
            current = {
                "start": event["time"],
                "route": route,
                "arrow_type": arrow_type,
                "supplies": event.get("supplies") or {},
                "hunts": [],
                "deaths": 0,
            }
        elif current is not None:
            if kind == "hunting":
                current["hunts"].append(event)
            elif kind in DEATH_EVENTS:
                current["deaths"] += 1
    if current and current["hunts"]:
        result.append(current)
    return result


def summarize(trip, price):
    first, last = trip["supplies"], trip["hunts"][-1]
    end = last.get("supplies") or {}
    minutes = max((last["time"] - trip["start"]) / 60, 0.01)
    arrows = max(0, first.get("arrows", 0) - end.get("arrows", 0))
    potions = max(0, first.get("potions", 0) - end.get("potions", 0))
    kind = trip["arrow_type"]
    per_arrow = price.get(kind, 0) / ARROW_PACK.get(kind, 1) if price.get(kind) else 0
    potion_price = max((p for t, p in price.items() if 1000000 <= (t or 0) < 1001000), default=0)
    income = end.get("silver", 0) - first.get("silver", 0)
    cost = arrows * per_arrow + potions * potion_price
    levels = [(h.get("experience") or {}).get("level") for h in trip["hunts"]]
    levels = [lv for lv in levels if lv]
    return {
        "start": time.strftime("%H:%M", time.localtime(trip["start"])),
        "route": trip["route"],
        "level": f"{min(levels)}-{max(levels)}" if levels else "?",
        "minutes": round(minutes, 1),
        "kills": (last.get("kills") or 0) - (trip["hunts"][0].get("kills") or 0),
        "xp_per_min": round(xp_gained(trip["hunts"]) / minutes),
        "silver_in": income,
        "supply_cost": round(cost),
        "net_per_min": round((income - cost) / minutes),
        "deaths": trip["deaths"],
    }


def report(character_dir, hours=6.0):
    events = Path(character_dir) / "reports" / "overnight" / "events.jsonl"
    rows = read_events(events, time.time() - hours * 3600)
    price = prices(rows)
    return [summarize(trip, price) for trip in trips(rows)]


def main(argv):
    if len(argv) < 2:
        raise SystemExit(__doc__)
    rows = report(argv[1], float(argv[2]) if len(argv) > 2 else 6.0)
    columns = ["start", "route", "level", "minutes", "kills", "xp_per_min",
               "silver_in", "supply_cost", "net_per_min", "deaths"]
    print("  ".join(f"{c:>11}" for c in columns))
    for row in rows:
        print("  ".join(f"{str(row[c]):>11}" for c in columns))


if __name__ == "__main__":
    main(sys.argv)
