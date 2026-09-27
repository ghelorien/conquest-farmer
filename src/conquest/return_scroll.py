"""Verified ordinary use of a carried TwinCityGate scroll."""

from conquest.character_context import state_path
from dataclasses import asdict
import json
from pathlib import Path
import time
from conquest.discord_notify import read_json, write_json

TYPE = 1060020
# Per character: a verified first use qualifies this character's client, and
# a release folder is immutable (a changed file there blocks route launches).
# Absent means disabled.
POLICY_NAME = ".runtime/return-scroll.json"
POLICY = Path(state_path(POLICY_NAME))
STATUS = Path(state_path("reports/return-scroll/status.json"))
# use() refuses before any scroll input when the farmer or bag moved while the
# Inventory opened; that refusal alone is safe to retry.
PRE_INPUT_CHANGE = "Player or inventory changed before scroll input"
SCROLL_ATTEMPTS = 3
# Silver on hand before a second (spare) scroll is bought.
SPARE_SCROLL_SILVER = 1000
# Twin City buildings a scroll leaves (ArcherGod's, map 1004): Twin City
# travel never guesses a portal walk.
BUILDINGS = (1004,)


def may_read(life):
    """Whether a scroll may be read here: outside town on the Twin City map,
    or inside one of its buildings."""
    if life.map_id in BUILDINGS:
        return True
    return life.map_id == 1002 and not in_town(life)


def settle(loop, seconds=4.0, steady=0.6):
    """Wait until position, bag, silver and ammunition stay unchanged.

    Farming stops just before a town trip; the last jump, shot or pickup can
    still be landing and would make the scroll's pre-input check refuse.
    """
    deadline = time.monotonic() + seconds
    last, since = None, None
    while time.monotonic() < deadline:
        life = loop.living()["embedded_controls"]["life"]
        bag = loop.town("supplies")
        key = json.dumps(
            [
                life["position"],
                bag.get("items"),
                bag.get("silver"),
                bag.get("equipped_ammo"),
            ],
            sort_keys=True,
            default=str,
        )
        now = time.monotonic()
        if key != last:
            last, since = key, now
        elif now - since >= steady:
            return True
        time.sleep(0.2)
    return False


def in_town(life):
    from conquest.city_travel import city_for

    if life.map_id != 1002:
        return False
    left, top, right, bottom = city_for(1002)["town_boundary"]
    return left <= life.position[0] <= right and top <= life.position[1] <= bottom


def receipt(before, item, after, source, life):
    identity = lambda i: (i.type_id, i.amount, i.limit, i.plus)
    expected = {i.uid: identity(i) for i in before.items}
    if item.amount == 1:
        expected.pop(item.uid)
    else:
        expected[item.uid] = (item.type_id, item.amount - 1, item.limit, item.plus)
    return (
        not life.dead_candidate
        and life.current_hp > 0
        and in_town(life)
        and life.object_address == source.object_address
        and max(abs(a - b) for a, b in zip(life.position, source.position)) >= 32
        and after.silver == before.silver
        and after.equipped_ammo == before.equipped_ammo
        and {i.uid: identity(i) for i in after.items} == expected
    )


def use(trade):
    from conquest.discard_loot import inventory_button

    source = trade.life(any_map=True)
    if not may_read(source):
        raise ValueError(
            "TwinCityGate qualification requires being outside town on Twin City map"
        )
    if read_json(STATUS).get("state") == "submitted":
        raise ValueError(
            "Previous scroll submission needs receipt verification; no repeat issued"
        )
    for name in ("Shop", "Warehouse"):
        try:
            trade.shop.gui.read(name)
        except ValueError as error:
            if "not active" not in str(error) and "absent" not in str(error):
                raise
        else:
            raise ValueError("Close town panels before using a return scroll")
    before = trade.inventory.read()
    item = next((i for i in before.items if i.type_id == TYPE and i.amount > 0), None)
    if item is None:
        raise ValueError("No TwinCityGate scroll is carried")
    try:
        trade.shop.gui.read("Inventory")
    except ValueError as error:
        if "not active" not in str(error) and "absent" not in str(error):
            raise
        trade.input_attempted = True
        trade.click(inventory_button(trade.shop.gui))
        trade.verified_read(
            lambda: trade.shop.gui.read("Inventory"),
            bool,
            "Inventory opening unverified",
        )
    grid = trade.shop.gui.read("Inventory/##ItemGrid_")
    if grid.size != (407.0, 175.0) or grid.scroll != (0.0, 0.0):
        raise ValueError("Return scroll inventory grid differs")
    fresh = trade.life(any_map=True)
    bag = trade.inventory.read()
    if (
        fresh.dead_candidate
        or fresh.map_id != source.map_id
        or fresh.position != source.position
        or fresh.object_address != source.object_address
        or bag.items != before.items
        or bag.silver != before.silver
        or bag.equipped_ammo != before.equipped_ammo
        or trade.shop.gui.read("Inventory/##ItemGrid_") != grid
    ):
        raise ValueError("Player or inventory changed before scroll input")
    point = (
        round(grid.position[0] + 20 + 40 * (item.slot % 10)),
        round(grid.position[1] + 20 + 40 * (item.slot // 10)),
    )
    trade.input_attempted = True
    write_json(
        STATUS,
        {
            "state": "submitted",
            "time": time.time(),
            "uid": item.uid,
            "before": asdict(before),
            "source": asdict(source),
        },
    )
    trade.click(point, "right")
    after, life = trade.verified_read(
        lambda: (trade.inventory.read(), trade.life(any_map=True)),
        lambda pair: receipt(before, item, pair[0], source, pair[1]),
        "Town scroll transfer unverified; no repeat scroll issued",
        timeout=8,
    )
    result = {
        "state": "verified",
        "time": time.time(),
        "uid": item.uid,
        "type_id": TYPE,
        "source": list(source.position),
        "position": list(life.position),
        "map_id": life.map_id,
        "remaining": after.count(TYPE),
        "silver": after.silver,
    }
    write_json(STATUS, result)
    policy = read_json(POLICY)
    policy.update(enabled=True, qualified=True)
    write_json(POLICY, policy)
    return result


def secure_one(loop, keep=0):
    """Buy one return scroll before any potion when none is carried.

    Potions came first and spent the silver: Toxic left town without a scroll
    (134 silver, stock needs 400) and died walking ~1,000 tiles home through
    the Poltergeists (live 2026-09-27 15:35). The scroll is the way home.
    ``keep`` is silver it must leave (the arrow pack a short quiver needs).
    """
    policy = read_json(POLICY)
    if not policy.get("enabled") or loop.route.restock_map_id != 1002:
        return False
    bag = loop.town("supplies")
    if any(i["type_id"] == TYPE and i["amount"] > 0 for i in bag["items"]):
        return False
    products = loop.town("shop", vendor_type=3)["products"]
    choices = [p for p in products if p["type_id"] == TYPE]
    if (
        len(choices) != 1
        or choices[0]["price"] != 200
        or bag["silver"] < choices[0]["price"] + keep
        or len(bag["items"]) >= bag["capacity"] - loop.route.supplies.minimum_free_slots
    ):
        return False
    result = loop.town("buy", vendor_type=3, type_id=TYPE)
    loop.record(
        "return_scroll_purchase",
        receipt=result,
        activity="Buying a TwinCityGate return scroll before potions",
    )
    return True


def stock(loop):
    policy = read_json(POLICY)
    if not policy.get("enabled") or loop.route.restock_map_id != 1002:
        return
    products = loop.town("shop", vendor_type=3)["products"]
    choices = [p for p in products if p["type_id"] == TYPE]
    if len(choices) != 1 or choices[0]["price"] != 200:
        return
    for _ in range(2):
        bag = loop.town("supplies")
        carried = sum(i["amount"] for i in bag["items"] if i["type_id"] == TYPE)
        if carried >= 2:
            return
        # The spare waits for a comfortable wallet: short of silver it took
        # the arrows' money and the farmer left with 402 arrows (15:46).
        if (
            bag["silver"] < (400 if not carried else SPARE_SCROLL_SILVER)
            or len(bag["items"])
            >= bag["capacity"] - loop.route.supplies.minimum_free_slots
        ):
            return
        result = loop.town("buy", vendor_type=3, type_id=TYPE)
        loop.record(
            "return_scroll_purchase",
            receipt=result,
            activity="Buying a TwinCityGate return scroll",
        )


def return_to_town(loop):
    policy = read_json(POLICY)
    if not (policy.get("enabled") and policy.get("qualified")):
        return False
    life = loop.living()["embedded_controls"]["life"]
    from types import SimpleNamespace

    if not may_read(SimpleNamespace(**life)):
        return False
    bag = loop.town("supplies")
    if not any(i["type_id"] == TYPE and i["amount"] > 0 for i in bag["items"]):
        return False
    loop.town("close", window="Shop")
    loop.town("close", window="Warehouse")
    for attempt in range(SCROLL_ATTEMPTS):
        settle(loop)
        try:
            result = loop.town("return-scroll")
            break
        except ValueError as error:
            loop.town("close", window="Inventory")
            if str(error) == PRE_INPUT_CHANGE and attempt + 1 < SCROLL_ATTEMPTS:
                continue
            # Walking stays the fallback. An unverified submission remains
            # in STATUS and blocks further scroll input until it is reconciled.
            loop.record(
                "return_scroll_failed",
                detail=str(error),
                activity="Return scroll did not complete; walking to town",
            )
            return False
    loop.town("close", window="Inventory")
    loop.record(
        "return_scroll_verified",
        receipt=result,
        activity="Returned to Twin City with a scroll",
    )
    return True
