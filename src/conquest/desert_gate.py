"""Twin City's way into the Desert (map 1000): an NPC in Twin City's west.

Alex 2026-09-30 19:3x: "When you get to the north west close to the mine /
poltergeist there is a npc that will bring you to the desert." Twin City
portal 1 (44, 394), the seeded guess, leads to the Mine (1028) instead
(Suicide, 19:28:43). Asked live and not the way (Suicide, 5bc8885):
GeneralPeace (model 296) at (60, 463), beside the Conductress's "Desert City"
landing (69, 473), only warns ("This is the way to the Desert City. Although
you are excellent, it is dangerous to go ahead.", option "I see."), and the
SpaceMark (model 270) at (96, 323) sells teleport scrolls to Water Taoists
only. CANDIDATES lists the NPCs still to try, in order; market_services
discovers each like any species-0 NPC.

Nobody had read a candidate's dialog when it was listed, and a click may
teleport with no dialog at all. Every page seen is saved in STATE["pages"].
An option is pressed only on a page approved for that NPC (STATE "approved":
[{"npc", "records", "option"}]), or on a page offer() accepts: it asks for no
input, names no fare above MAX_FARE, speaks of the Desert (or follows a
pressed page that did), and has exactly one option naming the Desert or,
failing that, exactly one affirmative one. Any other page is saved, the
dialog closed and the NPC marked refused, so the next try asks the next one.
The crossing counts once memory shows the farmer on the Desert's map; the
pages pressed then become the approved ones, with the fare paid and the
landing tile, and STATE "crossings" keeps the last few.

The way out is a carried gate (return_scroll.GATES): nobody has seen where the
Desert's own portals lead, so no crossing starts without the route's gate home.
"""

import re
import time
from pathlib import Path

from conquest.character_context import state_path
from conquest.discord_notify import read_json, write_json

# The map-connections edge's "service" for this crossing.
SERVICE = "DesertGate"
TWIN_CITY = 1002
DESERT = 1000
# NPCs that may carry a farmer from Twin City's west into the Desert, tried in
# order: (name, tile, approach), the approach being open ground on the road
# from the landing. One whose page offer() refused and nobody approved is
# skipped from then on (STATE "refused").
CANDIDATES = (
    # Alex 2026-09-30 21:2x, after taking Suicide across by hand: "you have
    # to talk to the general peace and press on 'I see'". His warning page
    # (APPROVED) teleports to (971, 666) on the Desert's map, for free.
    ("GeneralPeace", (60, 463), (64, 468)),
)
# Pages approved in code (STATE "approved" adds the ones seen live).
APPROVED = (
    {
        "npc": "GeneralPeace",
        "records": [
            {
                "kind": 0,
                "option": 0,
                "text": "This is the way to the Desert City. Although you are "
                "excellent, it is dangerous to go ahead.",
            },
            {"kind": 1, "option": 0, "text": "I see."},
            {"kind": 3, "option": 255, "text": ""},
            {"kind": 4, "option": 255, "text": ""},
        ],
        "option": "I see.",
    },
)
# Asked live and not the way: the SpaceMark (96, 323) says "Only Water class
# can buy this teleport scroll." (Suicide, 21:03:58).
# Where the Conductress's "Desert City" ride lands, ten tiles from
# GeneralPeace (Twin City's square is ~1,400 tiles off through the maze).
LANDING = (69, 473)
# Where his page lands on the Desert's map (Suicide, 21:16:05), six tiles
# from the Desert's east portal 1 (977, 668).
DESERT_LANDING = (971, 666)
STATE = Path(state_path(".runtime/desert-gate.json"))
# Closer than this to the NPC, the farmer walks to it instead of riding again.
NEAR_TILES = 200
# The Conductress charges 100 for any ride; no page naming more is pressed.
MAX_FARE = 1000
# Its fare, kept carried until a crossing shows the real one.
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


def approved_steps(state, name=None):
    """Approved pages (APPROVED, then STATE "approved": [{"npc", "records",
    "option"}]), for one NPC when ``name`` is given."""
    steps = state.get("approved")
    saved = [
        s
        for s in (steps if isinstance(steps, list) else [])
        if isinstance(s, dict)
        and isinstance(s.get("records"), list)
        and isinstance(s.get("option"), str)
    ]
    return [
        s
        for s in [*APPROVED, *(s for s in saved if s not in APPROVED)]
        if name is None or s.get("npc") == name
    ]


def next_candidate(state=None):
    """The first NPC not refused yet, or one with approved pages; None when
    every one has refused."""
    state = read_json(STATE) if state is None else state
    refused = state.get("refused") if isinstance(state.get("refused"), dict) else {}
    for candidate in CANDIDATES:
        if candidate[0] not in refused or approved_steps(state, candidate[0]):
            return candidate
    return None


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


def _save_page(records, pressed, name):
    data = read_json(STATE)
    pages = data.get("pages") if isinstance(data.get("pages"), list) else []
    if all(page.get("records") != records for page in pages):
        pages.append(
            {
                "npc": name,
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


def _after_press(loop, actor, records, seconds=None):
    """("arrived", life) once memory shows the Desert's map, ("page", dialog)
    when a page other than ``records`` opens (any page for None), or
    ("closed", None) after ``seconds`` (ARRIVAL_SECONDS)."""
    from conquest.worker import request

    deadline = time.monotonic() + (ARRIVAL_SECONDS if seconds is None else seconds)
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


def _identity_ok(identity, candidate):
    from conquest.market_services import MODELS

    name, spot, _ = candidate
    return (
        identity.get("name") == name
        and identity.get("map_id") == TWIN_CITY
        and identity.get("model") in MODELS[name]
        and max(abs(a - b) for a, b in zip(identity.get("position", ()), spot)) <= 2
    )


def _crossed(loop, before, actor, pressed, life, name):
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
    steps = [{"npc": name, "records": s["records"], "option": s["option"]} for s in pressed]
    saved = [s for s in data.get("approved") or [] if isinstance(s, dict)]
    data["approved"] = saved + [s for s in steps if s not in saved and s not in APPROVED]
    crossings = data.get("crossings") if isinstance(data.get("crossings"), list) else []
    crossings.append(
        {"at": time.time(), "npc": name, "fare": paid, "landing": life["position"]}
    )
    data["crossings"] = crossings[-KEEP_CROSSINGS:]
    write_json(STATE, data)
    loop.terrain = read_terrain(CLIENT_ROOT, DESERT)
    loop.record(
        "desert_gate_crossed",
        npc=name,
        fare=paid,
        landing=life["position"],
        options=[s["option"] for s in pressed],
        activity=f"{name}: in the Desert at {tuple(life['position'])} (fare {paid})",
    )
    if paid is not None and paid > MAX_FARE:
        loop.record(
            "desert_gate_fare_high",
            fare=paid,
            activity=f"{name} charged {paid} silver, above the {MAX_FARE} expected",
        )
    return life["position"]


def _refuse(loop, name, records, pressed):
    """Remember that ``name``'s page was refused; the next try goes to the
    next candidate."""
    data = read_json(STATE)
    refused = data.get("refused") if isinstance(data.get("refused"), dict) else {}
    refused[name] = {"records": records, "at": time.time()}
    data["refused"] = refused
    write_json(STATE, data)
    following = next_candidate(data)
    loop.record(
        "desert_gate_page",
        npc=name,
        records=records,
        pressed=[s["option"] for s in pressed],
        next_npc=following[0] if following else None,
        activity=f"{name}: saved a dialog page nobody approved; nothing pressed"
        + (f"; next try {following[0]}" if following else "; no Desert NPC left to try"),
    )


def cross(loop, candidate=None):
    """Beside a candidate NPC (next_candidate) on Twin City's map: into the
    Desert.

    Returns the landing tile once memory shows the Desert's map. Raises
    ValueError when a page needs approval (it is saved) or the crossing is not
    verified; nothing is pressed twice within one call."""
    from conquest.dialog_geometry import scroll_direction

    candidate = candidate or next_candidate()
    if candidate is None:
        raise ValueError(f"No Desert NPC left to try (pages saved in {STATE.name})")
    name, _, approach = candidate
    life = loop.living()["embedded_controls"]["life"]
    if life["map_id"] != TWIN_CITY:
        raise ValueError(f"{name} stands on Twin City's map")
    home = getattr(getattr(loop, "route", None), "restock_map_id", None)
    if home is not None and not gate_home_carried(loop, home):
        raise ValueError("No gate home carried; not crossing into the Desert")
    actor = life["object_address"]
    approved = approved_steps(read_json(STATE), name)
    loop.travel(approach, arrival_radius=2, activity=f"Walking up to {name}", service_name=name)
    located = loop.town("service-locate", name=name)
    if not _identity_ok(located["identity"], candidate):
        raise ValueError(f"{name}'s identity differs from the survey")
    loop.town("close", window="Shop")
    loop.town("close", window="Inventory")
    before = loop.town("supplies")
    pressed, arrival = [], None
    try:
        # A map-teleport NPC may move the farmer on the click itself.
        loop.town("service-open", name=name)
        outcome, value = _after_press(loop, actor, None, DIALOG_WAIT_SECONDS)
        if outcome == "closed":
            # The first click can miss the sprite (ArcherGod, 2026-09-27).
            loop.town("service-open", name=name)
            outcome, value = _after_press(loop, actor, None, DIALOG_WAIT_SECONDS)
        if outcome == "closed":
            raise ValueError(f"{name} neither opened a dialog nor moved the farmer")
        if outcome == "arrived":
            arrival = value
        dialog = value if outcome == "page" else None
        for _ in range(MAX_STEPS):
            if dialog is None:
                break
            loop.check_stop()
            records = dialog["records"]
            _save_page(records, pressed, name)
            step = next((s for s in approved if s["records"] == records), None)
            desert_before = any(
                "desert" in r.get("text", "").lower()
                for s in pressed
                for r in s["records"]
            )
            option = step["option"] if step else offer(records, desert_before)
            if option is None:
                _refuse(loop, name, records, pressed)
                raise ValueError(f"{name}: a dialog page needs approval (saved in {STATE.name})")
            if option not in [r.get("text") for r in records if r.get("kind") == 1]:
                raise ValueError(f"{name}'s option is not on its page")
            if scroll_direction(dialog, option, dialog.get("viewport")):
                loop.town("service-scroll-dialog", name=name, option=option, records=records)
                dialog = _dialog(loop) or dialog
                continue
            loop.town("service-select", name=name, option=option, records=records)
            pressed.append({"records": records, "option": option})
            outcome, value = _after_press(loop, actor, records)
            if outcome == "arrived":
                arrival = value
                break
            if outcome == "closed":
                raise ValueError(f"{name}'s dialog closed without a crossing into the Desert")
            dialog = value
        else:
            raise ValueError(f"{name}'s dialog went past its step limit")
    finally:
        if arrival is None:
            _dismiss(loop)
    return _crossed(loop, before, actor, pressed, arrival, name)


def fare_reserve():
    """Silver kept for the Conductress and the Desert NPC's fare (the last one
    seen, or a guess until then)."""
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
    unless already near the NPC, then the NPC (next_candidate). Returns the
    landing tile."""
    from conquest.conductress import take_saved_trip
    from conquest.navigation import read_terrain
    from conquest.world_travel import CLIENT_ROOT

    life = loop.living()["embedded_controls"]["life"]
    if life["map_id"] != TWIN_CITY:
        raise ValueError("The way into the Desert starts on Twin City's map")
    candidate = next_candidate()
    if candidate is None:
        raise ValueError(f"No Desert NPC left to try (pages saved in {STATE.name})")
    spot = candidate[1]
    home = getattr(getattr(loop, "route", None), "restock_map_id", None)
    # Checked before any fare: the ride and the crossing are one-way.
    if home is not None and not gate_home_carried(loop, home):
        raise ValueError("No gate home carried; not crossing into the Desert")
    loop.terrain = read_terrain(CLIENT_ROOT, TWIN_CITY)
    here = life["position"]
    if max(abs(a - b) for a, b in zip(here, spot)) > NEAR_TILES:
        # Not yet on its side of the map: the gates and the buff first (the
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
        from conquest import hempknight

        # His daily double EXP was a buff trip's stop; a route reached
        # through Twin City makes no buff trip. A visit never fails the way.
        loop.terrain = read_terrain(CLIENT_ROOT, TWIN_CITY)
        hempknight.visit(loop)
        loop.terrain = read_terrain(CLIENT_ROOT, TWIN_CITY)
        here = loop.living()["embedded_controls"]["life"]["position"]
    if max(abs(a - b) for a, b in zip(here, spot)) > NEAR_TILES:
        if not take_saved_trip(loop, DESERT):
            raise ValueError("The Conductress's Desert City ride is not saved")
        loop.terrain = read_terrain(CLIENT_ROOT, TWIN_CITY)
    return cross(loop, candidate)
