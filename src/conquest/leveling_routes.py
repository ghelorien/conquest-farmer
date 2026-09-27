"""Level brackets and verified-memory level selection for reusable routes."""

import json
from pathlib import Path
import time
from conquest.worker import request
from conquest.routes import RouteLibrary, monster_family


def presets(path="profiles/leveling-presets.json"):
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    expected = 1
    for row in rows:
        low, high = row["levels"]
        if low != expected or not low <= high <= 140:
            raise ValueError("Level brackets have a gap or overlap")
        expected = high + 1
    if expected != 141:
        raise ValueError("Level brackets must cover levels 1 through 140")
    return rows


def bracket(level, rows=None):
    if type(level) is not int or not 1 <= level <= 140:
        raise ValueError("Invalid observed character level")
    return next(
        r
        for r in (presets() if rows is None else rows)
        if r["levels"][0] <= level <= r["levels"][1]
    )


# An archer with Scatter levels fastest a few levels below its own bracket:
# dense groups die several to a cast. Live 2026-09-27 (Toxic, level 26, fresh
# Scatter): Poltergeists 14-16 kills a minute on 0.9 potions a minute,
# WingedSnakes 2.3 a minute (187 Scatters for 12 kills, out of arrows in five
# minutes).
SCATTER_LEVEL_OFFSET = 3


def scatter_hunting_level(level, rows=None):
    """hunting_level for an archer farming with Scatter."""
    return hunting_level(max(1, level - SCATTER_LEVEL_OFFSET), rows)


def hunting_level(level, rows=None):
    """The level whose zone to hunt: reaching a bracket's top level moves on to
    the next zone (user rule: Pheasants 1-6 hand over to Turtledoves at 6)."""
    top = bracket(level, rows)["levels"][1]
    return level + 1 if level == top and level < 140 else level


def read_level(info, health):
    data = health["embedded_controls"]
    life = data.get("life")
    if (
        not life
        or life["dead_candidate"]
        or not 0 <= time.time() - data.get("observed_at", 0) <= 1
    ):
        raise ValueError("Current living memory state required for route selection")
    # The worker reads the level through the client build's qualified player
    # layout and archer identity (1078 keeps it at +0x6F8). The fixed +0x6E8
    # read 0 live, which silently rejected every automatic route change.
    level = request(info, "town", {"action": "gear"})["level"]
    fresh = request(info, "health")["embedded_controls"]
    latest = fresh.get("life")
    if (
        not latest
        or latest["object_address"] != life["object_address"]
        or latest["dead_candidate"]
        or not 0 <= time.time() - fresh.get("observed_at", 0) <= 1
    ):
        raise ValueError("Character changed during route level observation")
    bracket(level)
    return level


def desired_route(level, library=None):
    entry = bracket(level)
    if not entry.get("saved_route"):
        return None, entry
    route = (library or RouteLibrary()).load(entry["saved_route"])
    if tuple(route.recommended_levels) != tuple(entry["levels"]) or tuple(
        route.monster_type_ids
    ) != tuple(m["type_id"] for m in monster_family(entry["monster_type_id"])):
        raise ValueError(
            "Saved route disagrees with its level bracket or monster group"
        )
    return route, entry
