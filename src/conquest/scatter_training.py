"""Learn Scatter from the archer class trainer once the character's level allows.

Memory-only, like every other NPC visit: ArcherGod is found by exact name and
model in the actor scene (market_services.discover), its dialog is read from
the player's dialog records, and success is proven only by the learned-skill
vector holding Scatter (MagicType 8001). Input goes through the qualified
service actions. The only options pressed are one that names Scatter or, before
it, a step whose text is about learning skills; anything else ends the attempt.

An unknown trainer tile is found once by walking a grid over the town until
ArcherGod enters the scene; the tile is kept in this character's state.
Learning never blocks farming: a trainer not found, an unexpected dialog or an
error is recorded, the farmer keeps leveling, and a later town visit retries.
"""

import time
from pathlib import Path

from conquest.character_context import state_path
from conquest.discord_notify import read_json, write_json

TRAINER = "ArcherGod"
# Alex (2026-09-27): "I will bring you to archer god". The trainer stands in
# its own building (map 1004), which the route cannot leave on its own, so
# walking there is opt-in per character: {"auto_visit": true} in STATE.
AUTO_VISIT_DEFAULT = False
# Walkable tile beside the trainer, per map, from a read-only memory survey.
TRAINERS = Path("profiles/archer-trainers.json")
STATE = Path(state_path(".runtime/scatter-training.json"))
# Retail Scatter level; the server decides (the client catalog has no level).
SCATTER_LEVEL = 23
MAX_DIALOG_STEPS = 6
RETRY_SECONDS = 1800
MAX_ATTEMPTS = 6
LEARNING_WORDS = ("learn", "skill", "teach", "study", "train")
# A confirmation right after the Scatter option ("Yes", "OK", ...).
CONFIRM_WORDS = ("yes", "ok", "sure", "confirm", "agree", "accept")


def learned(loop):
    """Whether memory shows Scatter in the learned-skill vector."""
    from conquest.character_context import farmer_name
    from conquest.combat_ranges import read_combat_ranges_for_session

    ranges = read_combat_ranges_for_session(
        loop.care.session, farmer_name(), require_scatter=False
    )
    return ranges["scatter"] is not None


def choose(records, pressed):
    """The option text to press next, or None when no option is safe."""
    if any(r.get("kind") == 2 for r in records):
        return None  # an input field: never type into a trainer dialog
    options = [r["text"] for r in records if r.get("kind") == 1]
    for text in options:
        if "scatter" in text.casefold():
            return text
    for text in options:
        if text not in pressed and any(w in text.casefold() for w in LEARNING_WORDS):
            return text
    if pressed and "scatter" in pressed[-1].casefold():
        # Only straight after choosing Scatter: accept the trainer's confirm.
        for text in options:
            words = text.casefold().replace(".", " ").replace("!", " ").split()
            if text not in pressed and any(w in words for w in CONFIRM_WORDS):
                return text
    return None


def auto_visit():
    """Whether this character may walk to ArcherGod by itself."""
    return bool(read_json(STATE).get("auto_visit", AUTO_VISIT_DEFAULT))


def trainer_tile(map_id):
    """A walkable tile beside the trainer: this character's own scouting
    result first, then the shared surveyed plan."""
    scouted = read_json(STATE).get("trainer") or {}
    if scouted.get("map_id") == map_id and scouted.get("approach"):
        return tuple(scouted["approach"])
    plan = read_json(TRAINERS).get(str(map_id))
    tile = plan.get("approach") if plan else None
    return tuple(tile) if tile else None


# Scene range is about 18 tiles; a 24-tile grid keeps every town tile in view
# of some waypoint.
SCOUT_SPACING = 24


def scout_points(boundary, walkable, spacing=SCOUT_SPACING):
    """Serpentine grid over the town, each point snapped to walkable ground."""
    left, top, right, bottom = boundary
    rows = list(range(top + spacing // 2, bottom + 1, spacing))
    points = []
    for index, y in enumerate(rows):
        xs = list(range(left + spacing // 2, right + 1, spacing))
        for x in xs if index % 2 == 0 else reversed(xs):
            snapped = next(
                (
                    (x + dx, y + dy)
                    for r in range(4)
                    for dx in range(-r, r + 1)
                    for dy in range(-r, r + 1)
                    if max(abs(dx), abs(dy)) == r and walkable((x + dx, y + dy))
                ),
                None,
            )
            if snapped:
                points.append(snapped)
    return points


def _locate(loop):
    try:
        return loop.town("service-locate", name=TRAINER)["npc"]
    except ValueError as error:
        if not str(error).startswith("One memory-identified "):
            raise
        return None


def scout(loop):
    """Walk the town grid until ArcherGod enters the scene; remember its tile.

    Returns the approach tile (the player's own tile when the trainer came
    into view, so it is known walkable ground) or None.
    """
    from conquest.city_travel import city_for

    life = loop.living()["embedded_controls"]["life"]
    map_id = life["map_id"]
    boundary = city_for(map_id)["town_boundary"]
    points = scout_points(boundary, loop.terrain.walkable)
    loop.record(
        "scatter_trainer_scouting",
        waypoints=len(points),
        activity="Scatter: walking the town to find ArcherGod",
    )
    for point in [None, *points]:
        if point is not None:
            try:
                loop.travel(
                    point, service_name=TRAINER, activity="Scatter: looking for ArcherGod"
                )
            except ValueError:
                continue  # an unreachable waypoint: try the next one
        npc = _locate(loop)
        if npc:
            here = loop.living()["embedded_controls"]["life"]["position"]
            state = read_json(STATE)
            state["trainer"] = {
                "map_id": map_id,
                "npc_tile": list(npc["position"]),
                "approach": list(here),
                "found_at": time.time(),
            }
            write_json(STATE, state)
            loop.record(
                "scatter_trainer_found",
                npc_tile=list(npc["position"]),
                approach=list(here),
                activity=f"Scatter: ArcherGod found at {tuple(npc['position'])}",
            )
            return tuple(here)
    loop.record(
        "scatter_training_pending",
        reason="trainer_not_found_in_town",
        activity="Scatter: ArcherGod was not found in town; farming on",
    )
    return None


def _dialog(loop):
    for _ in range(20):
        try:
            return loop.town("service-dialog")
        except ValueError as error:
            if "absent" not in str(error):
                raise
        time.sleep(0.25)
    raise ValueError("ArcherGod dialog did not open")


def learn(loop):
    """Visit ArcherGod and learn Scatter. Returns True once memory proves it."""
    if learned(loop):
        return True
    if not auto_visit():
        loop.record(
            "scatter_training_manual",
            activity="Scatter level reached: learn Scatter at ArcherGod by hand; leveling on",
        )
        return False
    state = read_json(STATE)
    state.update(attempts=state.get("attempts", 0) + 1, last_attempt=time.time())
    write_json(STATE, state)
    life = loop.living()["embedded_controls"]["life"]
    tile = trainer_tile(life["map_id"])
    if tile is None and scout(loop) is None:
        return False
    if tile is not None:
        loop.travel(tile, service_name=TRAINER, activity="Walking to ArcherGod to learn Scatter")
    loop.town("close", window="Shop")
    loop.town("close", window="Inventory")
    loop.town("service-open", name=TRAINER)
    pressed = []
    for step in range(MAX_DIALOG_STEPS):
        try:
            dialog = _dialog(loop)
        except ValueError:
            if step or pressed:
                raise
            # The first click missed the trainer's sprite: open it once more.
            loop.town("service-open", name=TRAINER)
            dialog = _dialog(loop)
        option = choose(dialog["records"], pressed)
        if option is None:
            loop.record(
                "scatter_training_failed",
                reason="no_learning_option",
                records=dialog["records"],
                activity="Scatter: ArcherGod offered no learning option; farming on",
            )
            _dismiss(loop)
            return False
        from conquest.dialog_geometry import scroll_direction

        if scroll_direction(dialog, option, dialog.get("viewport")):
            loop.town(
                "service-scroll-dialog",
                name=TRAINER,
                option=option,
                records=dialog["records"],
            )
            continue
        loop.town("service-select", name=TRAINER, option=option, records=dialog["records"])
        pressed.append(option)
        loop.record("scatter_training_option", option=option, records=dialog["records"])
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            time.sleep(0.3)
            if learned(loop):
                state = read_json(STATE)  # keep a trainer tile scouted meanwhile
                state.update(learned_at=time.time(), dialog=pressed)
                write_json(STATE, state)
                loop.record(
                    "scatter_learned",
                    options=pressed,
                    activity="Learned Scatter from ArcherGod",
                )
                _dismiss(loop)
                return True
    loop.record(
        "scatter_training_failed",
        reason="not_learned_after_dialog",
        options=pressed,
        activity="Scatter: not learned after the ArcherGod dialog; farming on",
    )
    _dismiss(loop)
    return False


def _dismiss(loop):
    try:
        loop.town("service-close-panel", window="Dialog")
    except ValueError:
        pass


def due(loop):
    """Whether this town visit should try the trainer (bounded retries)."""
    from conquest.level_goal import back2classic, goal

    if not back2classic() or goal() or not auto_visit():
        return False
    if getattr(loop, "last_level", 0) < SCATTER_LEVEL:
        return False
    state = read_json(STATE)
    if state.get("learned_at") or state.get("attempts", 0) >= MAX_ATTEMPTS:
        return False
    last = state.get("last_attempt")
    return last is None or time.time() - last >= RETRY_SECONDS


# Find ArcherGod ahead of the Scatter level, so level 23 is a short walk and
# the town grid walk is proven before it matters (at most once an hour).
SCOUT_FROM_LEVEL = 20
SCOUT_RETRY_SECONDS = 3600


def scout_due(loop):
    from conquest.level_goal import back2classic

    if not back2classic() or getattr(loop, "last_level", 0) < SCOUT_FROM_LEVEL:
        return False
    if not auto_visit():
        return False
    state = read_json(STATE)
    if state.get("learned_at") or state.get("trainer"):
        return False
    life = loop.living()["embedded_controls"]["life"]
    if trainer_tile(life["map_id"]) is not None:
        return False
    last = state.get("last_scout")
    return last is None or time.time() - last >= SCOUT_RETRY_SECONDS


def prepare(loop):
    """Scout ArcherGod during a town visit before the Scatter level; never raises."""
    state = read_json(STATE)
    state["last_scout"] = time.time()
    write_json(STATE, state)
    try:
        return scout(loop)
    except Exception as error:  # scouting must not stop farming
        loop.record(
            "scatter_trainer_scouting_failed",
            detail=str(error),
            activity="Scatter: scouting for ArcherGod failed; farming on",
        )
        return None


def attempt(loop):
    """One guarded learning attempt during a town visit; never raises."""
    try:
        return learn(loop)
    except Exception as error:  # a failed attempt must not stop farming
        loop.record(
            "scatter_training_failed",
            reason="error",
            detail=str(error),
            activity="Scatter: training attempt failed; farming on",
        )
        _dismiss(loop)
        return False
