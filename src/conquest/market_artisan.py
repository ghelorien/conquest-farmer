"""Magic Artisan level upgrades in the Market.

Alex (2026-09-29): "Would you be able to go to the market and upgrade your
bow level to the magic artisan? 1. Carry the item, required Meteors to the
Market. 2. Arm yourself with the item you want to upgrade. 3. Talk to Magic
Artisan. 4. Select upgrade the level or quality. 5. Select the item you want
to upgrade. 6. The higher the level or the quality, the more Meteors (Meteor
Tears) or Dragon Balls required." Then "how many meteors will it cost" and
"do it for both" (Toxic and Suicide, both on a Unique HornBow 500077).

The client says only "Magic Artisan in the Market (260,247) can upgrade the
level and quality of equipments, charging Meteors or Dragon Balls"
(ini/tips.json #15); npc.json type 501 (models 5010-5019). Her price is the
server's. Every dialog step is recorded (artisan_dialog), options are chosen
by their text, and the one irreversible choice, her confirmation, is sent
only when the Meteor price she states is at most the Meteors carried and the
character's request allows it. Otherwise the dialog is closed and the price
is kept for Alex (artisan_price).

A visit starts from a restock in a town with a saved Conductress ride that
offers "Market" (Ape City), when .runtime/artisan-request.json is enabled:
the ride, a read-only NPC survey, her price, Meteors from the Market
warehouse (loose ones, then the farmer's own MeteorScrolls unpacked in the
bag) when the carried ones fall short, the upgrade, any leftover valuables
stored in the Market warehouse, and Mark.Controller home.
"""

import re
import time
from pathlib import Path

from conquest.character_context import state_path
from conquest.discord_notify import read_json, write_json

MARKET = 1036
ARTISAN = "MagicArtisan"
# Live scan 2026-09-29 16:37 (Toxic in the Market): MagicArtisan model 5016
# at (179, 208), beside GodlyArtisan (193, 212) and the Warehouseman
# (182, 180). ini/tips.json's (260, 247) is stale: nobody is there. The live
# identity still comes from service-locate.
ARTISAN_TILE = (179, 208)
# MillionaireLee's verified approach: every Market trip has walked to it.
EXCHANGE_APPROACH = (230, 240)
# Service NPCs are clicked from up to 18 tiles and travel stops within 12 of
# one (overnight._travel's service_name check): stand at most this far away.
REACH_TILES = 12
SEARCH_STEPS = 150
METEOR = 1088001
SCROLL = 720027
REQUEST = Path(state_path(".runtime/artisan-request.json"))
STATE = Path(state_path(".runtime/artisan-state.json"))
# Visits allowed per worn item and character level.
VISITS_PER_ITEM = 2
# The next level tier of archer gear (HornBow 45 -> QinBow 50 -> LongBow 55).
TIER_STEP = 5
SLOT_WORDS = {"bow": ("weapon", "bow")}
CONFIRM_WORDS = ("yes", "ok", "sure", "upgrade", "agree", "continue")
DECLINE_WORDS = ("no", "not ", "passing", "later", "cancel", "think", "forget", "leave")


def request():
    data = read_json(REQUEST)
    return data if data.get("enabled") is True else None


def market_plan(home):
    """The home town's saved Conductress ride, taken to her "Market" option."""
    from conquest.gear_circuit import saved_ride

    ride = saved_ride(home, 1002)
    if ride is None:
        return None
    service = ride["service"]
    if not any(
        r.get("kind") == 1 and r.get("text") == "Market" for r in service["records"]
    ):
        return None
    return {
        "source_map": home,
        "destination_map": MARKET,
        "approach": service["approach"],
        "npc": ride["npc_name"],
        "identity": service["identity"],
        "fare": ride["price"],
        "dialogs": [{"records": service["records"], "option": "Market"}],
        "activity": "Heading to the Conductress for the Market",
    }


def exit_plan(home):
    """Mark.Controller's free ride back to the city the farmer came from."""
    from conquest.meteor_banking import POLICY

    returns = [
        origin["return"]
        for origin in read_json(POLICY).get("origins", {}).values()
        if origin.get("return", {}).get("verified")
        and origin["return"].get("npc") == "Mark.Controller"
    ]
    if not returns:
        raise ValueError("No verified Mark.Controller exit is saved")
    plan = dict(returns[0])
    plan.update(
        destination_map=home,
        activity="Leaving the Market by Mark.Controller",
    )
    return plan


def records_text(records, kind):
    return [r["text"] for r in records if r.get("kind") == kind]


def pick(records, words, exclude=()):
    """The one option whose text has one of `words` and none of `exclude`."""
    found = [
        text
        for text in records_text(records, 1)
        if any(w in text.lower() for w in words)
        and not any(x in text.lower() for x in exclude)
    ]
    return found[0] if len(found) == 1 else None


def meteor_price(records):
    """The Meteors her text asks for; None when absent, ambiguous or in Tears."""
    text = " ".join(records_text(records, 0))
    if re.search(r"tear", text, re.I):
        return None
    prices = {
        int(n)
        for n in re.findall(r"(\d+)\s*(?:pieces?\s+of\s+)?meteors?\b", text, re.I)
    }
    if not prices:
        prices = {
            int(n) for n in re.findall(r"meteors?\D{0,12}?(\d+)\b", text, re.I)
        }
    return prices.pop() if len(prices) == 1 else None


def carried_meteors(loop):
    return [
        i
        for i in loop.town("supplies")["items"]
        if i["type_id"] == METEOR and i["amount"] == i["limit"] == 1
    ]


def dialog_after(loop, previous=None, seconds=3.0):
    """Her next dialog: the first stable read that differs from `previous`."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            data = loop.town("service-dialog")
        except ValueError as error:
            if not any(t in str(error) for t in ("not active", "absent", "changed")):
                raise
        else:
            if data["records"] != previous:
                return data["records"]
        time.sleep(0.15)
    raise ValueError("Magic Artisan dialog did not change")


def close_dialog(loop):
    try:
        loop.town("service-close-panel", window="Dialog")
    except ValueError as error:
        if not any(t in str(error) for t in ("not active", "absent")):
            raise


def reach_artisan(loop):
    """Walk to the Magic Artisan's nearest reachable tile; travel stops early
    once she can be clicked."""
    try:
        npc = loop.town("service-locate", name=ARTISAN)["npc"]
    except ValueError as error:
        if not str(error).startswith("One memory-identified "):
            raise
        npc = {"position": list(ARTISAN_TILE)}
    target = tuple(npc["position"])
    approach = approach_tile(loop.terrain, target, loop.living()["embedded_controls"]["life"]["position"])
    loop.travel(
        approach,
        activity="Heading to the Magic Artisan",
        service_name=ARTISAN,
    )
    located = loop.town("service-locate", name=ARTISAN)
    loop.record(
        "artisan_located",
        identity=located["identity"],
        activity=f"Magic Artisan at {located['identity']['position']}",
    )
    return located


def approach_tile(terrain, target, source, reach=REACH_TILES):
    """The reachable tile nearest her, within `reach` tiles, then the shortest
    walk. The Market's terrain fences off its south-east (row y 237 and
    column x 237): MillionaireLee (242, 242) is clicked across it from
    (230, 240), and the tip's (260, 247) has no walkable tile beside her that
    the arrival side reaches (live 2026-09-29 16:11)."""
    from collections import deque

    start = tuple(source)
    steps = {start: 0}
    queue = deque([start])
    while queue:
        x, y = point = queue.popleft()
        if steps[point] >= SEARCH_STEPS:
            continue
        for near in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if near not in steps and terrain.walkable(near):
                steps[near] = steps[point] + 1
                queue.append(near)
    best = None
    for tile, walk in steps.items():
        away = max(abs(tile[0] - target[0]), abs(tile[1] - target[1]))
        if 2 <= away <= reach and (best is None or (away, walk) < best[0]):
            best = ((away, walk), tile)
    if best is None:
        raise ValueError("No walkable approach to the Magic Artisan")
    return best[1]


def talk(loop, slot, *, confirm):
    """Open her dialog, choose the level upgrade of `slot`, read her price, and
    confirm only when `confirm` and the carried Meteors cover it."""
    steps = []
    loop.town("service-open", name=ARTISAN)
    records = dialog_after(loop)
    steps.append(records)
    choice = pick(records, ("level",), ("quality",))
    outcome = {"steps": steps, "price": None, "confirmed": False}
    try:
        if choice is None:
            outcome["stopped"] = "no single level-upgrade option"
            return outcome
        loop.town("service-select", name=ARTISAN, option=choice, records=records)
        records = dialog_after(loop, records)
        steps.append(records)
        choice = pick(records, SLOT_WORDS[slot])
        if choice is None:
            outcome["stopped"] = f"no single {slot} option"
            return outcome
        loop.town("service-select", name=ARTISAN, option=choice, records=records)
        records = dialog_after(loop, records)
        steps.append(records)
        outcome["price"] = price = meteor_price(records)
        answer = pick(records, CONFIRM_WORDS, DECLINE_WORDS)
        carried = len(carried_meteors(loop))
        outcome["carried"] = carried
        if price is None or answer is None:
            outcome["stopped"] = "price or confirmation not readable"
            return outcome
        if not confirm or price > carried:
            outcome["stopped"] = "not confirmed" if not confirm else "Meteors short"
            return outcome
        before = loop.town("gear")["equipment"][slot]
        loop.town("service-select", name=ARTISAN, option=answer, records=records)
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            after = loop.town("gear")["equipment"].get(slot) or {}
            left = len(carried_meteors(loop))
            if after.get("type_id") != before["type_id"] and left == carried - price:
                outcome.update(
                    confirmed=True,
                    before={k: before.get(k) for k in ("name", "type_id", "level")},
                    after={k: after.get(k) for k in ("name", "type_id", "level")},
                )
                return outcome
            time.sleep(0.25)
        outcome["stopped"] = "upgrade unverified"
        try:
            steps.append(dialog_after(loop, records, seconds=1))
        except ValueError:
            pass
        return outcome
    finally:
        loop.record(
            "artisan_dialog",
            steps=steps,
            outcome={k: v for k, v in outcome.items() if k != "steps"},
            activity="Magic Artisan: "
            + (
                f"{outcome['price']} Meteors"
                if outcome.get("price") is not None
                else "price unread"
            ),
        )
        if not outcome.get("confirmed"):
            close_dialog(loop)


def fetch_meteors(loop, need):
    """Carry `need` Meteors: loose ones from the Market warehouse, then the
    farmer's own MeteorScrolls, unpacked one at a time in the bag."""
    from conquest.banking import close_warehouse, open_warehouse
    from conquest.meteor_banking import JOURNAL, approach_market_warehouse

    approach_market_warehouse(loop, "Fetching Meteors for the Magic Artisan")
    open_warehouse(loop)
    try:
        for item in loop.town("warehouse-items")["items"]:
            if len(carried_meteors(loop)) >= need:
                return True
            if item["type_id"] == METEOR:
                loop.town("warehouse-withdraw-meteor", uid=item["uid"])
        for item in loop.town("warehouse-items")["items"]:
            if len(carried_meteors(loop)) >= need:
                return True
            if item["type_id"] != SCROLL:
                continue
            bag = loop.town("supplies")
            if bag["capacity"] - len(bag["items"]) < 11:
                break
            loop.town("warehouse-withdraw-meteor", uid=item["uid"])
            close_warehouse(loop)
            receipt = loop.town("use-meteor-scroll", uid=item["uid"])
            from conquest import stored_scroll_queue as queue

            queue.complete(
                JOURNAL,
                item["uid"],
                {"outcome": "unpacked_for_magic_artisan", "receipt": receipt},
            )
            loop.record(
                "meteor_scroll_unpacked",
                receipt=receipt,
                activity="Unpacked a MeteorScroll for the Magic Artisan",
            )
            open_warehouse(loop)
        return len(carried_meteors(loop)) >= need
    finally:
        close_warehouse(loop)


def store_leftovers(loop):
    """Mark.Controller refuses a farmer carrying valuables: bank them here."""
    from conquest.banking import close_warehouse, open_warehouse
    from conquest.meteor_banking import approach_market_warehouse, carried

    if not carried(loop):
        return
    approach_market_warehouse(loop, "Storing leftover Meteors in the Market")
    open_warehouse(loop)
    try:
        for item in carried(loop):
            receipt = loop.town("warehouse-deposit", uid=item["uid"])
            if receipt.get("verified_in_warehouse") is not True:
                raise ValueError("Market deposit receipt missing")
            loop.record(
                "valuable_stored",
                **receipt,
                activity="Valuable safely stored in Market",
            )
    finally:
        close_warehouse(loop)


def next_tier(worn, level):
    return worn.get("level", 0) + TIER_STEP <= level


def run(loop):
    """One Magic Artisan visit from the restock town; True once it left town."""
    wanted = request()
    if not wanted:
        return False
    home = loop.route.restock_map_id
    plan = market_plan(home)
    if plan is None:
        return False
    slot = wanted.get("slot", "bow")
    gear = loop.town("gear")
    worn = gear["equipment"].get(slot)
    if not worn or not next_tier(worn, gear["level"]):
        return False
    state = read_json(STATE)
    key = f"{gear['level']}:{worn['type_id']}"
    visits = state.get("visits", {})
    if visits.get(key, 0) >= VISITS_PER_ITEM:
        return False
    visits[key] = visits.get(key, 0) + 1
    state.update(visits=visits, started_at=time.time())
    write_json(STATE, state)
    loop.record(
        "artisan_departing",
        item=worn.get("name"),
        activity=f"Taking the {worn.get('name')} to the Magic Artisan in the Market",
    )
    from conquest.meteor_banking import trip
    from conquest.city_travel import city_for
    from conquest.return_scroll import GATES, buy_gate

    prior = loop.phase
    loop.phase = "restocking"
    outcome, left = None, False
    try:
        # Mark.Controller's landing on the home map is unobserved: carry the
        # gate home in case it is far out (the ride's (381, 21) is beyond the
        # GiantApe plain).
        if home in GATES:
            loop.travel(tuple(city_for(home)["services"]["pharmacist"]))
            loop.town("open", vendor_type=3)
            try:
                gates = buy_gate(loop, home, keep=1)
            finally:
                loop.town("close", window="Shop")
                loop.town("close", window="Inventory")
            if gates < 1:
                raise ValueError("No gate home for the Market visit")
        trip(loop, plan)
        left = True
        try:
            from conquest.worker import request as worker

            npcs = worker(loop.info, "sample-npcs", {})
            loop.record("market_npcs", snapshot=npcs.get("snapshot"))
        except (ValueError, OSError, KeyError, AttributeError):
            pass
        reach_artisan(loop)
        outcome = talk(loop, slot, confirm=wanted.get("confirm") is True)
        price = outcome.get("price")
        if (
            not outcome["confirmed"]
            and wanted.get("confirm") is True
            and price is not None
            and price > outcome.get("carried", 0)
            and price <= wanted.get("max_meteors", 0)
        ):
            if fetch_meteors(loop, price):
                reach_artisan(loop)
                outcome = talk(loop, slot, confirm=True)
            else:
                outcome["stopped"] = "not enough Meteors banked"
    except ValueError as error:
        loop.record(
            "artisan_failed",
            detail=str(error),
            activity="Magic Artisan visit stopped; returning to the route",
        )
    except BaseException:
        loop.phase = prior
        raise
    try:
        if left:
            store_leftovers(loop)
            try:
                trip(loop, exit_plan(home))
            except ValueError as error:
                # Out of the Market but not on the home map (the controller
                # may send an Ape City visitor elsewhere): the gate goes home.
                stray = loop.living()["embedded_controls"]["life"]
                if stray["map_id"] == MARKET:
                    raise
                loop.record(
                    "market_exit_elsewhere",
                    map_id=stray["map_id"],
                    position=stray["position"],
                    detail=str(error),
                    activity="Market exit landed on another map; reading the gate home",
                )
            from conquest.return_scroll import near_town, read_gate
            from types import SimpleNamespace

            life = loop.living()["embedded_controls"]["life"]
            loop.record(
                "market_exit_landing",
                position=life["position"],
                map_id=life["map_id"],
                activity=f"Back from the Market at {life['position']}",
            )
            # Near town the walk goes: a gate read there cannot be received.
            if not near_town(SimpleNamespace(**life), home) and not read_gate(loop, home):
                raise ValueError(
                    f"Market exit landed outside town at {life['position']} and no gate home was read"
                )
            loop.travel(tuple(city_for(home)["services"]["pharmacist"]))
    finally:
        loop.phase = prior
    state = read_json(STATE)
    result = {k: v for k, v in (outcome or {}).items() if k != "steps"}
    state.update(last=result, finished_at=time.time())
    if result.get("price") is not None:
        state.setdefault("prices", {})[key] = result["price"]
    write_json(STATE, state)
    loop.record(
        "artisan_complete",
        outcome=result,
        activity=(
            f"Magic Artisan upgraded {result['before']['name']} to {result['after']['name']}"
            if result.get("confirmed")
            else f"Magic Artisan visit ended: {result.get('stopped') or 'no dialog'}"
        ),
    )
    return left
