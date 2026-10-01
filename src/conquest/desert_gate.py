"""GeneralPeace: Twin City's way into the Desert (map 1000).

Alex 2026-09-30 19:3x: "When you get to the north west close to the mine /
poltergeist there is a npc that will bring you to the desert." Twin City
portal 1 (44, 394), the seeded guess, leads to the Mine (1028) instead
(Suicide, 19:28:43). Suicide's npc survey saw him on 2026-09-27 13:48:
GeneralPeace (npc.json type 29, model 296, uid 100159) at (60, 463) on Twin
City's map, ten tiles from where the Conductress's "Desert City" ride lands
(69, 473). market_services.discover finds him like any species-0 NPC.

Nobody had read his dialog when this was written. Every page seen is saved in
STATE["pages"]. An option is pressed only on a page saved in STATE["approved"]
([{"records": [...], "option": "..."}]), or on a page offer() accepts: it asks
for no input, names no fare above MAX_FARE, speaks of the Desert (or follows a
pressed page that did), and has exactly one option naming the Desert or,
failing that, exactly one affirmative one. Any other page is saved and the
dialog closed. The crossing counts once memory shows the farmer on the
Desert's map; the pages pressed then become the approved ones, with the fare
paid and the landing tile, and STATE "crossings" keeps the last few.

The way out is a carried gate (return_scroll.GATES): nobody has seen where the
Desert's own portals lead, so no crossing starts without the route's gate home.
"""

import re
import time
from pathlib import Path

from conquest.character_context import state_path
from conquest.discord_notify import read_json, write_json

NAME = "GeneralPeace"
TWIN_CITY = 1002
DESERT = 1000
SPOT = (60, 463)
# Open ground five tiles south-east of him, on the way from the landing.
APPROACH = (64, 468)
# Where the Conductress's "Desert City" ride lands.
LANDING = (69, 473)
STATE = Path(state_path(".runtime/general-peace.json"))
# Closer than this, the farmer walks to him instead of riding again.
NEAR_TILES = 40
# The Conductress charges 100 for any ride; no page naming more is pressed.
MAX_FARE = 1000
# His fare, kept carried until a crossing shows the real one.
GUESSED_FARE = 300
# TwinCityGates for the hops: buy (up to buff_trip.GATE_KEEP) at this many.
HOP_GATES_LOW = 1
# MrBuffer is fetched on the way from within this many tiles of his square
# (the TwinCityGate landing, or the Pharmacist 45 tiles off).
SQUARE_TILES = 80
MAX_STEPS = 4
DIALOG_WAIT_SECONDS = 5
# After a press: the map change (or the next page) shows within this long.
ARRIVAL_SECONDS = 8
KEEP_CROSSINGS = 10
AFFIRMATIVE = re.compile(
    r"^(yes|yeah|sure|ok|okay|please|alright|of course|teleport|take me|"
    r"send me|bring me|i want|i would|i'd like|let's go|go)\b",
    re.IGNORECASE,
)
NEGATIVE = re.compile(
    r"^(no|not|nothing|never|nope|cancel|leave|bye|goodbye|just passing|"
    r"maybe later|later|i'll stay|i will stay)\b",
    re.IGNORECASE,
)
# Currencies other than silver: no page asking for them is pressed.
OTHER_CURRENCY = re.compile(r"\b(cps?|conquer ?points?|gold|emoney)\b", re.IGNORECASE)


def approved_steps(state):
    steps = state.get("approved")
    if not isinstance(steps, list):
        return []
    return [
        s
        for s in steps
        if isinstance(s, dict)
        and isinstance(s.get("records"), list)
        and isinstance(s.get("option"), str)
    ]


def offer(records, desert_before=False):
    """The one option to press on a page nobody approved, or None.

    ``desert_before``: a page pressed earlier in this dialog spoke of the
    Desert (a confirmation page need not name it again)."""
    if any(r.get("kind") == 2 for r in records):
        return None  # an input box
    texts = " ".join(r.get("text", "") for r in records if r.get("kind") == 0)
    options = [r.get("text", "") for r in records if r.get("kind") == 1]
    amounts = [int(n.replace(",", "")) for n in re.findall(r"\d[\d,]*", texts)]
    if any(a > MAX_FARE for a in amounts) or OTHER_CURRENCY.search(texts):
        return None
    candidates = [o for o in options if not NEGATIVE.match(o.strip())]
    desert = [o for o in candidates if "desert" in o.lower()]
    if len(desert) == 1:
        return desert[0]
    if desert or not (desert_before or "desert" in texts.lower()):
        return None
    yes = [o for o in candidates if AFFIRMATIVE.match(o.strip())]
    return yes[0] if len(yes) == 1 else None


def _save_page(records, pressed):
    data = read_json(STATE)
    pages = data.get("pages") if isinstance(data.get("pages"), list) else []
    if all(page.get("records") != records for page in pages):
        pages.append(
            {
                "records": records,
                "after": [step["option"] for step in pressed],
                "seen_at": time.time(),
            }
        )
        data["pages"] = pages
        write_json(STATE, data)


def _dialog(loop, seconds=DIALOG_WAIT_SECONDS):
    """The open dialog, or None when none shows within ``seconds``.

    Asks the worker directly: loop.town retries an absent dialog for ~20 s."""
    from conquest.worker import request

    deadline = time.monotonic() + seconds
    while True:
        loop.living()
        try:
            return request(loop.info, "town", {"action": "service-dialog"})
        except ValueError as error:
            if not any(
                t in str(error)
                for t in ("absent", "not active", "changed during observation")
            ):
                raise
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.2)


def _dismiss(loop):
    try:
        loop.town("service-close-panel", window="Dialog")
    except ValueError:
        pass


def _after_press(loop, actor, records):
    """("arrived", life) once memory shows the Desert's map, ("page", dialog)
    when another page opens, or ("closed", None)."""
    from conquest.worker import request

    deadline = time.monotonic() + ARRIVAL_SECONDS
    while time.monotonic() < deadline:
        loop.check_stop()
        data = loop.health()["embedded_controls"]
        life = data.get("life")
        if (
            life
            and not life["dead_candidate"]
            and life["object_address"] == actor
            and life["map_id"] == DESERT
            and 0 <= time.time() - data.get("observed_at", 0) <= 1
        ):
            return "arrived", life
        if life and life.get("map_id") == TWIN_CITY:
            try:
                dialog = request(loop.info, "town", {"action": "service-dialog"})
            except ValueError as error:
                if not any(
                    t in str(error)
                    for t in ("absent", "not active", "changed during observation")
                ):
                    raise
            else:
                if dialog["records"] != records:
                    return "page", dialog
        time.sleep(0.2)
    return "closed", None


def gate_home_carried(loop, home):
    """The route's gate home is carried (the Desert has no saved way out)."""
    from conquest.return_scroll import GATES, carried
    from conquest.world_travel import connection_path

    try:
        connection_path(DESERT, home)
        return True
    except ValueError:
        pass
    kind = GATES.get(home)
    return kind is not None and carried(loop, kind)


def _identity_ok(identity):
    from conquest.market_services import MODELS

    return (
        identity.get("name") == NAME
        and identity.get("map_id") == TWIN_CITY
        and identity.get("model") in MODELS[NAME]
        and max(abs(a - b) for a, b in zip(identity.get("position", ()), SPOT)) <= 2
    )


def _crossed(loop, before, actor, pressed, life):
    """Save the approved pages, the fare and the landing; return the landing."""
    from conquest.navigation import read_terrain
    from conquest.world_travel import CLIENT_ROOT

    paid = None
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        paid = before["silver"] - loop.town("supplies")["silver"]
        if paid:
            break  # the debit can follow the map change (meteor_banking.trip)
        time.sleep(0.2)
    data = read_json(STATE)
    steps = [{"records": s["records"], "option": s["option"]} for s in pressed]
    known = approved_steps(data)
    data["approved"] = known + [s for s in steps if s not in known]
    crossings = data.get("crossings") if isinstance(data.get("crossings"), list) else []
    crossings.append({"at": time.time(), "fare": paid, "landing": life["position"]})
    data["crossings"] = crossings[-KEEP_CROSSINGS:]
    write_json(STATE, data)
    loop.terrain = read_terrain(CLIENT_ROOT, DESERT)
    loop.record(
        "general_peace_crossed",
        fare=paid,
        landing=life["position"],
        options=[s["option"] for s in pressed],
        activity=f"GeneralPeace: in the Desert at {tuple(life['position'])} (fare {paid})",
    )
    if paid is not None and paid > MAX_FARE:
        loop.record(
            "general_peace_fare_high",
            fare=paid,
            activity=f"GeneralPeace charged {paid} silver, above the {MAX_FARE} expected",
        )
    return life["position"]


def cross(loop):
    """Beside GeneralPeace on Twin City's map: talk him into the Desert.

    Returns the landing tile once memory shows the Desert's map. Raises
    ValueError when a page needs approval (it is saved) or the crossing is not
    verified; nothing is pressed twice within one call."""
    from conquest.dialog_geometry import scroll_direction

    life = loop.living()["embedded_controls"]["life"]
    if life["map_id"] != TWIN_CITY:
        raise ValueError("GeneralPeace stands on Twin City's map")
    home = getattr(getattr(loop, "route", None), "restock_map_id", None)
    if home is not None and not gate_home_carried(loop, home):
        raise ValueError("No gate home carried; not crossing into the Desert")
    actor = life["object_address"]
    approved = approved_steps(read_json(STATE))
    loop.travel(
        APPROACH, arrival_radius=2, activity="Walking up to GeneralPeace", service_name=NAME
    )
    located = loop.town("service-locate", name=NAME)
    if not _identity_ok(located["identity"]):
        raise ValueError("GeneralPeace's identity differs from the survey")
    loop.town("close", window="Shop")
    loop.town("close", window="Inventory")
    before = loop.town("supplies")
    loop.town("service-open", name=NAME)
    pressed, arrival = [], None
    try:
        dialog = _dialog(loop)
        if dialog is None:
            # The first click can miss the sprite (ArcherGod, 2026-09-27).
            loop.town("service-open", name=NAME)
            dialog = _dialog(loop)
        if dialog is None:
            raise ValueError("GeneralPeace's dialog did not open")
        for _ in range(MAX_STEPS):
            loop.check_stop()
            records = dialog["records"]
            _save_page(records, pressed)
            step = next((s for s in approved if s["records"] == records), None)
            desert_before = any(
                "desert" in r.get("text", "").lower()
                for s in pressed
                for r in s["records"]
            )
            option = step["option"] if step else offer(records, desert_before)
            if option is None:
                loop.record(
                    "general_peace_page",
                    records=records,
                    pressed=[s["option"] for s in pressed],
                    activity="GeneralPeace: saved a dialog page nobody approved; nothing pressed",
                )
                raise ValueError(
                    "GeneralPeace: a dialog page needs approval (saved in general-peace.json)"
                )
            if option not in [r.get("text") for r in records if r.get("kind") == 1]:
                raise ValueError("GeneralPeace's option is not on its page")
            if scroll_direction(dialog, option, dialog.get("viewport")):
                loop.town("service-scroll-dialog", name=NAME, option=option, records=records)
                dialog = _dialog(loop) or dialog
                continue
            loop.town("service-select", name=NAME, option=option, records=records)
            pressed.append({"records": records, "option": option})
            outcome, value = _after_press(loop, actor, records)
            if outcome == "arrived":
                arrival = value
                break
            if outcome == "closed":
                raise ValueError("GeneralPeace's dialog closed without a crossing into the Desert")
            dialog = value
        else:
            raise ValueError("GeneralPeace's dialog went past its step limit")
    finally:
        if arrival is None:
            _dismiss(loop)
    return _crossed(loop, before, actor, pressed, arrival)


def fare_reserve():
    """Silver kept for the Conductress and his fare (the last one seen, or a
    guess until then)."""
    fares = [
        c["fare"]
        for c in read_json(STATE).get("crossings", [])
        if isinstance(c, dict) and type(c.get("fare")) is int
    ]
    return 100 + (fares[-1] if fares else GUESSED_FARE)


def stock_hop_gates(loop):
    """Twin City sells the TwinCityGates the next hops read (Ape City sells
    none): top them up while passing, keeping the fares carried. Without one
    the hop rides Ape City's Conductress to Twin City's south gate instead.
    Returns how many are carried."""
    from conquest import buff_trip
    from conquest.city_travel import city_for
    from conquest.return_scroll import GATE_PRICE, buy_gate

    bag = loop.town("supplies")
    have = buff_trip.gates_carried(bag["items"])
    if have > HOP_GATES_LOW:
        return have
    spare = max(0, (bag["silver"] - fare_reserve()) // GATE_PRICE)
    keep = min(buff_trip.GATE_KEEP, have + spare)
    if keep <= have:
        loop.record(
            "hop_gates_short",
            carried=have,
            silver=bag["silver"],
            activity="No silver to spare for TwinCityGates; the next hop may ride",
        )
        return have
    from types import SimpleNamespace

    from conquest.return_scroll import in_town

    if not in_town(SimpleNamespace(**loop.living()["embedded_controls"]["life"]), TWIN_CITY):
        # From the south gate (the ride's way in) the town is ~600 tiles off,
        # past one 90-second town leg.
        from conquest.gear_circuit import enter_town

        enter_town(loop)
    loop.travel(
        tuple(city_for(TWIN_CITY)["services"]["pharmacist"]),
        activity="Buying TwinCityGates for the next hops",
    )
    loop.town("open", vendor_type=3)
    try:
        return buy_gate(loop, TWIN_CITY, keep=keep)
    finally:
        loop.town("close", window="Shop")
        loop.town("close", window="Inventory")


def refresh_buff(loop):
    """MrBuffer on the way: the TwinCityGate hop lands on his square, so the
    ride to the Desert fetches his double damage when it is due (Alex
    2026-09-30: "make sure to grab damage buff"). Never fails the travel."""
    from conquest import buff_trip
    from conquest.navigation import read_terrain
    from conquest.world_travel import CLIENT_ROOT

    if (
        not buff_trip.enabled()
        or buff_trip.cooling_down()
        or buff_trip.stigma_left() >= buff_trip.REFRESH_WITHIN
    ):
        return False
    life = loop.living()["embedded_controls"]["life"]
    if life["map_id"] != TWIN_CITY or max(
        abs(a - b) for a, b in zip(life["position"], buff_trip.SQUARE)
    ) > SQUARE_TILES:
        return False
    loop.terrain = read_terrain(CLIENT_ROOT, TWIN_CITY)
    try:
        return buff_trip.visit_buffer(loop)
    except ValueError as error:
        loop.record(
            "buff_on_the_way_failed",
            detail=str(error),
            activity="MrBuffer not reached on the way; riding on to the Desert",
        )
        return False


def travel(loop):
    """From anywhere on Twin City's map into the Desert: the next hops'
    TwinCityGates and MrBuffer when due, the Conductress's "Desert City" ride
    unless already near him, then him. Returns the landing tile."""
    from conquest.conductress import take_saved_trip
    from conquest.navigation import read_terrain
    from conquest.world_travel import CLIENT_ROOT

    life = loop.living()["embedded_controls"]["life"]
    if life["map_id"] != TWIN_CITY:
        raise ValueError("The way into the Desert starts on Twin City's map")
    home = getattr(getattr(loop, "route", None), "restock_map_id", None)
    # Checked before any fare: the ride and his crossing are one-way.
    if home is not None and not gate_home_carried(loop, home):
        raise ValueError("No gate home carried; not crossing into the Desert")
    loop.terrain = read_terrain(CLIENT_ROOT, TWIN_CITY)
    here = life["position"]
    if max(abs(a - b) for a, b in zip(here, SPOT)) > NEAR_TILES:
        # Not yet on his side of the map: the gates and the buff first (the
        # Pharmacist is ~45 tiles from MrBuffer's square).
        try:
            stock_hop_gates(loop)
        except ValueError as error:
            # They serve the next hops; this one goes on, and a hop without a
            # gate rides Ape City's Conductress.
            loop.record(
                "hop_gates_failed",
                detail=str(error),
                activity="TwinCityGates not bought; riding on to the Desert",
            )
        refresh_buff(loop)
        loop.terrain = read_terrain(CLIENT_ROOT, TWIN_CITY)
        here = loop.living()["embedded_controls"]["life"]["position"]
    if max(abs(a - b) for a, b in zip(here, SPOT)) > NEAR_TILES:
        if not take_saved_trip(loop, DESERT):
            raise ValueError("The Conductress's Desert City ride is not saved")
        loop.terrain = read_terrain(CLIENT_ROOT, TWIN_CITY)
    return cross(loop)
