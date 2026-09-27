"""Size a leveling restock from what this route actually consumes.

Potions and LuckyArrow packs share the bag. Live on 2026-09-27, Toxic used
0.41 potions and 50 arrows a minute on Apparitions (level 20), and 2.3
potions and 50 arrows a minute on Poltergeists (level 21). The fixed 20
potions and 8 packs therefore lasted ~32 minutes on the first route but ~7
on the second, where every town trip was for potions while most arrows came
back unused.

Each restock learns the finished hunt's rates for its route (from the end of
the previous restock to this one, travel included) and splits the free bag
so that potions and arrows run out together. Without a measurement the
route's fixed targets stay.
"""

import time
from pathlib import Path

from conquest.character_context import state_path
from conquest.discord_notify import read_json, write_json

RATES = Path(state_path(".runtime/supply-rates.json"))
# Weight of the newest hunt in the learned rates.
SMOOTHING = 0.5
# A shorter hunt (a gear-review trip, a restart) is too noisy to learn from.
MIN_MINUTES = 3
# TwinCityGate scrolls the restock keeps (return_scroll.stock buys two).
SCROLL_SLOTS = 2
MIN_PACKS = 2


def begin_hunt(route_id, counts, now=None):
    """Remember the supplies a hunt leaves town with."""
    data = read_json(RATES)
    data["current"] = {
        "route": route_id,
        "at": time.time() if now is None else now,
        "potions": counts["potions"],
        "arrows": counts["arrows"],
    }
    write_json(RATES, data)


def end_hunt(route_id, counts, now=None):
    """Learn the finished hunt's consumption; the new rates, or None."""
    data = read_json(RATES)
    current = data.pop("current", None)
    now = time.time() if now is None else now
    learned = None
    if current and current.get("route") == route_id:
        minutes = (now - current["at"]) / 60
        potions = current["potions"] - counts["potions"]
        arrows = current["arrows"] - counts["arrows"]
        # A death, a manual purchase or a restart can make a hunt meaningless.
        if minutes >= MIN_MINUTES and potions >= 0 and arrows >= 0:
            fresh = {"potions_per_min": potions / minutes, "arrows_per_min": arrows / minutes}
            routes = data.setdefault("routes", {})
            old = routes.get(route_id)
            if old:
                fresh = {
                    key: SMOOTHING * fresh[key] + (1 - SMOOTHING) * old[key]
                    for key in fresh
                }
            learned = {
                **fresh,
                "hunts": (old or {}).get("hunts", 0) + 1,
                "updated_at": now,
            }
            routes[route_id] = learned
    write_json(RATES, data)
    return learned


def plan(rates, *, bag_slots, pack_size, max_packs, reserve, min_packs=MIN_PACKS):
    """(minutes, potions, packs) lasting longest before either runs out.

    The equipped pack takes no bag slot; ``reserve`` potions are kept for the
    way back and never counted as hunting supply.
    """
    potion_rate, arrow_rate = rates["potions_per_min"], rates["arrows_per_min"]
    best = None
    for packs in range(min_packs, max_packs + 1):
        potions = bag_slots - (packs - 1)
        if potions <= reserve:
            break
        minutes = min(
            (potions - reserve) / potion_rate if potion_rate > 0 else float("inf"),
            packs * pack_size / arrow_rate if arrow_rate > 0 else float("inf"),
        )
        if best is None or minutes > best[0] + 1e-9:
            best = (minutes, potions, packs)
    return best


def balance(loop):
    """Learn the finished hunt, then set this restock's potion and arrow targets."""
    from conquest.arrow_upgrades import (
        ARROW_REFILL_AMOUNTS,
        MAX_ARROW_PACKS,
        NORMAL_ARROWS,
        max_arrow_packs,
    )
    from conquest.equipment import leveling_archer
    from conquest.overnight import potion_reserve, supply_counts
    from conquest.potion_tiers import HEALING_POTIONS
    from conquest.return_scroll import TYPE as SCROLL

    snapshot = loop.town("supplies")
    counts = supply_counts(snapshot, loop.route)
    learned = end_hunt(loop.route.id, counts)
    supplies = loop.route.supplies
    kind = supplies.arrow_type
    if kind != 1050000 or not leveling_archer():
        return None
    rates = learned or read_json(RATES).get("routes", {}).get(loop.route.id)
    if not rates:
        return None
    others = sum(
        1
        for item in snapshot["items"]
        if item["type_id"] not in HEALING_POTIONS
        and item["type_id"] not in NORMAL_ARROWS
        and item["type_id"] != SCROLL
    )
    bag_slots = snapshot["capacity"] - supplies.minimum_free_slots - others - SCROLL_SLOTS
    pack_size = ARROW_REFILL_AMOUNTS[kind] // MAX_ARROW_PACKS
    best = plan(
        rates,
        bag_slots=bag_slots,
        pack_size=pack_size,
        max_packs=max_arrow_packs(kind),
        reserve=potion_reserve(loop.route),
    )
    if best is None:
        return None
    minutes, potions, packs = best
    potions = max(potions, supplies.healing_return_below + 1)
    # adopt_ammunition (run again during the Blacksmith review) keeps this.
    loop.planned_arrows = {kind: packs * pack_size}
    loop.route = loop.route.model_copy(
        update={
            "supplies": supplies.model_copy(
                update={
                    "healing_restock_to": potions,
                    "arrows_restock_to": packs * pack_size,
                }
            )
        }
    )
    loop.record(
        "supply_plan",
        route=loop.route.id,
        potions=potions,
        arrow_packs=packs,
        expected_minutes=round(minutes, 1),
        rates={k: round(v, 3) for k, v in rates.items() if k.endswith("_per_min")},
        activity=f"Restocking {potions} potions and {packs} arrow packs "
        f"(~{minutes:.0f} min at this route's rates)",
    )
    return best
