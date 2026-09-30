"""A temporary user-requested route hold, independent of reusable route templates."""

from conquest.character_context import state_path
from pathlib import Path
import time
from conquest.discord_notify import read_json, write_json

PLAN = Path(state_path(".runtime/session-plan.json"))
CIRCUIT = Path(state_path(".runtime/equipment-circuit.json"))
# Shop level thresholds for the seven supported archer slots.
UPGRADE_LEVELS = (
    1,
    7,
    8,
    10,
    12,
    15,
    17,
    20,
    22,
    25,
    27,
    30,
    32,
    35,
    37,
    40,
    42,
    45,
    50,
    52,
    55,
    57,
    60,
    65,
    67,
    70,
    73,
    75,
    77,
    80,
    82,
    85,
    87,
    90,
    95,
    97,
)


def active_plan():
    data = read_json(PLAN)
    if not data.get("active"):
        return None
    if data.get("mode") == "hold_route":
        from conquest.routes import RouteLibrary

        route = RouteLibrary().load(data["route_id"])
        if data.get("upgrade_maps") != [route.restock_map_id]:
            raise ValueError("A saved route hold must use its own restock town")
        return data
    if (
        data.get("mode") == "save_silver"
        and data.get("route_id") == "poltergeist"
        and data.get("silver_target") in (None, 50000)
        and data.get("upgrade_maps") == [1002]
    ):
        return data
    if data.get("route_id") != "bandit" or data.get("upgrade_maps") not in (
        [1002, 1011],
        [1011],
    ):
        raise ValueError("Unsupported overnight route hold")
    return data


def plan_note():
    plan = active_plan()
    if not plan:
        from conquest.level_goal import note

        return note() or "Automatic leveling"
    if plan.get("mode") == "hold_route":
        from conquest.routes import RouteLibrary

        return (
            "Staying on "
            + RouteLibrary().load(plan["route_id"]).name
            + " until Resume leveling"
        )
    if plan.get("mode") == "save_silver":
        if plan.get("silver_target") is None:
            from conquest.banking import policy, STATUS

            if policy().get("enabled"):
                bank = read_json(STATUS)
                if "stored_silver" in bank:
                    return f"Poltergeists: {plan.get('balance', 0):,} carried; {bank['stored_silver']:,} banked"
            return (
                f"Poltergeists: continuous farming; {plan.get('balance', 0):,} silver"
            )
        return f"Poltergeist savings: {plan.get('balance', plan.get('starting_silver', 0)):,} / {plan['silver_target']:,} silver"
    return (
        "Tonight: Bandits; Phoenix shops only"
        if plan["upgrade_maps"] == [1011]
        else "Tonight: Bandits; upgrades in both cities"
    )


def rotate_hold(visit_id=None):
    """Move a route hold to the next field of its rotation; the new route id,
    or None when nothing rotates.

    A GiantApe field gives ~1.5-2.4M xp/h fresh and thins to ~0.7-1.1M within
    15-30 min (respawn caps it); the rested north field gave Toxic 2.39M xp/h
    at 20:14-20:24 after one restock cycle on the south (2026-09-29). A hold
    with "rotation": [route ids] therefore moves to the next field at each
    restock, and the restock's end departs from town to it. Once per town
    visit: a resumed or repeated restock of the same visit keeps its field.
    Every field must share the hold's restock town.
    """
    data = read_json(PLAN)
    rotation = data.get("rotation")
    if (
        not data.get("active")
        or data.get("mode") != "hold_route"
        or not isinstance(rotation, list)
        or len(rotation) < 2
        or data.get("route_id") not in rotation
    ):
        return None
    if visit_id is not None and data.get("rotated_for_visit") == visit_id:
        return None
    from conquest.routes import RouteLibrary

    library = RouteLibrary()
    towns = {library.load(route).restock_map_id for route in rotation}
    if towns != {library.load(data["route_id"]).restock_map_id}:
        return None
    target = rotation[(rotation.index(data["route_id"]) + 1) % len(rotation)]
    data.update(
        previous_route_id=data["route_id"],
        route_id=target,
        rotated_at=time.time(),
        rotated_for_visit=visit_id,
    )
    write_json(PLAN, data)
    return target


def resume_leveling():
    data = read_json(PLAN)
    data.update(active=False, ended_at=time.time())
    write_json(PLAN, data)


def follow_manual_route(route):
    """An explicit UI selection supersedes a hold on a different route."""
    data = read_json(PLAN)
    if not data.get("active") or data.get("route_id") == route.id:
        return
    if data.get("mode") == "hold_route":
        write_json(
            PLAN,
            {
                "active": True,
                "mode": "hold_route",
                "route_id": route.id,
                "upgrade_maps": [route.restock_map_id],
                "started_at": time.time(),
            },
        )
    else:
        # Savings/equipment circuit policies must not transfer to another area.
        resume_leveling()


def upgrade_tier(level):
    return max(tier for tier in UPGRADE_LEVELS if tier <= level)


def circuit_due(plan, level, previous):
    return (
        not previous.get("completed")
        or previous.get("plan_started_at") != plan["started_at"]
        or previous.get("tier") != upgrade_tier(level)
    )


def upgrade_circuit(loop):
    plan = active_plan()
    if not plan or plan.get("mode") == "hold_route":
        # Leveling or a route hold: a requested Magic Artisan visit first,
        # else a restock town without gear shops (Ape City) sends the farmer
        # to Twin City's when an upgrade is due. One trip per restock.
        from conquest import market_artisan
        from conquest.gear_circuit import run

        return market_artisan.run(loop) or run(loop)
    if loop.route.id != plan["route_id"]:
        return False
    if plan.get("mode") == "save_silver":
        return False
    state = loop.town("gear")
    previous = read_json(CIRCUIT)
    if not circuit_due(plan, state["level"], previous):
        return False
    from conquest.world_travel import travel_to_map, connection_path
    from conquest.city_travel import city_for
    from conquest.equipment import EquipmentReview

    home = loop.route.map_id
    maps = plan["upgrade_maps"]
    # The normal local restock already reviews Phoenix's equipment vendors.
    if maps == [home]:
        return False
    # Check both directions and all vendor stops before departing.
    for city in maps:
        connection_path(home, city)
        connection_path(city, home)
        if not city_for(city).get("services"):
            raise ValueError("Equipment circuit has an unmapped shop city")
    checkpoint = {
        "plan_started_at": plan["started_at"],
        "tier": upgrade_tier(state["level"]),
        "completed": False,
        "maps": [],
        "started_at": time.time(),
    }
    if (
        previous.get("plan_started_at") == plan["started_at"]
        and not previous.get("finished_at")
        and previous.get("tier") == checkpoint["tier"]
    ):
        checkpoint = previous
    write_json(CIRCUIT, checkpoint)
    review = EquipmentReview(loop)
    complete = all(row["reviewed"] for row in checkpoint["maps"])
    prior = loop.phase
    loop.phase = "restocking"
    try:
        for map_id in maps:
            if not active_plan():
                break
            if any(row["map_id"] == map_id for row in checkpoint["maps"]):
                continue
            city = city_for(map_id)
            services = city["services"]
            loop.record(
                "equipment_city_departing",
                city=city["name"],
                activity=f"Checking archer upgrades in {city['name']}; returning to Bandits afterwards",
            )
            loop.town("close", window="Shop")
            loop.town("close", window="Inventory")
            travel_to_map(loop, map_id)
            reviewed = True
            for vendor, point in [(5, services["blacksmith"]), *services["equipment"]]:
                try:
                    loop.travel(tuple(point))
                    loop.town("open", vendor_type=vendor)
                    if review.visit(vendor) is not True:
                        reviewed = False
                except ValueError as error:
                    reviewed = False
                    loop.record(
                        "equipment_review_deferred",
                        city=city["name"],
                        vendor=vendor,
                        detail=str(error),
                        activity="Upgrade unavailable; continuing the town circuit",
                    )
                finally:
                    loop.town("close", window="Shop")
                    loop.town("close", window="Inventory")
            complete = complete and reviewed
            checkpoint["maps"].append({"map_id": map_id, "reviewed": reviewed})
            write_json(CIRCUIT, checkpoint)
    finally:
        # Existing travel uses the saved Conductress trip when leaving Twin City.
        loop.record(
            "equipment_returning", activity="Returning to Bandits after town upgrades"
        )
        travel_to_map(loop, home)
        loop.phase = prior
    checkpoint.update(
        completed=complete and len(checkpoint["maps"]) == len(maps),
        finished_at=time.time(),
    )
    write_json(CIRCUIT, checkpoint)
    loop.record(
        "equipment_circuit_complete",
        equipment_circuit=checkpoint,
        activity="Town upgrade review finished; returning to Bandits",
    )
    return True
