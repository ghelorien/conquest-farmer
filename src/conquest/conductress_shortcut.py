"""Ride the Twin City Conductress toward a far hunting field.

Alex, 2026-09-27: "Fastest way to get to apparitions is to talk to the
conductress and select ape city." Her city options drop the farmer beside
that city's gate on the Twin City map (live: Phoenix Castle lands at
(958, 555) by the east gate). Terrain paths measured the same day: from Twin
City to the Apparition field ~600-700 walking tiles, from the south gate
~350 and from the west gate ~310.

The ride uses the qualified Conductress dialog and fare path (conductress-open,
prepare_destination, conductress-travel) and is verified by memory: the
farmer moved away from her and exactly the fare left the bag. Each option's
landing is remembered per character; once known, the ride is taken only when
walking to her plus walking from the landing beats walking from here, both
measured on the terrain. A refusal before payment just means walking; an
unverified ride is never paid again.
"""

import time
from pathlib import Path

from conquest.character_context import state_path
from conquest.discord_notify import read_json, write_json

# Poltergeist: the Desert City gate (45, 397) is 78 walking tiles from the
# field; the Ape gate is 665 and Twin City ~1,010 (beyond the default planner
# budget), so "instead" there means Desert City.
SHORTCUTS = {"apparition": "Ape Mountain", "poltergeist": "Desert City"}
FARE = 100
CONDUCTRESS_TILE = (438, 444)
# Her dialog opens within 18 tiles; arriving within a few tiles of her tile
# does not stall when someone stands on it (live 2026-09-27 14:50: 30 s of
# stalled steps until the ride was abandoned for a ~1,000-tile walk). Town
# travel accepts at most 2: a radius of 4 was refused before every ride
# (live 15:22, "Intermediate arrival radius must be zero to two tiles").
ARRIVAL_RADIUS = 2
LANDINGS = Path(state_path(".runtime/conductress-landings.json"))


def _distance(a, b):
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def walking_tiles(terrain, start, goal):
    """Checked walking tiles between two Twin City points; None if unplannable."""
    try:
        return len(terrain.travel_path(tuple(start), tuple(goal))) - 1
    except ValueError:
        return None


def worth_riding(loop, position, landing, anchor):
    """Whether walking to her and then from the landing beats walking from here.

    A straight line misjudges Twin City: the river puts the Ape gate 352
    tiles from the Apparition field in a line but 356 to walk, and town ~225
    in a line but 653 to walk (live 2026-09-27: the second ride was skipped).
    Without Twin City terrain the straight line is all there is.
    """
    terrain = getattr(loop, "terrain", None)
    if getattr(terrain, "map_id", None) != 1002:
        return _distance(landing, anchor) < _distance(position, anchor)
    after = walking_tiles(terrain, landing, anchor)
    to_her = walking_tiles(terrain, position, CONDUCTRESS_TILE)
    if after is None or to_her is None:
        return False
    walk = walking_tiles(terrain, position, anchor)
    # Unplannable from here (Poltergeist from town is beyond the planner's
    # budget): the ride is the way.
    return walk is None or to_her + after < walk


def _in_town(life):
    from conquest.city_travel import city_for

    if life["map_id"] != 1002:
        return False
    left, top, right, bottom = city_for(1002)["town_boundary"]
    x, y = life["position"]
    # Her tile lies south of the town boundary: a farmer left standing there
    # by a refused ride must still board (live 2026-09-27, every retry after
    # the refusal walked instead).
    return (left <= x <= right and top <= y <= bottom) or _distance(
        life["position"], CONDUCTRESS_TILE
    ) <= 12


def ride(loop):
    """Take the Conductress toward this route's field; True after a verified ride."""
    option = SHORTCUTS.get(loop.route.id)
    if not option:
        return False
    life = loop.living()["embedded_controls"]["life"]
    if not _in_town(life):
        return False
    anchor = tuple(loop.route.hunting_anchor)
    landing = read_json(LANDINGS).get(option)
    if landing and not worth_riding(loop, life["position"], landing, anchor):
        return False
    actor = life["object_address"]
    try:
        from conquest.banking import ensure_transport
        from conquest.conductress import prepare_destination

        ensure_transport(loop, minimum=FARE * 2)
        # Checked before walking to her: with 39 silver and an empty bank her
        # dialog refused the fare and the route failed (live 2026-09-27).
        silver = loop.town("supplies")["silver"]
        if silver < FARE:
            raise ValueError(f"The {FARE} silver fare is more than the {silver} carried")
        loop.record(
            "conductress_shortcut_departing",
            option=option,
            activity=f"Taking the Conductress to {option} toward the hunting field",
        )
        loop.travel(CONDUCTRESS_TILE, arrival_radius=ARRIVAL_RADIUS)
        loop.town("conductress-open")
        prepare_destination(loop, option)
        before = loop.town("supplies")
    except ValueError as error:
        loop.record(
            "conductress_shortcut_skipped",
            option=option,
            detail=str(error),
            activity="Conductress unavailable; walking to the hunting field",
        )
        return False
    loop.town("conductress-travel", destination=option)
    for _ in range(40):
        health = loop.health()
        data = health["embedded_controls"]
        life = data.get("life")
        if (
            life
            and life["object_address"] == actor
            and not life["dead_candidate"]
            and 0 <= time.time() - data.get("observed_at", 0) <= 1
            and life["map_id"] == 1002
            and _distance(life["position"], CONDUCTRESS_TILE) >= 40
        ):
            after = loop.town("supplies")
            if before["silver"] - after["silver"] == FARE:
                landings = read_json(LANDINGS)
                landings[option] = list(life["position"])
                write_json(LANDINGS, landings)
                loop.record(
                    "conductress_shortcut_arrived",
                    option=option,
                    position=life["position"],
                    activity=f"Conductress dropped us at {tuple(life['position'])}",
                )
                return True
        time.sleep(0.1)
    raise ValueError("Conductress travel was not verified; no repeat payment issued")
