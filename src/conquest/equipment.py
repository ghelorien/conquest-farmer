"""Memory-qualified archer shop upgrades during scheduled town visits."""

from conquest.character_context import state_path
import json
import struct
from dataclasses import asdict
from functools import lru_cache
from pathlib import Path
from conquest.addressing import checked_address
from conquest.memory_build_layout import read_build_layout

SLOTS = {
    "head": 0xBD8,
    "necklace": 0xBE8,
    "armor": 0xBF8,
    "bow": 0xC08,
    "arrows": 0xC18,
    "ring": 0xC28,
    "boots": 0xC48,
}
VENDORS = {5: ("bow", "arrows"), 1: ("ring", "boots", "necklace"), 4: ("armor", "head")}
RESERVE_SILVER = 3000
# Per-character gear rule. Alex 2026-10-02 04:5x: "I don't want you to look
# at replacing your gear unless the gear [doesn't work] anymore", so with
# {"upgrades": false} reviews, gear trips and level_goal buy nothing for a
# slot that holds gear; an empty slot (gear that broke and vanished) is still
# filled. Without the file the reviews keep buying upgrades.
POLICY = Path(state_path(".runtime/equipment-policy.json"))
# The character's attributes in the inventory owner (the role record whose
# name is at +0xA4, classic-1078-inventory-candidate.yaml) on build 1078. Live
# 2026-10-02, Suicide level 64: Strength 40, Spirit 0, Agility 139, Vitality
# 20, then the base maximum HP 1017 = 3 x (40 + 0 + 139) + 24 x 20, the archer
# formula, which checks every read. Toxic's gear circuit bought an IronBow
# (Strength 41, Agility 141) at level 65 that the client would not equip, and
# its HerderBow had just broken (04:01).
ATTRIBUTES = {"strength": 0x3D8, "spirit": 0x3DC, "agility": 0x3E0, "vitality": 0x3E4}
BASE_HP = 0x3F0
# Durability and its limit: the inventory layout's item amount and limit.
DURABILITY = 0x62
CLIENT_ITEMS = r"C:\Program Files\Classic Conquer 2.0\ini\itemtype.json"


# Equip attempts per level and item for a slot that lost its gear.
EMPTY_SLOT_ATTEMPTS = 3
_reserved = [None, frozenset()]


def upgrades_journal():
    return Path(state_path(".runtime/equipment-upgrades.json"))


def upgrades_enabled():
    from conquest.discord_notify import read_json

    return read_json(POLICY).get("upgrades", True) is not False


def reserved_gear_uids():
    """Gear bought to wear whose equip did not land: never banked or sold, so
    the next review equips it. Toxic's IronBow (2026-10-02 04:02, "Equip
    unverified") went to urgent banking a minute later, leaving no bow."""
    from conquest.discord_notify import read_json

    journal = upgrades_journal()
    try:
        info = journal.stat()
    except OSError:
        return frozenset()
    stamp = (str(journal), info.st_mtime_ns, info.st_size)
    if _reserved[0] != stamp:
        attempts = read_json(journal, [])
        if not isinstance(attempts, list):
            attempts = []
        _reserved[:] = [
            stamp,
            frozenset(
                a["uid"]
                for a in attempts
                if isinstance(a, dict)
                and type(a.get("uid")) is int
                and a.get("state") in ("bought", "deferred")
            ),
        ]
    return _reserved[1]


@lru_cache(maxsize=1)
def client_requirements():
    """{type_id: (strength, agility)} for gear from the installed client's
    ini/itemtype.json: shop records carry no attribute requirements."""
    from conquest.character_context import installation_path

    try:
        rows = json.loads(Path(installation_path(CLIENT_ITEMS)).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {
        row["id"]: (row.get("requiredStrength") or 0, row.get("requiredAgility") or 0)
        for row in rows
        if isinstance(row, dict) and type(row.get("id")) is int and category(row["id"])
    }


def read_attributes(session):
    """Strength, agility, vitality and spirit, or None when the read does not
    check out against the base maximum HP."""
    from conquest.addressing import resolve_object
    from conquest.memory_build_layout import CLIENT_SHA256_1078, inventory_reader_layouts

    player, inventory = inventory_reader_layouts(session)
    if inventory.expected_sha256 != CLIENT_SHA256_1078 or inventory.owner_root_rva is None:
        return None
    owner = resolve_object(
        session,
        expected_sha256=inventory.expected_sha256,
        module=player.module,
        root_rva=inventory.owner_root_rva,
        pointer_offsets=inventory.owner_pointer_offsets,
        vtable_rva=inventory.owner_vtable_rva,
    )
    start = min(ATTRIBUTES.values())
    return parse_attributes(session.read_block(owner + start, BASE_HP + 4 - start))


def parse_attributes(raw):
    """The attribute block from +0x3D8 through the base HP, or None."""
    start = min(ATTRIBUTES.values())
    values = {
        name: struct.unpack_from("<I", raw, offset - start)[0]
        for name, offset in ATTRIBUTES.items()
    }
    base_hp = struct.unpack_from("<I", raw, BASE_HP - start)[0]
    if (
        any(value > 2000 for value in values.values())
        or base_hp
        != 3 * (values["strength"] + values["agility"] + values["spirit"])
        + 24 * values["vitality"]
    ):
        return None
    return values


def leveling_archer():
    """A Back2Classic farmer, or one on the level goal, outgrows its gear every
    few levels. America farmers keep their established shop rules."""
    from conquest.potion_tiers import adaptive

    return adaptive()


def category(kind):
    if kind in (1050000, 1050001, 1050002, 1050020):
        return "arrows"
    group = kind // 1000
    return {
        113: "head",
        132: "armor",
        133: "armor",
        500: "bow",
        150: "ring",
        120: "necklace",
        160: "boots",
    }.get(group)


def slots_for(session):
    """Return immutable exact-build slot offsets for raw read primitives.

    ``read_equipment`` reads life through the observer's exact-build reader.
    """
    return read_build_layout(session).equipment_slots


def item_details(session, address, base):
    raw = session.read_block(checked_address(address), 0x78)
    # Legacy offline helpers supplied only a read block, not a pinned session.
    # Production readers always provide the exact fingerprinted selector.
    item_vtable = (
        read_build_layout(session).item_vtable_rva
        if hasattr(session, "expected_sha256")
        else 0x5CF220
    )
    if struct.unpack_from("<Q", raw)[0] != base + item_vtable:
        raise ValueError("Equipment item type changed")
    uid, kind = (
        struct.unpack_from("<I", raw, 8)[0],
        struct.unpack_from("<I", raw, 0x10)[0],
    )
    length, capacity = struct.unpack_from("<QQ", raw, 0x28)
    if (
        not uid
        or not 0 < kind < 100000000
        or not 1 <= length <= 63
        or not length <= capacity <= 1024
    ):
        raise ValueError("Invalid equipment identity")
    name = (
        raw[0x18:0x28]
        if capacity <= 15
        else session.read_block(
            checked_address(struct.unpack_from("<Q", raw, 0x18)[0]), length
        )
    )[:length].decode("utf-8")
    latest = session.read_block(address, 0x78)
    if any(raw[a:b] != latest[a:b] for a, b in ((0, 0x74),)):
        raise ValueError("Equipment changed during observation")
    # Pinned tooltip RVA b1730 counts +67/+68; 255 is an empty socket.
    # +69/+6a are other attributes, not gems. See socket-memory-mapping.md.
    return dict(
        uid=uid,
        type_id=kind,
        name=name,
        level=raw[0x3A],
        profession=raw[0x38],
        sex=raw[0x3B],
        plus=raw[0x6B] if raw[0x6B] <= 12 else None,
        gem1=raw[0x67],
        gem2=raw[0x68],
        attack_min=struct.unpack_from("<H", raw, 0x52)[0],
        attack_max=struct.unpack_from("<H", raw, 0x50)[0],
        defense=struct.unpack_from("<H", raw, 0x54)[0],
        dodge=struct.unpack_from("<H", raw, 0x58)[0],
        durability=struct.unpack_from("<H", raw, DURABILITY)[0],
        max_durability=struct.unpack_from("<H", raw, DURABILITY + 2)[0],
    )


def read_equipment(observer):
    life = observer.read_life()
    s = observer.adapter
    from conquest.memory_build_layout import actual_player_layout

    player = actual_player_layout(s)
    base = next(m["base"] for m in s.modules if m["name"].casefold() == "imconquer.exe")
    actor = life.object_address
    level = struct.unpack("<I", s.read_block(actor + player.level_offset, 4))[0]
    profession = struct.unpack(
        "<I", s.read_block(actor + player.level_offset - 0x14, 4)
    )[0]
    if not 1 <= level <= 140 or not 40 <= profession <= 45:
        raise ValueError("Upgrade requires a memory-identified archer")
    pointers = {
        slot: s.read_block(actor + offset, 8) for slot, offset in slots_for(s).items()
    }
    equipped = {
        slot: item_details(s, struct.unpack("<Q", ptr)[0], base)
        for slot, ptr in pointers.items()
        if struct.unpack("<Q", ptr)[0]
    }
    if any(category(item["type_id"]) != slot for slot, item in equipped.items()):
        raise ValueError("Equipped slot layout differs from archer profile")
    if any(
        s.read_block(actor + slots_for(s)[slot], 8) != ptr
        for slot, ptr in pointers.items()
    ):
        raise ValueError("Equipped slots changed during observation")
    if s.read_block(actor + player.level_offset, 4) != struct.pack("<I", level):
        raise ValueError("Level changed during equipment observation")
    fresh = observer.read_life()
    if fresh.object_address != actor or fresh.dead_candidate:
        raise ValueError("Character changed during equipment observation")
    try:
        attributes = read_attributes(s)
    except (ValueError, OSError, AttributeError):
        attributes = None
    s.assert_identity()
    return {
        "level": level,
        "profession": profession,
        "map_id": fresh.map_id,
        "equipment": equipped,
        "attributes": attributes,
    }


def upgrade_reason(product, state):
    get = (
        product.get
        if isinstance(product, dict)
        else lambda key, default=None: getattr(product, key, default)
    )
    slot = category(get("type_id"))
    if slot not in ("head", "armor", "bow", "ring", "necklace", "boots"):
        return "Unsupported equipment type"
    if not 1 <= get("level", 0) <= state["level"]:
        return "Required level is too high or unknown"
    if get("profession", 0) not in (0, 40, 41):
        return "Not compatible with archer"
    old = state["equipment"].get(slot)
    sex = get("sex", 0)
    known_sex = state["equipment"].get("armor", {}).get("sex", 0)
    if sex and sex != known_sex:
        return "Character requirement does not match"
    if (
        slot == "armor"
        and old
        and (get("type_id") // 100) % 10 != (old["type_id"] // 100) % 10
        # The hundreds digit is the dye colour. A leveling archer's starter
        # Coat (132504, colour 5) matches nothing the Twin City armorer sells.
        and not leveling_archer()
    ):
        return "Armor form does not match"
    attributes = state.get("attributes")
    if attributes:
        needs = client_requirements().get(get("type_id"))
        if needs and (
            needs[0] > attributes["strength"] or needs[1] > attributes["agility"]
        ):
            return "Strength or agility below the requirement"
    if not old:
        return None
    if not upgrades_enabled():
        return "Keeping working gear (upgrades off)"
    if (
        old.get("plus") != 0
        or old["type_id"] % 10 >= 7
        or old.get("gem1")
        or old.get("gem2")
    ):
        return "Keeping protected or unverified equipped gear"
    if get("level", 0) <= old["level"]:
        return "Already wearing this level tier or higher"
    keys = (
        ("attack_min", "attack_max")
        if slot in ("bow", "ring")
        else ("defense", "dodge")
    )
    if not (
        all(get(k, 0) >= old.get(k, 0) for k in keys)
        and any(get(k, 0) > old.get(k, 0) for k in keys)
    ):
        return "No improvement without a stat downgrade"
    return None


def upgrade_candidate(product, state):
    return upgrade_reason(product, state) is None


def review_slots(products, state, silver, reserve=RESERVE_SILVER):
    """Explain every mapped slot against this particular live shop's inventory."""
    from conquest.arrow_upgrades import eligible_arrow

    result = {}
    for slot in SLOTS:
        stock = [p for p in products if category(p["type_id"]) == slot]
        old = state["equipment"].get(slot, {})
        candidates = []
        reasons = []
        for p in stock:
            if slot == "arrows":
                if not eligible_arrow(p, state):
                    reason = "Required level or ammunition type is not supported"
                elif p["attack_min"] + p["attack_max"] <= old.get(
                    "attack_min", 0
                ) + old.get("attack_max", 0):
                    reason = "Already using equal or better ammunition"
                elif p["attack_min"] < old.get("attack_min", 0) or p[
                    "attack_max"
                ] < old.get("attack_max", 0):
                    reason = "No improvement without a stat downgrade"
                else:
                    reason = None
            else:
                reason = upgrade_reason(p, state)
            if reason is None and not 0 < p["price"] <= silver - reserve:
                reason = "Keeping silver for supplies"
            if reason is None:
                candidates.append(p["name"])
            else:
                reasons.append(reason)
        result[slot] = {
            "equipped": old.get("name"),
            "upgrades": candidates,
            "reasons": sorted(set(reasons))
            if stock
            else ["This shop does not stock this slot"],
        }
    return result


def choose_upgrades(products, state, silver, reserve=RESERVE_SILVER):
    chosen = []
    for slot in ("bow", "armor", "ring", "boots", "necklace", "head"):
        candidates = [
            p
            for p in products
            if category(p["type_id"]) == slot
            and upgrade_candidate(p, state)
            and 0 < p["price"] <= silver - reserve
        ]
        if candidates:
            best = max(
                candidates,
                key=lambda p: (
                    p["level"],
                    p["attack_min"] + p["attack_max"] + p["defense"] + p["dodge"],
                ),
            )
            chosen.append(best)
            silver -= best["price"]
    return chosen


def equip_receipt(uid, slot, before_bag, before_gear, after_bag, after_gear):
    if after_gear["equipment"].get(slot, {}).get("uid") != uid:
        return False
    for name, old in before_gear["equipment"].items():
        if (
            name != slot
            and after_gear["equipment"].get(name, {}).get("uid") != old["uid"]
        ):
            return False
    old = before_gear["equipment"].get(slot)
    expected = {(i.uid, i.type_id) for i in before_bag.items if i.uid != uid}
    if old:
        ammo = getattr(before_bag, "equipped_ammo", None)
        if slot != "arrows" or ammo is None or ammo.amount > 0:
            expected.add((old["uid"], old["type_id"]))
    return (
        after_bag.silver == before_bag.silver
        and {(i.uid, i.type_id) for i in after_bag.items} == expected
    )


class EquipmentReview:
    def __init__(self, loop, *, minimum_reserve=0):
        self.loop = loop
        # Silver no purchase may touch (gear_circuit keeps its floor).
        self.minimum_reserve = minimum_reserve

    def visit(self, vendor):
        from conquest.savings import savings_plan

        if savings_plan():
            if vendor == 5:
                from conquest.savings import review_ammunition

                review_ammunition(self.loop)
            return True
        import time
        from conquest.discord_notify import read_json, write_json

        loop = self.loop
        journal = upgrades_journal()
        attempts = read_json(journal, [])
        try:
            state = loop.town("gear")
            bag = loop.town("supplies")
            shop = loop.town("shop", vendor_type=vendor)
            if (
                "products" not in shop
                or "level" not in state
                or "equipment" not in state
            ):
                raise ValueError("Equipment review memory snapshot unavailable")
            map_id = state.get(
                "map_id", getattr(getattr(loop, "terrain", None), "map_id", None)
            )
            if map_id is None:
                raise ValueError("Shop city is unknown")
            from conquest.archer_shop_catalog import record

            record(map_id, vendor, shop["products"], observed_at=time.time())
            if vendor == 5:
                from conquest.arrow_upgrades import review_arrows

                state = review_arrows(loop, shop["products"], state, bag["silver"])
                bag = loop.town("supplies")
            options = [
                p for p in shop["products"] if category(p["type_id"]) in VENDORS[vendor]
            ]
            reserve = RESERVE_SILVER
            if leveling_archer():
                # Keep what the rest of this visit still buys (missing arrow
                # packs, potions, scrolls, fares), not a flat 3,000: a level-11
                # archer kept its level-1 bow over a 204-silver BambooBow.
                from conquest.banking import shopping_budget

                try:
                    reserve = shopping_budget(loop.route, bag, state["level"])
                except ValueError:
                    reserve = RESERVE_SILVER
            reserve = max(reserve, self.minimum_reserve)
            choices = choose_upgrades(options, state, bag["silver"], reserve)
            loop.record(
                "equipment_review",
                level=state["level"],
                vendor=vendor,
                activity=f"Checking level {state['level']} archer equipment",
                map_id=map_id,
                slots=review_slots(shop["products"], state, bag["silver"], reserve),
                upgrades=[p["name"] for p in choices],
                reserve=reserve,
            )
            for product in choices:
                key = (state["level"], product["type_id"])
                tried = sum((a["level"], a["type_id"]) == key for a in attempts)
                # An empty slot (gear that broke) tries again, equipping the
                # carried purchase before buying another; worn gear does not.
                empty = not state["equipment"].get(category(product["type_id"]))
                if tried >= (EMPTY_SLOT_ATTEMPTS if empty else 1):
                    continue
                attempt = {
                    "level": state["level"],
                    "type_id": product["type_id"],
                    "name": product["name"],
                    "timestamp": time.time(),
                    "state": "attempting",
                }
                attempts.append(attempt)
                write_json(journal, attempts)
                try:
                    bag = loop.town("supplies")
                    carried = [
                        i for i in bag["items"] if i["type_id"] == product["type_id"]
                    ]
                    if len(carried) > 1:
                        raise ValueError("Ambiguous carried upgrade")
                    if carried:
                        uid = carried[0]["uid"]
                    else:
                        # The worker re-checks the wallet; hand it this
                        # review's reserve so it does not reimpose 3,000.
                        extra = (
                            {"reserve": min(reserve, RESERVE_SILVER)}
                            if reserve != RESERVE_SILVER
                            else {}
                        )
                        receipt = loop.town(
                            "buy-equipment",
                            vendor_type=vendor,
                            type_id=product["type_id"],
                            **extra,
                        )
                        uid = receipt["uid"]
                        attempt.update(state="bought", uid=uid)
                        write_json(journal, attempts)
                    loop.town("close", window="Shop")
                    receipt = loop.town("equip", uid=uid)
                    attempt.update(state="equipped", receipt=receipt)
                    write_json(journal, attempts)
                    loop.record(
                        "equipment_upgraded",
                        receipt=receipt,
                        activity=f"Equipped {product['name']} (level {product['level']})",
                    )
                    state = loop.town("gear")
                except ValueError as error:
                    attempt.update(state="deferred", detail=str(error))
                    write_json(journal, attempts)
                    loop.record(
                        "equipment_deferred",
                        detail=str(error),
                        activity="Keeping current gear; upgrade deferred",
                    )
                finally:
                    loop.town("close", window="Inventory")
                    loop.town("open", vendor_type=vendor)
            return True
        except ValueError as error:
            loop.record(
                "equipment_review_deferred",
                detail=str(error),
                activity="Equipment review deferred; continuing route",
            )
            return False
