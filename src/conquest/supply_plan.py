"""Size a leveling restock from what this route actually consumes.

Potions and arrow packs (LuckyArrows, IronArrows) share the bag. Live on
2026-09-27, Toxic used 0.41 potions and 50 arrows a minute on Apparitions
(level 20), and 2.3
potions and 50 arrows a minute on Poltergeists (level 21). The fixed 20
potions and 8 packs therefore lasted ~32 minutes on the first route but ~7
on the second, where every town trip was for potions while most arrows came
back unused.

Each restock learns the finished hunt's rates for its route (from the end of
the previous restock to this one, travel included) and splits the free bag
so that potions and arrows run out together. Without a measurement the
route's fixed targets stay.
"""

import math
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
# When arrows end the hunt, potions beyond twice its measured use (above the
# way-back reserve, never under MIN_HUNT_POTIONS) only spend silver and bag
# room: jump-Scatter on WingedSnakes used no potion in 17 minutes (20:11-20:28,
# 2026-09-27), yet the restock bought 27 and left Suicide 5 free slots, so one
# loot drop meant a town trip.
POTION_MARGIN = 2
MIN_HUNT_POTIONS = 10


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


def plan(
    rates,
    *,
    bag_slots,
    pack_size,
    max_packs,
    reserve,
    min_packs=MIN_PACKS,
    budget=None,
    potion_price=None,
    pack_price=None,
    carried_potions=0,
    carried_packs=0,
    carried_arrows=None,
):
    """(minutes, potions, packs) lasting longest before either runs out.

    The equipped pack takes no bag slot; ``reserve`` potions are kept for the
    way back and never counted as hunting supply. With a ``budget`` the
    silver is split the same way: short of silver the restock bought ~30
    potions first and could pay for 318 arrows, six minutes of shooting
    (live 2026-09-27 15:36). ``carried_arrows``: what the carried packs
    really hold (a partial pack is not ``pack_size`` arrows).
    """
    potion_rate, arrow_rate = rates["potions_per_min"], rates["arrows_per_min"]
    best = None
    for packs in range(1 if budget is not None else min_packs, max_packs + 1):
        potions = bag_slots - (packs - 1)
        if budget is not None:
            spent = max(0, packs - carried_packs) * pack_price
            if spent > budget:
                break
            potions = min(potions, carried_potions + (budget - spent) // potion_price)
        if potions <= reserve:
            if budget is None:
                break
            continue
        arrows = (
            carried_arrows
            if carried_arrows is not None
            else min(packs, carried_packs) * pack_size
        ) + max(0, packs - carried_packs) * pack_size
        minutes = min(
            (potions - reserve) / potion_rate if potion_rate > 0 else float("inf"),
            arrows / arrow_rate if arrow_rate > 0 else float("inf"),
        )
        if best is None or minutes > best[0] + 1e-9:
            best = (minutes, potions, packs)
    if best is not None and math.isfinite(best[0]):
        minutes, potions, packs = best
        needed = reserve + math.ceil(potion_rate * minutes * POTION_MARGIN)
        best = (minutes, min(potions, max(needed, MIN_HUNT_POTIONS)), packs)
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
    if kind not in NORMAL_ARROWS or not leveling_archer():
        return None
    rates = learned or read_json(RATES).get("routes", {}).get(loop.route.id)
    if not rates:
        return None
    from conquest.arrow_upgrades import arrow_pack_price, counted_tiers
    from conquest.overnight import last_verified_price

    # IronArrows too (live 2026-09-27 22:54): unplanned, a restarted
    # controller kept bandit.yaml's 5 potions, Suicide left with 5 and a
    # one-potion way back, and died walking home at 23:12 with none left.
    pack_price = (
        last_verified_price(kind)
        or arrow_pack_price(kind)
        or (200 if kind == 1050000 else None)
    )
    if not pack_price:
        return None
    from conquest.banking import STATUS, transport_reserve

    # Silver this restock may spend on potions and packs: carried and banked,
    # less the Conductress fare and, without one carried, a return scroll.
    scroll_carried = any(
        item["type_id"] == SCROLL and item["amount"] > 0 for item in snapshot["items"]
    )
    budget = max(
        0,
        snapshot["silver"]
        + read_json(STATUS).get("stored_silver", 0)
        - transport_reserve()
        - (0 if scroll_carried else 200),
    )
    ammo = snapshot.get("equipped_ammo")

    def carried(tiers):
        """Arrows of ``tiers`` in the bag and quiver (remnants excluded)."""
        stacks = [
            i["amount"]
            for i in snapshot["items"]
            if i["type_id"] in tiers and i["amount"] >= 3
        ]
        if ammo and ammo["type_id"] in tiers and ammo["amount"] >= 3:
            stacks.append(ammo["amount"])
        return sum(stacks)

    lucky = 1050000
    if (
        kind != lucky
        and pack_price > budget
        and carried(counted_tiers(kind)) < ARROW_REFILL_AMOUNTS[kind] // MAX_ARROW_PACKS // 2
    ):
        # No pack of this tier is affordable, so the Blacksmith falls back to
        # LuckyArrow (arrow_tier_fallback). Plan that tier's 200-arrow packs:
        # planned as one IronArrow pack, Suicide's restock bought 33 potions
        # first and then had bag room for one 200-arrow pack, ~2 minutes of
        # Scatter (2026-09-30 05:25, 3.9k silver).
        lucky_price = last_verified_price(lucky) or arrow_pack_price(lucky) or 200
        if lucky_price <= budget:
            kind, pack_price = lucky, lucky_price
    # Lower-tier stacks kept as a fallback hold their slots.
    tiers = counted_tiers(kind)
    others = sum(
        1
        for item in snapshot["items"]
        if item["type_id"] not in HEALING_POTIONS
        and (item["type_id"] not in NORMAL_ARROWS or item["type_id"] not in tiers)
        and item["type_id"] != SCROLL
    )
    bag_slots = snapshot["capacity"] - supplies.minimum_free_slots - others - SCROLL_SLOTS
    pack_size = ARROW_REFILL_AMOUNTS[kind] // MAX_ARROW_PACKS
    from conquest.arrow_upgrades import arrow_pack_count

    best = plan(
        rates,
        bag_slots=bag_slots,
        pack_size=pack_size,
        max_packs=max_arrow_packs(kind),
        reserve=potion_reserve(loop.route),
        budget=budget,
        potion_price=last_verified_price(supplies.healing_type)
        or HEALING_POTIONS.get(supplies.healing_type, (None, None, 60))[2],
        pack_price=pack_price,
        carried_potions=counts["potions"],
        # A remnant that cannot fire Scatter is recycled, not a paid pack
        # (an IronArrow pack is 4,800 silver).
        carried_packs=arrow_pack_count(
            {
                **snapshot,
                "items": [
                    i
                    for i in snapshot["items"]
                    if i["type_id"] not in NORMAL_ARROWS or i["amount"] >= 3
                ],
                "equipped_ammo": ammo if ammo and ammo["amount"] >= 3 else None,
            },
            kind,
        ),
        carried_arrows=carried(tiers),
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
        arrow_type=kind,
        expected_minutes=round(minutes, 1),
        rates={k: round(v, 3) for k, v in rates.items() if k.endswith("_per_min")},
        activity=f"Restocking {potions} potions and {packs} arrow packs "
        f"(~{minutes:.0f} min at this route's rates)",
    )
    return best
