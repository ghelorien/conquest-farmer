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

With Scatter too (live 2026-09-27, Toxic): WingedSnakes gave 5,500-7,300 XP a
minute at level 26 against 2,400-2,500 on Poltergeists at 27-28, but about 20
Scatters per kill cost more arrows than their silver returned, while
Poltergeists cleared about 55 silver a minute. The archer levels on the
dearer bracket and refills on the cheaper one; standing the hold down once
Scatter was learned sent it to WingedSnakes broke and stranded it in Phoenix.
"""

import time
from pathlib import Path

from conquest.character_context import state_path
from conquest.discord_notify import read_json, write_json

HOLD = Path(state_path(".runtime/economy-hold.json"))
LOW = 3000
# A refill of 2,000 takes about 40 minutes on Poltergeists (8,000 kept a
# Scatter archer off its leveling spot for two hours).
HIGH = 5000
# The whole next five-level bracket may refill on the previous one.
GRACE_LEVELS = 5
CHECK_SECONDS = 60


def wallet(loop):
    """Silver carried and banked plus what the carried arrows and potions
    cost (last verified prices).

    Silver alone judged every restock poor: at 19:57 Toxic turned 5,436
    silver into 8 arrow packs and potions, banked the rest and read 1,949,
    so the hold kept it off the WingedSnakes it had just paid for.

    Arrows count at what LuckyArrows would replace them for: a dearer tier's
    premium is spent like an upgrade. Valued at IronArrow prices (4.8 each),
    Alex's IronArrows (22:36) burned the wallet ~225 a minute on paper and
    the hold would have sent a level-33 archer to WingedSnakes to refill.
    """
    from conquest.arrow_upgrades import ARROW_REFILL_AMOUNTS, MAX_ARROW_PACKS
    from conquest.banking import STATUS
    from conquest.overnight import last_verified_price, supply_counts

    bag = loop.town("supplies")
    value = bag["silver"] + read_json(STATUS).get("stored_silver", 0)
    route = getattr(loop, "route", None)
    if route is not None:
        counts = supply_counts(bag, route)
        lucky = 1050000
        pack = ARROW_REFILL_AMOUNTS.get(lucky, 0) // MAX_ARROW_PACKS
        arrow_price = last_verified_price(lucky)
        if arrow_price and pack:
            value += counts["arrows"] * arrow_price // pack
        potion_price = last_verified_price(route.supplies.healing_type)
        if potion_price:
            value += counts["potions"] * potion_price
    return value


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
    # Leaving the cheaper bracket needs HIGH; hunting the dearer one continues
    # down to LOW. At 3,200 silver (19:40) Toxic would otherwise have moved to
    # the WingedSnakes and been sent back within minutes of the trip.
    on_previous = getattr(getattr(loop, "route", None), "id", None) == previous.get(
        "saved_route"
    )
    if silver is None:
        held = bool(state.get("active"))
    elif state.get("active") or on_previous:
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
