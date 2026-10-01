"""Verified ordinary use of a carried TwinCityGate (or another city's gate) scroll."""

from conquest.character_context import state_path
from dataclasses import asdict
import json
from pathlib import Path
import time
from conquest.discord_notify import read_json, write_json

TYPE = 1060020
# A gate lands in its city's town. Each city's Pharmacist sells its own: the
# installed ini/shop.json lists TwinCityGate only in Twin City's Shop3 (and
# Shop3623), ApeCityGate only in Shop10030, and CastleGate (1060023, "Teleport
# to Phoenix Castle") only in Shop10014, Phoenix's Pharmacist (live 2026-09-27
# 17:43). All cost 200 (ini/itemtype.json). With CastleGates a Phoenix route's
# buff trip and far restocks land in Phoenix town, instead of the Twin City
# Conductress's fare, portal 7 and ~250 tiles from Phoenix's west gate (the
# Bandits, 2026-09-30).
GATES = {1002: TYPE, 1020: 1060022, 1011: 1060023}
GATE_NAMES = {TYPE: "TwinCityGate", 1060022: "ApeCityGate", 1060023: "CastleGate"}
GATE_PRICE = 200
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
# Maps a TwinCityGate leads back to Twin City from, where Twin City travel
# never guesses a portal walk: ArcherGod's building (1004), and Phoenix
# Castle (1011), which has no saved Conductress trip back (a level route
# took Suicide there at 26; live 2026-09-27 17:28).
SCROLL_SOURCES = (1004, 1011)


def may_read(life):
    """Whether a scroll may be read here: outside town on the Twin City map,
    or on a map whose way back to Twin City is a scroll."""
    if life.map_id in SCROLL_SOURCES:
        return True
    return life.map_id == 1002 and not in_town(life)


def gate_readable(life, destination):
    """Where map travel may read a gate to `destination`: anywhere but that
    city's own town and the Market. return_to_town keeps may_read's narrower
    rule: urgent banking from Ape City must not scroll to Twin City."""
    return life.map_id != 1036 and not in_town(life, destination)


def carried(loop, type_id=TYPE):
    return any(
        i["type_id"] == type_id and i["amount"] > 0
        for i in loop.town("supplies")["items"]
    )


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


def in_town(life, map_id=1002):
    from conquest.city_travel import city_for

    if life.map_id != map_id:
        return False
    left, top, right, bottom = city_for(map_id)["town_boundary"]
    return left <= life.position[0] <= right and top <= life.position[1] <= bottom


def receipt(before, item, after, source, life, destination=1002):
    identity = lambda i: (i.type_id, i.amount, i.limit, i.plus)
    expected = {i.uid: identity(i) for i in before.items}
    if item.amount == 1:
        expected.pop(item.uid)
    else:
        expected[item.uid] = (item.type_id, item.amount - 1, item.limit, item.plus)
    return (
        not life.dead_candidate
        and life.current_hp > 0
        and in_town(life, destination)
        and life.object_address == source.object_address
        # A gate read on another map changes the map; on the gate's own map
        # the farmer must have moved from the field into town.
        and (
            life.map_id != source.map_id
            or max(abs(a - b) for a, b in zip(life.position, source.position)) >= 32
        )
        and after.silver == before.silver
        and after.equipped_ammo == before.equipped_ammo
        and {i.uid: identity(i) for i in after.items} == expected
    )


def use(trade, type_id=None):
    """Read one carried gate. Without `type_id` this is the TwinCityGate
    return (may_read's rule); map travel names its gate (gate_readable)."""
    from conquest.discard_loot import inventory_button

    legacy = type_id is None
    type_id = TYPE if legacy else type_id
    destinations = [m for m, kind in GATES.items() if kind == type_id]
    if len(destinations) != 1:
        raise ValueError("Unsupported city gate scroll")
    destination = destinations[0]
    source = trade.life(any_map=True)
    if legacy and not may_read(source):
        raise ValueError(
            "TwinCityGate qualification requires being outside town on Twin City map"
        )
    if not legacy and not gate_readable(source, destination):
        raise ValueError(f"{GATE_NAMES[type_id]} is not read here")
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
    item = next(
        (i for i in before.items if i.type_id == type_id and i.amount > 0), None
    )
    if item is None:
        raise ValueError(f"No {GATE_NAMES[type_id]} scroll is carried")
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
        lambda pair: receipt(before, item, pair[0], source, pair[1], destination),
        "Town scroll transfer unverified; no repeat scroll issued",
        timeout=8,
    )
    result = {
        "state": "verified",
        "time": time.time(),
        "uid": item.uid,
        "type_id": type_id,
        "source": list(source.position),
        "source_map": source.map_id,
        "position": list(life.position),
        "map_id": life.map_id,
        "remaining": after.count(type_id),
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
    """Keep two of the restock town's own gates: TwinCityGates in Twin City,
    ApeCityGates in Ape City (overnight.gate_home_from_afar reads one from
    the far GiantApe and ThunderApe fields)."""
    policy = read_json(POLICY)
    kind = GATES.get(loop.route.restock_map_id)
    if not policy.get("enabled") or kind is None:
        return
    products = loop.town("shop", vendor_type=3)["products"]
    choices = [p for p in products if p["type_id"] == kind]
    if len(choices) != 1 or choices[0]["price"] != GATE_PRICE:
        return
    for _ in range(2):
        bag = loop.town("supplies")
        carried = sum(i["amount"] for i in bag["items"] if i["type_id"] == kind)
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
        result = loop.town("buy", vendor_type=3, type_id=kind)
        loop.record(
            "return_scroll_purchase",
            receipt=result,
            activity=f"Buying a {GATE_NAMES[kind]} return scroll",
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


def read_gate(loop, destination):
    """Read a carried gate to `destination`'s town; True once memory verifies it.

    Map travel prefers a gate over any walk: Ape City's only saved way in is
    the Twin City portal (381, 21) and ~550 tiles across the GiantApe plain,
    where Toxic died at level 47 (602, 278; 2026-09-29 11:51).
    """
    kind = GATES.get(destination)
    policy = read_json(POLICY)
    if kind is None or not (policy.get("enabled") and policy.get("qualified")):
        return False
    life = loop.living()["embedded_controls"]["life"]
    from types import SimpleNamespace

    if not gate_readable(SimpleNamespace(**life), destination) or not carried(
        loop, kind
    ):
        return False
    loop.town("close", window="Shop")
    loop.town("close", window="Warehouse")
    for attempt in range(SCROLL_ATTEMPTS):
        settle(loop)
        try:
            result = loop.town("gate-scroll", type_id=kind)
            break
        except ValueError as error:
            loop.town("close", window="Inventory")
            if str(error) == PRE_INPUT_CHANGE and attempt + 1 < SCROLL_ATTEMPTS:
                continue
            # An unverified submission remains in STATUS and blocks every
            # further scroll input until it is reconciled.
            loop.record(
                "gate_scroll_failed",
                gate=GATE_NAMES[kind],
                detail=str(error),
                activity=f"{GATE_NAMES[kind]} did not complete",
            )
            return False
    loop.town("close", window="Inventory")
    from conquest.city_travel import city_for

    loop.record(
        "gate_scroll_verified",
        receipt=result,
        activity=f"Arrived in {city_for(destination)['name']} with a {GATE_NAMES[kind]}",
    )
    return True


def buy_gate(loop, destination, *, keep=2):
    """Buy gates to `destination` at the open Pharmacist until `keep` are carried.

    Only the verified 200-silver price is paid, and a full bag buys nothing.
    Returns how many are carried afterwards.
    """
    kind = GATES[destination]
    products = loop.town("shop", vendor_type=3)["products"]
    if [p["price"] for p in products if p["type_id"] == kind] != [GATE_PRICE]:
        return sum(
            i["amount"] for i in loop.town("supplies")["items"] if i["type_id"] == kind
        )
    for _ in range(keep):
        bag = loop.town("supplies")
        count = sum(i["amount"] for i in bag["items"] if i["type_id"] == kind)
        if (
            count >= keep
            or bag["silver"] < GATE_PRICE
            or len(bag["items"]) >= bag["capacity"] - loop.route.supplies.minimum_free_slots
        ):
            return count
        result = loop.town("buy", vendor_type=3, type_id=kind)
        loop.record(
            "gate_scroll_purchase",
            receipt=result,
            gate=GATE_NAMES[kind],
            activity=f"Buying a {GATE_NAMES[kind]}",
        )
    return sum(
        i["amount"] for i in loop.town("supplies")["items"] if i["type_id"] == kind
    )
