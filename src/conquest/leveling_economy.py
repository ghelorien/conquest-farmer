"""Hunt a bracket back while the wallet cannot pay for the next one.

Live 2026-09-27 (Toxic, levels 21-22, Back2Classic): Poltergeists used 2.3
potions a minute and cost ~190 silver per hunting minute in potions, arrows
and scrolls while its silver pickups returned ~60; Apparitions cost ~75 and
returned more. The bank ran from 12,248 to 94 silver and a restock could no
longer pay for its arrows, so the farmer sat in town.

While a Back2Classic archer levels (goal or not) and the wallet (carried
plus banked silver) is below LOW, it hunts the previous, cheaper bracket as
long as its
level is at most GRACE_LEVELS past that bracket's top; it moves on once the
wallet is back to HIGH. The hold survives controller restarts.
"""

import time
from pathlib import Path

from conquest.character_context import state_path
from conquest.discord_notify import read_json, write_json

HOLD = Path(state_path(".runtime/economy-hold.json"))
LOW = 3000
HIGH = 8000
GRACE_LEVELS = 2
CHECK_SECONDS = 60


def scatter_learned(loop):
    """Whether memory shows Scatter; unreadable counts as not learned."""
    try:
        from conquest.scatter_training import learned

        return bool(learned(loop))
    except Exception:
        return False


def wallet(loop):
    from conquest.banking import STATUS

    carried = loop.town("supplies")["silver"]
    return carried + read_json(STATUS).get("stored_silver", 0)


def economy_route(loop, level, selected, entry):
    """The route to hunt: ``selected``, or a cheaper one while silver is low."""
    from conquest.equipment import leveling_archer
    from conquest.leveling_routes import bracket, desired_route

    # Back2Classic farmers keep leveling after the goal's Scatter level (the
    # goal ended at 23 and the guard stopped applying, 15:26).
    if not leveling_archer() or entry["levels"][0] <= 1:
        return selected, entry
    previous = bracket(entry["levels"][0] - 1)
    if level > previous["levels"][1] + GRACE_LEVELS:
        if read_json(HOLD).get("active"):
            write_json(HOLD, {"active": False, "ended_at": time.time()})
        return selected, entry
    state = read_json(HOLD)
    now = time.monotonic()
    if now < getattr(loop, "economy_checked_until", 0):
        silver = getattr(loop, "economy_wallet", None)
    else:
        try:
            silver = wallet(loop)
        except (ValueError, OSError, KeyError):
            silver = None
        loop.economy_checked_until = now + CHECK_SECONDS
        loop.economy_wallet = silver
        loop.economy_scatter = scatter_learned(loop)
    # The guard bridges the leveling stretch before Scatter. Once Scatter is
    # learned the plan is to farm the next spot with it (Alex 2026-09-27:
    # "I just manually taught you scatter, now you gotta go to the next
    # training spot").
    if getattr(loop, "economy_scatter", False):
        if state.get("active"):
            write_json(HOLD, {"active": False, "ended_at": time.time(), "reason": "scatter"})
            loop.record(
                "economy_hold_ended",
                silver=silver,
                reason="scatter",
                activity=f"Scatter learned: moving on to {entry['name']}",
            )
        return selected, entry
    if silver is None:
        held = bool(state.get("active"))
    elif state.get("active"):
        held = silver < HIGH
    else:
        held = silver < LOW
    if not held:
        if state.get("active"):
            write_json(HOLD, {"active": False, "ended_at": time.time(), "silver": silver})
            loop.record(
                "economy_hold_ended",
                silver=silver,
                activity=f"{silver:,} silver: moving on to {entry['name']}",
            )
        return selected, entry
    route, previous_entry = desired_route(previous["levels"][1])
    if route is None:
        return selected, entry
    if not state.get("active"):
        write_json(
            HOLD,
            {
                "active": True,
                "route_id": route.id,
                "silver": silver,
                "started_at": time.time(),
            },
        )
        loop.record(
            "economy_hold_started",
            silver=silver,
            held_route=route.id,
            next_route=entry["name"],
            activity=f"Only {silver:,} silver: hunting {previous_entry['name']} "
            f"until {HIGH:,} before {entry['name']}",
        )
    return route, previous_entry
