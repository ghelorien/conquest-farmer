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
landing is remembered per character; a landing farther from the field than
the farmer is not ridden to. A refusal before payment just means walking;
an unverified ride is never paid again.
"""

import time
from pathlib import Path

from conquest.character_context import state_path
from conquest.discord_notify import read_json, write_json

SHORTCUTS = {"apparition": "Ape Mountain", "poltergeist": "Ape Mountain"}
FARE = 100
CONDUCTRESS_TILE = (438, 444)
LANDINGS = Path(state_path(".runtime/conductress-landings.json"))


def _distance(a, b):
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def _in_town(life):
    from conquest.city_travel import city_for

    if life["map_id"] != 1002:
        return False
    left, top, right, bottom = city_for(1002)["town_boundary"]
    x, y = life["position"]
    return left <= x <= right and top <= y <= bottom


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
    if landing and _distance(landing, anchor) >= _distance(life["position"], anchor):
        return False
    actor = life["object_address"]
    try:
        from conquest.banking import ensure_transport
        from conquest.conductress import prepare_destination

        ensure_transport(loop, minimum=FARE * 2)
        loop.record(
            "conductress_shortcut_departing",
            option=option,
            activity=f"Taking the Conductress to {option} toward the hunting field",
        )
        loop.travel(CONDUCTRESS_TILE)
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
