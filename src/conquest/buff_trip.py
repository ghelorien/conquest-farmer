"""Twin City buff trips: MrBuffer's double damage ("Attack Up", Stigma).

Alex 2026-09-30 07:1x: "in twin city there is a npc called MrBuffer ... mr
buffer will double your damage for 30 minutes when you walk past him ... Note
that if you die, you lose the double damage buff ... Best way would be to
keep twin city scrolls available and use one to go grab the buff, then
another scroll back to ape city." Then: "mrbuffer roams around the middle
square probably within 20 tiles."

MrBuffer is a player-range actor, "MrBuffer[Bot]" (uid 2146483646, species
0, the common actor vtable), so the NPC readers, which keep uids below
1,000,000, never list him; this module scans the scene by name. A
TwinCityGate lands on the middle square, (429, 378). Twin City's Pharmacist
sells TwinCityGates; Ape City's sells only ApeCityGates, so each trip buys
the next trips' gates before reading an ApeCityGate home.

A hunt ends for a refresh once the buff runs out (overnight.hunt), and a
restock goes through Twin City first while less than REFRESH_WITHIN is left
(overnight.restock). A death clears the buff (overnight.living).
Enabled per character by .runtime/buff-trip.json {"stigma": true}.
"""

import struct
import time
from pathlib import Path

from conquest.character_context import state_path
from conquest.discord_notify import read_json, write_json

POLICY = Path(state_path(".runtime/buff-trip.json"))
STATE = Path(state_path(".runtime/buffs.json"))
TWIN_CITY = 1002
# Where a TwinCityGate lands; MrBuffer roams within ~20 tiles of it.
SQUARE = (429, 378)
BUFFER_NAME = "MrBuffer"
STIGMA_SECONDS = 30 * 60
# A restock refreshes the buff unless more than this is left of it: the walk
# back to the field alone takes ~3 minutes of it.
REFRESH_WITHIN = 25 * 60
# A hunt ends for a refresh once less than this is left.
HUNT_END_WITHIN = 30
# "Walk past him": within this many tiles, then a short wait for the buff.
BUFFER_REACH = 2
BUFF_DWELL_SECONDS = 2.0
SEARCH_SECONDS = 60
# TwinCityGates kept after each trip (one per trip, spares for a failed read).
GATE_KEEP = 3
# After a failed trip, restocks go straight home for this long.
FAILURE_COOLDOWN = 20 * 60


def enabled():
    return bool(read_json(POLICY).get("stigma"))


def stigma_left(now=None):
    """Seconds of MrBuffer's buff left, from the recorded visit (0 if none)."""
    at = read_json(STATE).get("stigma_at")
    if type(at) not in (int, float):
        return 0.0
    return max(0.0, at + STIGMA_SECONDS - (time.time() if now is None else now))


def lost(reason="death"):
    """A death clears the buff (Alex: "if you die, you lose the double damage buff")."""
    if reason == "death":
        from conquest import hempknight

        # The double EXP waits for a quiet spell ("you dont die too much").
        hempknight.note_death()
    data = read_json(STATE)
    if data.get("stigma_at") is None:
        return False
    data.update(stigma_at=None, lost_at=time.time(), lost_reason=reason)
    write_json(STATE, data)
    return True


def cooling_down(now=None):
    failed = read_json(STATE).get("failed_at")
    now = time.time() if now is None else now
    return type(failed) in (int, float) and now - failed < FAILURE_COOLDOWN


def silver_needed():
    """Silver a restock keeps carried for the next trip: GATE_KEEP TwinCityGates
    and a Conductress fare. Twin City's warehouse is not Ape City's, and the
    first trip (2026-09-30 08:38) reached the Pharmacist with 100 silver after
    the fare and bought no gate."""
    if not enabled():
        return 0
    from conquest.return_scroll import GATE_PRICE

    return GATE_KEEP * GATE_PRICE + 200


def gates_carried(items):
    from conquest.return_scroll import TYPE

    return sum(i.get("amount", 1) for i in items if i.get("type_id") == TYPE)


def way_home(items, home):
    """A gate back to the restock town is carried (Twin City needs none).

    Twin City's Pharmacist sells no ApeCityGate, and from Twin City the saved
    way into Ape City is a Conductress fare plus ~550 tiles across the GiantApe
    plain (Toxic died there at 47). On 2026-09-30 09:57 Suicide was about to
    refresh from the ThunderApe field with no ApeCityGate and 200 silver, which
    the TwinCityGate purchase would have spent before the fare.
    """
    from conquest.return_scroll import GATES

    kind = GATES.get(home) if home not in (None, TWIN_CITY) else None
    return kind is None or any(
        i.get("type_id") == kind and i.get("amount", 1) > 0 for i in items
    )


def refresh_due(items, now=None, home=None):
    """A restock should go through Twin City: enabled, a TwinCityGate and a
    gate home carried, no recent failure, and less than REFRESH_WITHIN of the
    buff left."""
    return (
        enabled()
        and gates_carried(items) > 0
        and way_home(items, home)
        and not cooling_down(now)
        and stigma_left(now) < REFRESH_WITHIN
    )


def gate_home_missing(items, now=None, home=None):
    """A refresh is due but for the gate home, which a restock at home buys
    (return_scroll.stock keeps two ApeCityGates).

    2026-09-30 17:09: Alex read Toxic's last ApeCityGate by hand. The restart
    walked out with three TwinCityGates and no buff, and the next restock
    checked for the trip before its Pharmacist sold the gate home.
    """
    return (
        enabled()
        and gates_carried(items) > 0
        and not way_home(items, home)
        and not cooling_down(now)
        and stigma_left(now) < REFRESH_WITHIN
    )


def bootstrap_due(loop, items, now=None):
    """No TwinCityGate carried (Ape City sells none): after a restock, ride the
    Ape City Conductress to Twin City once for the buff and the first gates."""
    from conquest.gear_circuit import saved_ride

    return (
        enabled()
        and gates_carried(items) == 0
        and way_home(items, loop.route.restock_map_id)
        and not cooling_down(now)
        and stigma_left(now) < REFRESH_WITHIN
        and saved_ride(loop.route.restock_map_id, TWIN_CITY) is not None
    )


def on_the_way(route):
    """The hunt's own map travel crosses Twin City from home: the Desert's
    TwinCityGate hop lands on MrBuffer's square, and desert_gate fetches his
    buff and the next TwinCityGates there. A restock then goes straight home,
    and the buff is fresh on arrival instead of ~5 minutes old."""
    from conquest.world_travel import connection_path

    home, field = route.restock_map_id, getattr(route, "map_id", None)
    if field is None or TWIN_CITY in (home, field):
        return False
    try:
        hops = connection_path(home, field)
    except ValueError:
        return False
    return any(edge["destination_map"] == TWIN_CITY for edge in hops)


def hunt_should_end(items, now=None, home=None):
    """A hunt should end for a refresh: the recorded buff is running out.
    Never before a first visit, so turning this on waits for a restock."""
    return (
        enabled()
        and read_json(STATE).get("stigma_at") is not None
        and gates_carried(items) > 0
        and way_home(items, home)
        and not cooling_down(now)
        and stigma_left(now) < HUNT_END_WITHIN
    )


def find_buffer(session):
    """MrBuffer's tile in the scene, or None. Scans every actor with the
    common vtable whatever its uid or species (he has a player-range uid)."""
    from conquest.addressing import checked_address
    from conquest.memory_entities import MemoryEntityReader

    entities = MemoryEntityReader.for_session(session)
    layout = entities.layout
    base, collection, _ = entities._resolve()
    begin, end = (
        struct.unpack("<Q", session.read_block(collection + offset, 8))[0]
        for offset in (layout.begin_offset, layout.end_offset)
    )
    if not begin <= end or (end - begin) % layout.entry_stride:
        return None
    raw = session.read_block(checked_address(begin), end - begin) if end > begin else b""
    addresses = []
    for i in range(0, len(raw), layout.entry_stride):
        pointer = struct.unpack_from("<Q", raw, i + layout.entry_object_offset)[0]
        try:
            addresses.append(checked_address(pointer, 0x100))
        except ValueError:
            pass
    vtable = base + layout.monster_vtable_rva
    for start in range(0, len(addresses), 900):
        part = addresses[start : start + 900]
        for block in session.read_blocks(part, 0x100):
            if block is None or struct.unpack_from("<Q", block)[0] != vtable:
                continue
            name = block[layout.name_offset : layout.name_offset + 32].split(b"\0", 1)[0]
            if name.decode("utf-8", errors="replace").startswith(BUFFER_NAME):
                return tuple(struct.unpack_from("<2I", block, layout.position_offset))
    return None


def visit_buffer(loop, seconds=SEARCH_SECONDS, find=None):
    """Walk past MrBuffer on Twin City's middle square; True once beside him."""
    find = find or (lambda: find_buffer(loop.care.session))
    deadline = time.monotonic() + seconds
    searched_square = False
    while time.monotonic() < deadline:
        life = loop.living()["embedded_controls"]["life"]
        if life["map_id"] != TWIN_CITY:
            raise ValueError("Left Twin City before reaching MrBuffer")
        spot = find()
        if spot is None:
            if not searched_square:
                searched_square = True
                loop.travel(SQUARE, arrival_radius=2, activity="Looking for MrBuffer on the middle square")
            else:
                time.sleep(0.5)
            continue
        if max(abs(a - b) for a, b in zip(life["position"], spot)) <= BUFFER_REACH:
            time.sleep(BUFF_DWELL_SECONDS)
            now = time.time()
            data = read_json(STATE)
            data.update(stigma_at=now, stigma_tile=list(spot), failed_at=None,
                        visits=int(data.get("visits") or 0) + 1)
            write_json(STATE, data)
            loop.record(
                "buff_received",
                buff="stigma",
                position=life["position"],
                buffer=list(spot),
                until=now + STIGMA_SECONDS,
                activity="Walked past MrBuffer: double damage for 30 minutes",
            )
            return True
        # He roams: walk toward his tile and look again.
        loop.travel(spot, arrival_radius=BUFFER_REACH, activity="Walking to MrBuffer")
    raise ValueError("MrBuffer was not found on Twin City's middle square")


def buy_gates(loop):
    from conquest.city_travel import city_for
    from conquest.return_scroll import buy_gate

    loop.travel(tuple(city_for(TWIN_CITY)["services"]["pharmacist"]))
    loop.town("open", vendor_type=3)
    try:
        carried = buy_gate(loop, TWIN_CITY, keep=GATE_KEEP)
    finally:
        loop.town("close", window="Shop")
        loop.town("close", window="Inventory")
    if carried < GATE_KEEP:
        loop.record(
            "buff_trip_gates_short",
            carried=carried,
            silver=loop.town("supplies")["silver"],
            activity=f"Only {carried} TwinCityGates carried; the next trip may ride",
        )
    return carried


def failed(loop, error):
    data = read_json(STATE)
    data.update(failed_at=time.time(), failure=str(error))
    write_json(STATE, data)
    loop.record(
        "buff_trip_failed",
        detail=str(error),
        activity="Twin City buff trip stopped; restocking at home",
    )


def arrived(loop, map_id):
    """Walks plan on ``map_id``'s terrain from here: a gate read leaves the
    old map's loaded, and map travel on the same map only reloads it.

    2026-09-30 10:04, the first trip by TwinCityGate: MrBuffer's tile
    (424, 372) was planned on Ape Mountain's terrain ("Route endpoint is
    blocked or outside the map"), and the trip failed in Twin City. The rides
    before it worked because entering Twin City town loads its terrain.
    """
    from conquest.world_travel import travel_to_map

    travel_to_map(loop, map_id)


def trip(loop, ride=False):
    """TwinCityGate to Twin City, walk past MrBuffer, restock TwinCityGates,
    ApeCityGate home. With ``ride`` (from home town, no gate carried) the
    saved Conductress ride goes instead and the walk enters Twin City town.
    True when the farmer went to Twin City (the restock that called it then
    carries on at home)."""
    from conquest.return_scroll import read_gate

    home = loop.route.restock_map_id
    if home == TWIN_CITY:
        return False
    if not ride and on_the_way(loop.route):
        # The bootstrap ride still goes: with no TwinCityGate carried the hop
        # would walk Ape Mountain's portal across the GiantApe plain.
        loop.record(
            "buff_trip_skipped",
            reason="on_the_way",
            activity="MrBuffer is on the way to the hunt; no separate Twin City trip",
        )
        return False
    if not way_home(loop.town("supplies")["items"], home):
        loop.record(
            "buff_trip_skipped",
            reason="no_gate_home",
            activity="No gate home carried; skipping the Twin City buff trip",
        )
        return False
    life = loop.living()["embedded_controls"]["life"]
    loop.record(
        "buff_trip_departing",
        stigma_left=round(stigma_left()),
        way="ride" if ride else "gate",
        activity="Refreshing MrBuffer's double damage in Twin City",
    )
    prior = loop.phase
    loop.phase = "restocking"
    went = life["map_id"] == TWIN_CITY
    try:
        if not went and ride:
            from conquest.gear_circuit import enter_town, reach_twin_city

            went = True  # a fare may be paid: always try the gate home
            reach_twin_city(loop, home)
            enter_town(loop)
        elif not went:
            quiet = getattr(loop, "quiet_for_gate", None)
            if quiet is not None and not quiet():
                raise ValueError("No quiet spot to read the TwinCityGate")
            if not read_gate(loop, TWIN_CITY):
                raise ValueError("The TwinCityGate did not complete")
            went = True
        arrived(loop, TWIN_CITY)
        visit_buffer(loop)
        from conquest import hempknight

        # TheHempKnight's daily double EXP lies on the way to the Pharmacist;
        # a visit never fails the trip.
        hempknight.visit(loop)
        buy_gates(loop)
    except ValueError as error:
        failed(loop, error)
    except BaseException:
        loop.phase = prior
        raise
    try:
        if went:
            here = loop.living()["embedded_controls"]["life"]
            if here["map_id"] != home:
                read_gate(loop, home)
            # Home's terrain again, or map travel when the gate failed.
            arrived(loop, home)
            loop.record("buff_trip_complete", stigma_left=round(stigma_left()),
                        activity="Back from Twin City; restocking")
    finally:
        loop.phase = prior
    return went
