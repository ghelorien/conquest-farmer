"""Learn Scatter from the archer class trainer once the character's level allows.

Memory-only, like every other NPC visit: ArcherGod is found by exact name and
model in the actor scene (market_services.discover), its dialog is read from
the player's dialog records, and success is proven only by the learned-skill
vector holding Scatter (MagicType 8001). Input goes through the qualified
service actions. The only options pressed are one that names Scatter or, before
it, a step whose text is about learning skills; anything else ends the attempt.

ArcherGod stands in his own building (map 1004), whose terrain file the
planner cannot read. Twin City portal 2 at (401, 387) leads in and lands at
(37, 55), four tiles from him at (33, 53) (live 2026-09-27 13:02), inside the
18-tile reach of his dialog. So the farmer never walks inside: it carries a
TwinCityGate scroll (the only way back out), enters, talks from the landing,
and always reads the scroll out again. Learning never blocks farming: an
unexpected dialog or an error is recorded, the farmer keeps leveling, and a
later town visit retries.
"""

import time
from pathlib import Path

from conquest.character_context import state_path
from conquest.discord_notify import read_json, write_json

TRAINER = "ArcherGod"
# Alex (2026-09-27, level 23): "why are you not heading to archer god for a
# new skill?" Earlier he brought the farmer himself; a character can still opt
# out with {"auto_visit": false} in STATE.
AUTO_VISIT_DEFAULT = True
TRAINER_DOOR = 2  # Twin City portal id
TRAINER_MAP = 1004
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


def _locate(loop):
    try:
        return loop.town("service-locate", name=TRAINER)["npc"]
    except ValueError as error:
        if not str(error).startswith("One memory-identified "):
            raise
        return None


def _carries_scroll(loop):
    from conquest.return_scroll import TYPE

    return any(
        i["type_id"] == TYPE and i["amount"] > 0 for i in loop.town("supplies")["items"]
    )


def _exit_scroll(loop):
    """Carry the TwinCityGate that leads back out of the trainer's building.

    The goal's return to town may have read the last one: buy one at the
    Pharmacist (the restock anchor) at its verified 200-silver price.
    """
    if _carries_scroll(loop):
        return True
    from conquest.banking import ensure_transport
    from conquest.return_scroll import TYPE

    ensure_transport(loop, minimum=200)
    if loop.town("supplies")["silver"] < 200:
        return False
    loop.travel(loop.route.restock_anchor)
    loop.town("open", vendor_type=3)
    try:
        products = loop.town("shop", vendor_type=3)["products"]
        if [p["price"] for p in products if p["type_id"] == TYPE] != [200]:
            return False
        result = loop.town("buy", vendor_type=3, type_id=TYPE)
        loop.record(
            "return_scroll_purchase",
            receipt=result,
            activity="Buying a TwinCityGate to leave ArcherGod's building",
        )
    finally:
        loop.town("close", window="Shop")
        loop.town("close", window="Inventory")
    return _carries_scroll(loop)


def _to_town(loop):
    """From the field to Twin City town, the way the level goal's finish goes."""
    from types import SimpleNamespace

    from conquest.return_scroll import in_town, return_to_town
    from conquest.world_travel import travel_to_map

    life = loop.living()["embedded_controls"]["life"]
    if life["map_id"] == 1002 and in_town(SimpleNamespace(**life)):
        return
    return_to_town(loop)
    travel_to_map(loop, 1002)
    loop.travel(loop.route.restock_anchor)


def _leave(loop):
    """Read the scroll out of the building; raises if the farmer stays inside."""
    from conquest.return_scroll import return_to_town

    life = loop.living()["embedded_controls"]["life"]
    if life["map_id"] != TRAINER_MAP:
        return
    _dismiss(loop)
    return_to_town(loop)
    if loop.living()["embedded_controls"]["life"]["map_id"] == TRAINER_MAP:
        raise ValueError("Still inside ArcherGod's building: the return scroll failed")


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
    if loop.living()["embedded_controls"]["life"]["map_id"] != TRAINER_MAP:
        _to_town(loop)
        if not _exit_scroll(loop):
            loop.record(
                "scatter_training_pending",
                reason="no_return_scroll",
                activity="Scatter: no TwinCityGate to leave ArcherGod's building; farming on",
            )
            return False
        from conquest.world_travel import cross_portal

        loop.record(
            "scatter_trainer_departing",
            activity="Walking to ArcherGod to learn Scatter",
        )
        cross_portal(loop, TRAINER_DOOR, TRAINER_MAP, read_destination=False)
    try:
        return _talk(loop)
    finally:
        _leave(loop)


def _talk(loop):
    """Learn Scatter in ArcherGod's dialog from where the farmer stands."""
    if _locate(loop) is None:
        loop.record(
            "scatter_training_failed",
            reason="trainer_not_in_scene",
            activity="Scatter: ArcherGod is not in sight; farming on",
        )
        return False
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
                state = read_json(STATE)
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
