"""TheHempKnight's daily double EXP in Twin City.

Alex 2026-09-30 (to Laptop2): "talk to him once every 24 hours to get double
exp", and "when you are confident about your route, and you dont die too much
it would be valuable to go grab the double exp". He stands at (424, 344) on
Twin City's square (model 8450, uid 100167), a plain species-0 NPC, so
market_services.discover finds him; buff_trip.trip calls visit() after
MrBuffer, on the way to the Pharmacist.

Nobody has read his dialog yet, so a visit presses nothing that was not
approved for that exact page. Every page seen is saved in STATE["pages"]; an
approved step (STATE["approved"]: [{"records": [...], "option": "...",
"claims": true?}]) is added by hand after reading a capture, and its option is
pressed only while the page's records are exactly the approved ones. A page
nobody approved is saved and the dialog closed. The step marked "claims" (the
double EXP itself) is pressed at most once per CLAIM_INTERVAL and only after
DEATH_QUIET_SECONDS without a death (note_death, from the living hook); it
counts only once the dialog closes after it (Laptop2: a confirmation page
would otherwise lose the day).

Enabled per character by .runtime/buff-trip.json {"double_exp": true}, beside
buff_trip's {"stigma": true}: the visit happens on a buff trip.
"""

import time
from pathlib import Path

from conquest.character_context import state_path
from conquest.discord_notify import read_json, write_json

NAME = "TheHempKnight"
TWIN_CITY = 1002
SPOT = (424, 344)
# Open ground two tiles south-east of him (the Shopboy stands at (424, 350)).
APPROACH = (426, 346)
STATE = Path(state_path(".runtime/hempknight.json"))
# "once every 24 hours", with a margin for the server's own clock.
CLAIM_INTERVAL = 24 * 3600 + 300
# "you dont die too much": no claim within this long of a death.
DEATH_QUIET_SECONDS = 3 * 3600
# A visit that saved a page or failed waits this long before the next one.
RETRY_SECONDS = 30 * 60
MAX_STEPS = 6
DIALOG_WAIT_SECONDS = 5


def enabled():
    from conquest import buff_trip

    return bool(read_json(buff_trip.POLICY).get("double_exp"))


def note_death(now=None):
    """Remember the latest death (the living hook sees dead_candidate often)."""
    now = time.time() if now is None else now
    data = read_json(STATE)
    died = data.get("died_at")
    if type(died) in (int, float) and now - died < 60:
        return False
    data["died_at"] = now
    write_json(STATE, data)
    return True


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


def claim_allowed(state, now):
    claimed = state.get("claimed_at")
    died = state.get("died_at")
    return (type(claimed) not in (int, float) or now - claimed >= CLAIM_INTERVAL) and (
        type(died) not in (int, float) or now - died >= DEATH_QUIET_SECONDS
    )


def next_visit(now=None):
    """'claim' (walk the approved steps to the double EXP), 'explore' (walk the
    approved steps and save the first new page), or None."""
    if not enabled():
        return None
    now = time.time() if now is None else now
    state = read_json(STATE)
    last = state.get("last_attempt")
    if type(last) in (int, float) and now - last < RETRY_SECONDS:
        return None
    approved = approved_steps(state)
    if any(step.get("claims") for step in approved):
        return "claim" if claim_allowed(state, now) else None
    # Read on until a page nobody approved; again only once more is approved.
    if not state.get("pages") or state.get("explored_with", -1) < len(approved):
        return "explore"
    return None


def _save_page(records, pressed):
    data = read_json(STATE)
    pages = data.get("pages") if isinstance(data.get("pages"), list) else []
    if all(page.get("records") != records for page in pages):
        pages.append({"records": records, "after": list(pressed), "seen_at": time.time()})
        data["pages"] = pages
        write_json(STATE, data)


def _dialog(loop, seconds=DIALOG_WAIT_SECONDS):
    """The open dialog, or None when none shows within ``seconds``."""
    deadline = time.monotonic() + seconds
    while True:
        try:
            return loop.town("service-dialog")
        except ValueError as error:
            if "absent" not in str(error) and "not active" not in str(error):
                raise
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.25)


def _dismiss(loop):
    try:
        loop.town("service-close-panel", window="Dialog")
    except ValueError:
        pass


def talk(loop, mode):
    """Open his dialog and press only approved options on exactly their pages.

    Returns the options pressed and whether the double EXP counts as claimed:
    the claiming option was pressed and the dialog then closed, or every page
    after it was approved and walked to the end. A page nobody approved after
    the claim (a confirmation, say) leaves it unclaimed, so the day is not
    lost: that page is saved for approval and the next visit tries again.
    """
    from conquest.dialog_geometry import scroll_direction

    approved = approved_steps(read_json(STATE))
    loop.travel(APPROACH, arrival_radius=1, activity="Walking up to TheHempKnight")
    loop.town("service-locate", name=NAME)
    loop.town("close", window="Shop")
    loop.town("close", window="Inventory")
    loop.town("service-open", name=NAME)
    pressed, claim_pressed = [], False
    try:
        dialog = _dialog(loop)
        if dialog is None:
            # The first click can miss the sprite (ArcherGod, 2026-09-27).
            loop.town("service-open", name=NAME)
            dialog = _dialog(loop)
        for _ in range(MAX_STEPS):
            if dialog is None:
                break  # the last choice closed the dialog
            loop.check_stop()
            records = dialog["records"]
            _save_page(records, pressed)
            step = next((s for s in approved if s["records"] == records), None)
            if step is None:
                loop.record(
                    "hempknight_page",
                    records=records,
                    pressed=pressed,
                    activity="TheHempKnight: saved a dialog page nobody approved; nothing pressed",
                )
                break
            if step.get("claims") and mode != "claim":
                break
            option = step["option"]
            if any(r.get("kind") == 2 for r in records) or option not in [
                r.get("text") for r in records if r.get("kind") == 1
            ]:
                raise ValueError("TheHempKnight's approved option is not on its page")
            if scroll_direction(dialog, option, dialog.get("viewport")):
                loop.town("service-scroll-dialog", name=NAME, option=option, records=records)
                dialog = _dialog(loop)
                continue
            loop.town("service-select", name=NAME, option=option, records=records)
            pressed.append(option)
            claim_pressed = claim_pressed or bool(step.get("claims"))
            time.sleep(0.5)
            dialog = _dialog(loop, seconds=2)
        if dialog is not None and pressed:
            _save_page(dialog["records"], pressed)
    finally:
        _dismiss(loop)
    return pressed, claim_pressed and dialog is None


def visit(loop, now=None):
    """One guarded visit from a Twin City buff trip; never stops the trip.
    Returns what happened: None (not due), 'claimed', 'unconfirmed' (claim
    pressed but a page nobody approved followed), 'explored' or 'failed'."""
    from conquest.overnight import OvernightStopped

    mode = next_visit(now)
    if mode is None:
        return None
    if loop.living()["embedded_controls"]["life"]["map_id"] != TWIN_CITY:
        return None
    data = read_json(STATE)
    data["last_attempt"] = time.time()
    write_json(STATE, data)
    try:
        pressed, claimed = talk(loop, mode)
    except OvernightStopped:
        raise
    except Exception as error:  # the buff trip goes on to the Pharmacist
        _dismiss(loop)
        loop.record(
            "hempknight_failed",
            mode=mode,
            detail=str(error),
            activity="TheHempKnight visit failed; carrying on with the buff trip",
        )
        return "failed"
    data = read_json(STATE)
    if mode == "explore":
        data["explored_with"] = len(approved_steps(data))
    if claimed:
        data["claimed_at"] = time.time()
        data["claims"] = int(data.get("claims") or 0) + 1
    write_json(STATE, data)
    if claimed:
        loop.record(
            "double_exp_claimed",
            options=pressed,
            activity="TheHempKnight: double EXP claimed (next claim in 24 h)",
        )
        return "claimed"
    if {s["option"] for s in approved_steps(data) if s.get("claims")} & set(pressed):
        loop.record(
            "hempknight_claim_unconfirmed",
            options=pressed,
            activity="TheHempKnight: a page nobody approved followed the claim; saved, retrying later",
        )
        return "unconfirmed"
    loop.record(
        "hempknight_explored",
        mode=mode,
        options=pressed,
        activity="TheHempKnight: dialog read and saved; no double EXP claimed",
    )
    return "explored"
