"""Twin City gear shopping for a farmer whose restock town sells no archer gear.

Ape City's shops stock arrows only (its Blacksmith catalog; the installed
ini/shop.json lists bows only in Twin City's Shop5 and rings, boots and
necklaces only in its Shop1). On the Macaque hold every equipment review said
"This shop does not stock this slot": Toxic reached 49 on a HornBow (45) with
75k silver banked, Suicide 52 on level 37-45 gear, and GiantApes (2,700 HP)
lost ~90 per Scatter hit from Suicide's HornBow (2026-09-29).

Alex (2026-09-29): "if you can afford it, you can change all the items that
are below unique grade that you are currently wearing. If not you cannot
change them, I will manually upgrade them tonight". upgrade_reason already
keeps Unique+ (type % 10 >= 7), plussed and socketed gear. A trip happens only
when carried + banked silver keeps KEEP_SILVER after the planned purchases,
and the Twin City reviews buy only above that floor.

The trip starts from the restock town once its restock is done:
1. one gate home from its Pharmacist: the way back, and the way out of any
   failure on the road;
2. Twin City by a carried TwinCityGate, else by the restock town's saved
   Conductress ride and the portal beside her landing (never the ~550-tile
   walk across the GiantApe plain, where Toxic died at 47);
3. the Blacksmith, Shopkeeper and Armorer reviews, and two TwinCityGates so
   the next trip is a scroll read;
4. the gate home, where the restock banks what is left.
"""

import json
from pathlib import Path
import time

from conquest.character_context import state_path
from conquest.discord_notify import read_json, write_json

SHOP_CITY = 1002
SHOP_VENDORS = (5, 1, 4)
# Carried + banked silver kept after the planned upgrades: about three hours
# of IronArrows net of silver pickup on Macaques (52 arrows/min, 2026-09-29).
KEEP_SILVER = 20000
# Trips allowed per character level: a failed or partial trip is not repeated
# at every restock.
TRIPS_PER_LEVEL = 2
# A Conductress landing this close to its exit portal walks to it; Phoenix's
# ride landed 6 tiles from portal 0 (11, 376 beside 5, 376).
EXIT_WALK_TILES = 30
STATE = Path(state_path(".runtime/gear-circuit.json"))


def shop_products(city):
    from conquest.archer_shop_catalog import catalog

    vendors = catalog().get("cities", {}).get(str(city), {})
    return [
        p
        for vendor in SHOP_VENDORS
        for p in (vendors.get(str(vendor)) or {}).get("products", [])
    ]


def planned(state, silver, home):
    """Twin City upgrades affordable above KEEP_SILVER that `home` cannot sell."""
    from conquest.equipment import choose_upgrades

    local = {p["type_id"] for p in shop_products(home)}
    return [
        p
        for p in choose_upgrades(shop_products(SHOP_CITY), state, silver, KEEP_SILVER)
        if p["type_id"] not in local
    ]


def worth_trip(plan):
    """A trip costs ~5 minutes of hunting (~130k XP on Macaques) and ~500
    silver: go for damage (bow or ring) or at least two defense upgrades, not
    a lone CrystalNecklace at 49 (QinBow and IvoryRing unlock at 50)."""
    from conquest.equipment import category

    slots = [category(p["type_id"]) for p in plan]
    return any(slot in ("bow", "ring") for slot in slots) or len(slots) >= 2


def trips_at(level):
    data = read_json(STATE)
    return data.get("trips", 0) if data.get("level") == level else 0


def saved_ride(source, destination):
    """The saved Conductress ride from `source` toward `destination` that lands
    beside an exit portal (the Phoenix and Ape City rides stay on their map)."""
    from conquest.conductress import TRIPS

    trips = json.loads(TRIPS.read_text(encoding="utf-8"))["trips"] if TRIPS.exists() else []
    rides = [
        t
        for t in trips
        if t["source_map"] == source
        and t["destination_map"] == destination
        and t.get("service")
        and type(t.get("exit_portal")) is int
    ]
    return rides[0] if len(rides) == 1 else None


def reach_twin_city(loop, home):
    from conquest.return_scroll import read_gate

    if read_gate(loop, SHOP_CITY):
        return "gate"
    ride = saved_ride(home, SHOP_CITY)
    if ride is None:
        raise ValueError("No TwinCityGate and no saved ride toward Twin City")
    from conquest.conductress import take_service_trip
    from conquest.world_travel import cross_portal

    try:
        take_service_trip(loop, ride)
    except ValueError as error:
        # A dialog that differs from the saved records sends no choice and no
        # fare. Record what she said so the ride can be saved exactly.
        if "dialog changed" in str(error):
            try:
                seen = loop.town("service-dialog")["records"]
            except (ValueError, KeyError):
                seen = None
            loop.record(
                "gear_circuit_ride_dialog",
                records=seen,
                saved=ride["service"].get("records"),
                activity="Conductress dialog differs from the saved ride; no fare paid",
            )
            try:
                loop.town("service-close-panel", window="Dialog")
            except ValueError:
                pass
        raise
    life = loop.living()["embedded_controls"]["life"]
    if life["map_id"] != SHOP_CITY:
        from conquest.navigation import read_terrain
        from conquest.world_travel import CLIENT_ROOT

        portals = [
            p
            for p in read_terrain(CLIENT_ROOT, life["map_id"]).portals
            if p[2] == ride["exit_portal"]
        ]
        # Only a short walk from her landing: a far landing would walk the
        # plain; the gate home is the way back from anywhere.
        if len(portals) != 1 or max(
            abs(a - b) for a, b in zip(life["position"], portals[0][:2])
        ) > EXIT_WALK_TILES:
            raise ValueError(
                f"Conductress landing {life['position']} is not beside portal {ride['exit_portal']}"
            )
        edge = cross_portal(loop, ride["exit_portal"], SHOP_CITY)
        loop.record(
            "gear_circuit_portal",
            connection=edge,
            activity="Crossed into Twin City beside the Conductress landing",
        )
    return "ride"


def enter_town(loop):
    """Walk into Twin City town; a long road from the south gate may need a
    second 90-second leg."""
    from conquest.city_travel import ensure_city_visit

    for attempt in range(3):
        try:
            ensure_city_visit(loop, new_arrival=True)
            return
        except ValueError as error:
            if attempt == 2:
                raise
            loop.record(
                "gear_circuit_walk_retry",
                detail=str(error),
                activity="Continuing the walk into Twin City",
            )


def shop(loop, reserve):
    from conquest.city_travel import city_for
    from conquest.equipment import EquipmentReview
    from conquest.return_scroll import buy_gate

    services = city_for(SHOP_CITY)["services"]
    review = EquipmentReview(loop, minimum_reserve=reserve)
    for vendor, point in [(5, services["blacksmith"]), *services["equipment"]]:
        try:
            loop.travel(tuple(point))
            loop.town("open", vendor_type=vendor)
            review.visit(vendor)
        except ValueError as error:
            loop.record(
                "equipment_review_deferred",
                city="TwinCity",
                vendor=vendor,
                detail=str(error),
                activity="Upgrade unavailable; continuing the Twin City shops",
            )
        finally:
            loop.town("close", window="Shop")
            loop.town("close", window="Inventory")
    loop.travel(tuple(services["pharmacist"]))
    loop.town("open", vendor_type=3)
    try:
        buy_gate(loop, SHOP_CITY, keep=2)
    finally:
        loop.town("close", window="Shop")
        loop.town("close", window="Inventory")


def go_home(loop, home):
    from conquest.return_scroll import in_town, read_gate
    from types import SimpleNamespace

    life = loop.living()["embedded_controls"]["life"]
    if in_town(SimpleNamespace(**life), home):
        return
    if not read_gate(loop, home):
        from conquest.world_travel import travel_to_map

        travel_to_map(loop, home)
    from conquest.city_travel import city_for

    loop.travel(tuple(city_for(home)["services"]["pharmacist"]))


def waiting(loop, level, names, reason):
    """Record once per level why a due trip cannot leave yet."""
    data = read_json(STATE)
    if data.get("waiting_level") == level and data.get("waiting") == reason:
        return
    data.update(waiting_level=level, waiting=reason)
    write_json(STATE, data)
    loop.record(
        "gear_circuit_waiting",
        planned=names,
        detail=reason,
        activity=f"Twin City upgrades due ({', '.join(names)}); {reason}",
    )


def run(loop):
    """Shop Twin City's upgrades for a farmer restocking elsewhere; True once
    the farmer left town for it (the restock then rechecks its supplies)."""
    from conquest.return_scroll import GATES, GATE_NAMES, POLICY, TYPE, buy_gate, carried

    home = loop.route.restock_map_id
    if home == SHOP_CITY or home not in GATES:
        return False
    state = loop.town("gear")
    from conquest.banking import STATUS as BANK

    banked = read_json(BANK).get("stored_silver", 0)
    banked = banked if type(banked) is int else 0
    silver = loop.town("supplies")["silver"] + banked
    plan = planned(state, silver, home)
    level = state["level"]
    if not worth_trip(plan) or trips_at(level) >= TRIPS_PER_LEVEL:
        return False
    names = [p["name"] for p in plan]
    policy = read_json(POLICY)
    if not (policy.get("enabled") and policy.get("qualified")):
        waiting(loop, level, names, "city gate scrolls are not qualified here")
        return False
    if not carried(loop, TYPE) and saved_ride(home, SHOP_CITY) is None:
        waiting(loop, level, names, "no TwinCityGate and no saved Conductress ride")
        return False
    cost = sum(p["price"] for p in plan)
    write_json(
        STATE,
        {
            "level": level,
            "trips": trips_at(level) + 1,
            "planned": names,
            "cost": cost,
            "silver": silver,
            "started_at": time.time(),
        },
    )
    loop.record(
        "gear_circuit_departing",
        planned=names,
        cost=cost,
        silver=silver,
        activity=f"Level {level}: buying {', '.join(names)} in Twin City ({cost:,} silver)",
    )
    from conquest.city_travel import city_for

    prior = loop.phase
    loop.phase = "restocking"
    left, how = False, None
    try:
        loop.travel(tuple(city_for(home)["services"]["pharmacist"]))
        loop.town("open", vendor_type=3)
        try:
            gates = buy_gate(loop, home, keep=1)
        finally:
            loop.town("close", window="Shop")
            loop.town("close", window="Inventory")
        if gates < 1:
            raise ValueError(f"No {GATE_NAMES[GATES[home]]} for the way back")
        left = True
        how = reach_twin_city(loop, home)
        enter_town(loop)
        # Banked silver stays banked: only carried silver above the floor buys.
        shop(loop, max(0, KEEP_SILVER - banked))
    except ValueError as error:
        # A failed step still goes home by the gate; a stop request does not
        # walk anywhere (it propagates untouched).
        loop.record(
            "gear_circuit_failed",
            detail=str(error),
            activity="Twin City gear trip stopped; returning to the route",
        )
    except BaseException:
        loop.phase = prior
        raise
    try:
        if left:
            loop.record(
                "gear_circuit_returning",
                activity=f"Returning to {city_for(home)['name']} after the Twin City shops",
            )
            go_home(loop, home)
    finally:
        loop.phase = prior
    after = loop.town("gear")
    worn = {
        slot: item.get("name")
        for slot, item in after.get("equipment", {}).items()
    }
    data = read_json(STATE)
    data.update(finished_at=time.time(), way=how, worn=worn)
    write_json(STATE, data)
    loop.record(
        "gear_circuit_complete",
        way=how,
        worn=worn,
        activity=f"Twin City gear trip finished; wearing {', '.join(v for v in worn.values() if v)}",
    )
    return left
