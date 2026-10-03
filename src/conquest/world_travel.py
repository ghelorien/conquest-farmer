"""Reusable map connections learned from ordinary portal entry and memory arrival."""

from conquest.character_context import installation_path, state_path
from collections import deque
import json
from pathlib import Path
import time
from conquest.navigation import read_terrain
from conquest.worker import request

CLIENT_ROOT = installation_path(r"C:\Program Files\Classic Conquer 2.0")
CONNECTIONS = Path("profiles/map-connections.json")


def connections(path=CONNECTIONS):
    if not Path(path).exists():
        return []
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    return value["connections"]


def connection_path(source, destination, edges=None):
    queue = deque([(source, [])])
    seen = {source}
    for_map = connections() if edges is None else edges
    while queue:
        here, path = queue.popleft()
        if here == destination:
            return path
        for edge in for_map:
            if (
                edge.get("verified") is not True
                or edge["source_map"] != here
                or edge["destination_map"] in seen
            ):
                continue
            seen.add(edge["destination_map"])
            queue.append((edge["destination_map"], path + [edge]))
    raise ValueError(
        f"No memory-verified map connection from {source} to {destination}"
    )


def approach_candidates(terrain, source, portal_id):
    """Every approach with a terrain path, nearest first (Manhattan, then
    Chebyshev: from Ape City's (381, 21) landing that puts (376, 11), which
    Toxic reached, before (379, 8), which it never could)."""
    matches = [p for p in terrain.portals if p[2] == portal_id]
    if len(matches) != 1:
        raise ValueError("Portal ID is absent or ambiguous")
    x, y, _ = matches[0]
    # Try nearest approach first; all paths remain outside portal guard tiles.
    candidates = sorted(
        (
            (x + dx, y + dy)
            for dx, dy in (
                (2, 0),
                (-2, 0),
                (0, 2),
                (0, -2),
                (3, 0),
                (-3, 0),
                (0, 3),
                (0, -3),
            )
        ),
        key=lambda p: (
            abs(p[0] - source[0]) + abs(p[1] - source[1]),
            max(abs(p[0] - source[0]), abs(p[1] - source[1])),
        ),
    )
    found = []
    for point in candidates:
        try:
            terrain.path(tuple(source), point)
        except ValueError:
            continue
        found.append((point, matches[0]))
    return found


def approach_portal(terrain, source, portal_id):
    found = approach_candidates(terrain, source, portal_id)
    if not found:
        raise ValueError("No walkable approach to this portal")
    return found[0]


def cross_portal(loop, portal_id, expected_map=None):
    before = loop.living()["embedded_controls"]["life"]
    source_map = before["map_id"]
    actor = before["object_address"]
    terrain = read_terrain(CLIENT_ROOT, source_map)
    loop.terrain = terrain
    options = approach_candidates(terrain, before["position"], portal_id)
    if not options:
        raise ValueError("No walkable approach to this portal")
    from conquest.travel_progress import TravelStalled

    # A walkable-looking side can still refuse every step: 1020 portal 1's
    # east approach (379, 8) held Toxic at y 10-12 until bounded recovery
    # gave up (2026-09-29 15:35), while (376, 11) south of it was reached.
    for index, (approach, portal) in enumerate(options):
        try:
            loop.travel(approach)
            break
        except TravelStalled as error:
            if error.code != "no_progress" or index + 1 == len(options):
                raise
            loop.record(
                "portal_approach_retry",
                approach=list(approach),
                detail=str(error),
                activity="Portal approach made no progress; trying another side",
            )
    before = loop.living()["embedded_controls"]["life"]
    if before["map_id"] != source_map or before["object_address"] != actor:
        raise ValueError("Character or map changed before portal entry")
    loop.record(
        "entering_portal",
        source_map=source_map,
        portal_id=portal_id,
        activity=f"Entering portal to map {expected_map}"
        if expected_map
        else "Checking map portal destination",
    )
    request(
        loop.info,
        "route-jump",
        {
            "source": before["position"],
            "destination": list(portal[:2]),
            "map_id": source_map,
            "portal_id": portal_id,
            "expires_at": time.time() + 4,
        },
    )
    deadline = time.monotonic() + 12
    stable = 0
    last_stamp = None
    arrival = None
    while time.monotonic() < deadline:
        data = loop.health()["embedded_controls"]
        life = data.get("life")
        if (
            life
            and life["object_address"] == actor
            and not life["dead_candidate"]
            and life["map_id"] != source_map
            and 0 <= time.time() - data.get("observed_at", 0) <= 1
        ):
            if life["timestamp"] != last_stamp:
                stable = (
                    stable + 1 if arrival and arrival["map_id"] == life["map_id"] else 1
                )
                arrival = life
                last_stamp = life["timestamp"]
            if stable >= 3:
                break
        time.sleep(0.1)
    else:
        raise ValueError("Portal arrival was not confirmed by memory")
    if expected_map is not None and arrival["map_id"] != expected_map:
        raise ValueError(
            f"Portal destination changed: expected {expected_map}, observed {arrival['map_id']}"
        )
    destination = read_terrain(CLIENT_ROOT, arrival["map_id"])
    edge = {
        "source_map": source_map,
        "portal_id": portal_id,
        "portal_position": list(portal[:2]),
        "destination_map": arrival["map_id"],
        "arrival_position": arrival["position"],
        "source_terrain_sha256": terrain.source_sha256,
        "destination_terrain_sha256": destination.source_sha256,
        "verified": True,
        "observed_at": time.time(),
    }
    loop.terrain = destination
    loop.record(
        "map_arrived", connection=edge, activity=f"Arrived on map {arrival['map_id']}"
    )
    return edge


def save_connection(edge, path=CONNECTIONS):
    if edge.get("verified") is not True:
        raise ValueError("Only observed map arrivals may be saved")
    rows = [
        r
        for r in connections(path)
        if (r["source_map"], r.get("portal_id"))
        != (edge["source_map"], edge["portal_id"])
    ]
    from conquest.discord_notify import write_json

    write_json(Path(path), {"connections": rows + [edge]})


def gate_towns(loop, source, destination):
    """Towns whose carried gate leads on to ``destination``, fewest saved hops
    first. A gate reads anywhere but its own town and the Market."""
    from conquest.return_scroll import GATES, carried

    towns = []
    for town, kind in GATES.items():
        if town == source:
            continue
        try:
            hops = 0 if town == destination else len(connection_path(town, destination))
        except ValueError:
            continue
        towns.append((hops, town, kind))
    return [town for _, town, kind in sorted(towns) if carried(loop, kind)]


def reachable(loop, source, destination):
    """Map travel can get from ``source`` to ``destination``: a verified
    connection, or a carried gate to a town with one (the way out of the
    Desert, whose portals nobody has crossed)."""
    if source == destination:
        return True
    try:
        connection_path(source, destination)
        return True
    except ValueError:
        return bool(gate_towns(loop, source, destination))


def inside(region, position):
    x0, y0, x1, y1 = region
    return x0 <= position[0] <= x1 and y0 <= position[1] <= y1


def enter_route_area(loop):
    """Reach a hunting area walled off from its own town (route "entry"):
    map travel to the entry map without its town walk, then the entry portal
    into the route's map, checked by memory. False when already inside.

    Love Canyon, Ape Mountain's south-west (Snakemen L62, the client's help:
    (232, 461)), is ~1,285 walking tiles from Ape City through the GiantApe
    and ThunderApe boss grounds; its west corner holds portal 4 (11, 377),
    and the Desert's east portal 1 (977, 668) is six tiles from where
    GeneralPeace's page lands (2026-09-30).
    """
    route = loop.route
    entry = getattr(route, "entry", None)
    if entry is None or entry.map_id is None:
        return False  # no portal entry: the hunt walks in
    life = loop.living()["embedded_controls"]["life"]
    if life["map_id"] == route.map_id and inside(entry.region, life["position"]):
        loop.terrain = read_terrain(CLIENT_ROOT, route.map_id)
        return False
    travel_to_map(loop, entry.map_id, visit_town=False)
    edge = cross_portal(loop, entry.portal_id, route.map_id)
    here = loop.living()["embedded_controls"]["life"]["position"]
    if not inside(entry.region, here):
        raise ValueError(f"The entry portal landed outside {route.name} at {here}")
    loop.record(
        "route_entry_crossed",
        connection=edge,
        activity=f"Through map {entry.map_id}'s portal {entry.portal_id} into {route.name}",
    )
    return True


def travel_to_map(loop, destination, visit_town=True):
    """Travel to ``destination`` by carried gates and saved connections.

    ``visit_town`` False skips each arrival's walk to its city's town, for a
    map that is only a passage (the Desert on the way into Love Canyon)."""
    for _ in range(8):
        life = loop.living()["embedded_controls"]["life"]
        if life["map_id"] == destination:
            loop.terrain = read_terrain(CLIENT_ROOT, destination)
            return
        if life["map_id"] == 1036:
            return_from_market(loop, destination)
            continue
        from conquest.return_scroll import SCROLL_SOURCES, read_gate, return_to_town

        if life["map_id"] in SCROLL_SOURCES and destination == 1002:
            # A carried TwinCityGate is the quickest way back to Twin City.
            # Only Twin City sells them (Phoenix's Pharmacist sells CastleGate,
            # live 17:43), so without one Phoenix walks its verified portal.
            if return_to_town(loop):
                continue
            if life["map_id"] == 1004:
                raise ValueError("A TwinCityGate scroll is needed to leave this building")
        # A carried gate beats any saved walk: into Ape City that walk crosses
        # the GiantApe plain from the Twin City portal (Toxic died there at 47).
        if read_gate(loop, destination):
            continue
        try:
            edge = connection_path(life["map_id"], destination)[0]
        except ValueError:
            # No saved way on from this map: a carried gate to a town with one.
            # 2026-09-30 19:28 the seeded Twin City portal 1 led Suicide into
            # the Mine (1028), and every restart failed there until an
            # ApeCityGate was read by hand.
            if not any(
                read_gate(loop, town)
                for town in gate_towns(loop, life["map_id"], destination)
            ):
                raise
            continue
        # A hop through Twin City takes a carried TwinCityGate too. From Ape
        # City that edge is portal 1 at (376, 8), ~550 tiles across the GiantApe
        # plain (the Ape City Conductress trip is unverified), on the way to the
        # Desert (2026-09-30).
        if edge["destination_map"] == 1002:
            if read_gate(loop, 1002):
                continue
            from conquest.gear_circuit import reach_twin_city, saved_ride

            if saved_ride(life["map_id"], 1002) is not None:
                # No TwinCityGate: Ape City's Conductress lands beside its
                # portal 1 (381, 21), so the hop skips the walk across the
                # GiantApe plain.
                reach_twin_city(loop, life["map_id"])
                continue
        if edge.get("service"):
            from conquest import desert_gate
            from conquest.city_travel import city_for, ensure_city_visit

            if edge["service"] != desert_gate.SERVICE:
                raise ValueError(f"Unsupported map crossing service {edge['service']}")
            if (
                read_terrain(CLIENT_ROOT, life["map_id"]).source_sha256
                != edge["source_terrain_sha256"]
                or read_terrain(CLIENT_ROOT, edge["destination_map"]).source_sha256
                != edge["destination_terrain_sha256"]
            ):
                raise ValueError("Saved map connection differs from installed terrain")
            city_for(edge["destination_map"])  # Check before any fare.
            desert_gate.travel(loop)
            if visit_town:
                ensure_city_visit(loop, new_arrival=True)
            continue
        terrain = read_terrain(CLIENT_ROOT, life["map_id"])
        # Every walk before the crossing (banking for the fare, the way to the
        # Conductress) is on this map. A restarted route still held its
        # route map's terrain: Twin City's east gate (958, 555) was "outside
        # the map" of Phoenix Castle (live 2026-09-27 17:46).
        loop.terrain = terrain
        target = read_terrain(CLIENT_ROOT, edge["destination_map"])
        if (
            terrain.source_sha256 != edge["source_terrain_sha256"]
            or target.source_sha256 != edge["destination_terrain_sha256"]
            or tuple(edge["portal_position"]) + (edge["portal_id"],)
            not in terrain.portals
        ):
            raise ValueError("Saved map connection differs from installed terrain")
        from conquest.city_travel import city_for, ensure_city_visit

        city_for(edge["destination_map"])  # Check before any teleport payment.
        if life["map_id"] == 1002 or edge["destination_map"] == 1002:
            from conquest.conductress import take_saved_trip

            if take_saved_trip(loop, edge["destination_map"]):
                arrival = loop.living()["embedded_controls"]["life"]
                if arrival["map_id"] == edge["destination_map"]:
                    if visit_town:
                        ensure_city_visit(loop, new_arrival=True)
                    continue
            elif life["map_id"] == 1002:
                raise ValueError(
                    "A memory-verified Conductress trip is required for Twin City travel; walking fallback is disabled"
                )
            # No saved Conductress trip leads back into Twin City: walk the
            # memory-verified portal checked above. Live 2026-09-27 17:32
            # (Toxic, Phoenix City, 60 silver, no scroll): the route change
            # to the Poltergeists failed here on every retry.
        cross_portal(loop, edge["portal_id"], edge["destination_map"])
        if visit_town:
            ensure_city_visit(loop, new_arrival=True)
    raise ValueError("Map travel exceeded the connection limit")


def return_from_market(loop, destination):
    """Resume hunting from Market using a qualified service, never a portal guess."""
    from conquest.discord_notify import read_json, write_json
    from conquest.meteor_banking import POLICY, trip
    from conquest.town_trade import stash_candidate
    from conquest.city_travel import ensure_city_visit

    policy = read_json(POLICY)
    origin = getattr(loop.route, "restock_map_id", destination)
    plan = policy.get("origins", {}).get(str(origin), {}).get("return")
    if not plan:
        # Mark.Controller sends a farmer back to its city; the Magic Artisan
        # visit from Ape City has no Meteor-trip origin of its own.
        from conquest.market_artisan import exit_plan

        try:
            plan = exit_plan(origin)
        except ValueError:
            plan = None
    if (
        not plan
        or not plan.get("verified")
        or plan.get("source_map") != 1036
        or plan.get("destination_map") != origin
    ):
        raise ValueError("Market departure needs a verified return itinerary")
    if any(stash_candidate(item) for item in loop.town("supplies")["items"]):
        store_before_leaving_market(loop)
    if any(stash_candidate(item) for item in loop.town("supplies")["items"]):
        raise ValueError(
            "Stay in Market: store protected valuables before returning to the route"
        )
    journal = Path(state_path(".runtime/market-route-departure.json"))
    from conquest.recovery_override import read_recovered

    old = read_recovered(journal)
    if old.get("phase") == "submitted":
        raise ValueError(
            "Market departure is uncertain; reconcile arrival before retrying"
        )
    before = loop.living()
    state = {
        "phase": "prepared",
        "identity": before["target"],
        "origin": 1036,
        "destination": origin,
        "started_at": time.time(),
    }
    write_json(journal, state)
    trip(
        loop,
        plan,
        before_submit=lambda: write_json(journal, {**state, "phase": "submitted"}),
    )
    write_json(journal, {**state, "phase": "complete", "completed_at": time.time()})
    loop.record(
        "market_route_returned",
        activity="Returned from Market; resuming the saved farming route",
    )
    ensure_city_visit(loop, new_arrival=True)


def store_before_leaving_market(loop):
    """Bank carried valuables in the Market warehouse before a route return.

    Suicide, 2026-10-03 00:58: brought into the Market by hand with a +1 ring
    and a unique MaskBag, its route restart refused to leave ("Stay in Market")
    and urgent banking only knew the Twin City warehouse, which needs that same
    departure, so it retried in the Market with no kills. Only outside a Meteor
    trip: an unfinished trip stores in Market through market_bank, whose own
    journal keeps the receipts.
    """
    from conquest.banking import close_warehouse, deposit_item, open_warehouse
    from conquest.discord_notify import read_json
    from conquest.meteor_banking import JOURNAL, approach_market_warehouse, carried

    if read_json(JOURNAL).get("phase") not in (None, "completed"):
        return
    approach_market_warehouse(
        loop, "Storing carried valuables in Market before returning to the route"
    )
    open_warehouse(loop)
    try:
        for item in carried(loop):
            receipt = deposit_item(loop, item)
            if receipt.get("verified_in_warehouse") is not True:
                raise ValueError("Market deposit receipt missing; no return issued")
            loop.record(
                "valuable_stored",
                **receipt,
                plus=item.get("plus"),
                activity="Valuable safely stored in Market before returning to the route",
            )
    finally:
        close_warehouse(loop)


def recheck_market_departure(loop):
    """Read current farmer state for an interrupted Market departure."""
    return {
        "observed_at": time.time(),
        "life": loop.living()["embedded_controls"]["life"],
        "supplies": loop.town("supplies"),
    }


def operator_override_market_departure(
    loop, *, operator_confirmed=False, confirmation_reference=None, operator=None
):
    from conquest.recovery_override import operator_override

    journal = Path(state_path(".runtime/market-route-departure.json"))
    try:
        fresh = recheck_market_departure(loop)
    except (ValueError, OSError, KeyError, TypeError) as error:
        fresh = {
            "recheck_unavailable": type(error).__name__,
            "reason": "Fresh farmer memory unavailable; resume requires a fresh replan",
        }
    return operator_override(
        journal,
        pending_phases=("prepared", "submitted"),
        operator_confirmed=operator_confirmed,
        confirmation_reference=confirmation_reference,
        operator=operator,
        fresh_evidence=fresh,
        incident="market-route-departure",
    )
