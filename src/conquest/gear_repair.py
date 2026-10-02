"""Repair worn gear at a shop, the way Alex does it by hand.

Alex 2026-10-02 04:4x: "Keep an eye on gear durability and go automatically
repair before it gets destroyed". His way: take the item off into the bag,
press the shop's Repair button (the bar under every shop's item grid,
screenshot 04:50), click the item in the bag, then wear it again. Toxic's
HerderBow had broken at 0 durability that morning and vanished.

No farmer step has opened the client's Status (equipment) window before, so
this first maps it: probe() opens it from ##Control, reports the windows it
creates (held open briefly for a screenshot) and closes it again. A probe is
asked for by .runtime/gear-window-probe.json and runs once, in town, before
the hunt leaves.
"""

import time
from pathlib import Path

from conquest.capture import CaptureUnavailable
from conquest.character_context import state_path
from conquest.discord_notify import read_json, write_json
from conquest.merchants.memory import GuiReader, HoverNotReady, unpack

STATUS_COLUMN = 0
# Long enough for an outside screenshot of the open window.
PROBE_HOLD_SECONDS = 3.0
PROBE = Path(state_path(".runtime/gear-window-probe.json"))
# The probe (2026-10-02 05:56) and its screenshot: "STATUS & SKILLS" opens as
# "Status", 506 x 376 at (70, 109), on its Status tab. Its gear column runs top
# down as below, slot centres 208 px right of the window's left edge from
# 83 px under its top, 43.8 px apart.
STATUS_SIZE = (506.0, 376.0)
GEAR_COLUMN = ("head", "necklace", "ring", "bow", "arrows", "armor", "boots")
SLOT_X, SLOT_Y, SLOT_STEP = 208, 83, 43.8
# A restock repairs any piece below this share of its durability: Alex's
# repair (04:40, ~810 silver for four pieces) left them full; the HerderBow
# then lost ~20 points an hour of its 5,099.
REPAIR_BELOW = 0.6
JOURNAL = Path(state_path(".runtime/gear-repair.json"))
REPAIR_LABEL = "Repair"


def windows(trade):
    return {w["name"]: w for w in GuiReader.for_session(trade.observer.adapter).windows()}


def hovered_window(gui):
    context = unpack(gui.session, gui.base + gui.context_rva, "<Q")[0]
    return unpack(gui.session, context + 0x3EC0, "<Q")[0]


def slot_point(window, slot):
    x, y, width, height = window["geometry"]
    point = (round(x + SLOT_X), round(y + SLOT_Y + SLOT_STEP * GEAR_COLUMN.index(slot)))
    if not (x < point[0] < x + width and y < point[1] < y + height):
        raise ValueError("Gear slot lies outside the Status window")
    return point


def probe(trade):
    """Open the Status window, report what opened, close it again."""
    from conquest.discard_loot import control_button
    from conquest.panel_close import click_close

    trade.life()
    before = windows(trade)
    point = control_button(trade.shop.gui, STATUS_COLUMN)
    trade.input_attempted = True
    trade.click(point)
    trade.verified_read(
        lambda: windows(trade),
        lambda now: bool(set(now) - set(before)),
        "Status window did not open; no repeat input issued",
    )
    time.sleep(PROBE_HOLD_SECONDS)
    opened = {
        name: {"geometry": [round(v, 1) for v in w["geometry"]], "scroll": list(w["scroll"])}
        for name, w in windows(trade).items()
        if name not in before
    }
    # The button toggles the window; its own close button is the fallback.
    trade.click(point)
    try:
        trade.verified_read(
            lambda: windows(trade),
            lambda now: not set(now) - set(before),
            "Status window stayed open",
        )
        closed_by = "button"
    except ValueError:
        for name in [n for n in opened if "/" not in n]:
            click_close(trade, name, display_only=True)
        trade.verified_read(
            lambda: windows(trade),
            lambda now: not set(now) - set(before),
            "Status window stayed open after its close button",
        )
        closed_by = "close"
    return {"button": list(point), "opened": opened, "closed_by": closed_by}


def probe_if_asked(loop):
    """In town before the hunt leaves: one probe when asked for."""
    from types import SimpleNamespace

    from conquest.return_scroll import in_town

    if not read_json(PROBE).get("pending"):
        return None
    life = loop.living()["embedded_controls"]["life"]
    if not in_town(SimpleNamespace(**life), loop.route.restock_map_id):
        return None  # never stand still in the field for it
    PROBE.unlink(missing_ok=True)  # once, whatever happens
    loop.record("gear_window_probe_opening", activity="Opening the Status window to map gear slots")
    try:
        result = loop.town("gear-window")
    except ValueError as error:
        loop.record("gear_window_probe_failed", detail=str(error), activity="Status window probe failed")
        return None
    loop.record("gear_window_probe", result=result, activity="Mapped the Status window")
    return result


def open_status(trade):
    """The Status window on its Status tab, opened from ##Control if needed."""
    from conquest.discard_loot import control_button

    status = windows(trade).get("Status")
    if status is None:
        point = control_button(trade.shop.gui, STATUS_COLUMN)
        trade.input_attempted = True
        trade.click(point)
        status = trade.verified_read(
            lambda: windows(trade).get("Status"),
            bool,
            "Status window did not open; no repeat input issued",
        )
    if tuple(status["geometry"][2:]) != STATUS_SIZE:
        close_status(trade)
        raise ValueError("Status window layout differs from its mapped Status tab")
    return status


def close_status(trade):
    from conquest.discard_loot import control_button
    from conquest.panel_close import click_close

    if "Status" not in windows(trade):
        return
    trade.input_attempted = True
    trade.click(control_button(trade.shop.gui, STATUS_COLUMN))
    try:
        trade.verified_read(lambda: windows(trade), lambda now: "Status" not in now, "Status window stayed open")
    except ValueError:
        click_close(trade, "Status", display_only=True)
        trade.verified_read(
            lambda: windows(trade), lambda now: "Status" not in now, "Status window stayed open"
        )



def open_bag(trade):
    """The bag's item grid, opening the Inventory from ##Control if needed."""
    from conquest.discard_loot import inventory_button

    try:
        trade.shop.gui.read("Inventory")
    except ValueError as error:
        if "not active" not in str(error) and "absent" not in str(error):
            raise
        trade.input_attempted = True
        trade.click(inventory_button(trade.shop.gui))
        trade.verified_read(lambda: trade.shop.gui.read("Inventory"), bool, "Inventory opening unverified")
    grid = trade.shop.gui.read("Inventory/##ItemGrid_")
    if grid.size != (407.0, 175.0) or grid.scroll != (0.0, 0.0):
        raise ValueError("Inventory grid differs from the qualified layout")
    return grid


def unequip(trade, slot):
    """Take one worn piece off into the bag: a double-click on its Status
    slot. Alex 2026-10-02 07:5x: "its double click on the item, and for bow
    you gotta remove the arrow first" (repair_worn takes the arrows off
    first). A right-click (06:54) and a drag (07:39) did nothing."""
    from conquest.equipment import read_equipment
    from conquest.merchants.driver import wait_hover_validation

    if slot not in GEAR_COLUMN:
        raise ValueError("Unsupported gear slot")
    trade.life()
    open_panels = set(windows(trade)) & {"Shop", "Warehouse", "Booth", "Dialog"}
    if open_panels:
        raise ValueError(f"Close {sorted(open_panels)[0]} before unequipping")
    bag = trade.inventory.read()
    worn = read_equipment(trade.observer)["equipment"]
    item = worn.get(slot)
    if not item:
        raise ValueError(f"No {slot} is worn")
    if slot == "bow" and worn.get("arrows"):
        raise ValueError("Take the arrows off before the bow")
    if len(bag.items) >= bag.capacity:
        raise ValueError("No bag room to take gear off into")
    carried = sum(i.amount for i in bag.items if i.type_id == item["type_id"])

    def off(pair):
        now_bag, now_gear = pair
        if now_gear["equipment"].get(slot):
            return False
        if any(i.uid == item["uid"] for i in now_bag.items):
            return True
        # Arrows may join a carried pack of their kind.
        return slot == "arrows" and sum(
            i.amount for i in now_bag.items if i.type_id == item["type_id"]
        ) > carried

    status = open_status(trade)
    try:
        point = slot_point(status, slot)
        gui = GuiReader.for_session(trade.observer.adapter)

        def guard():
            fresh = windows(trade).get("Status")
            if not fresh or (fresh["address"], fresh["geometry"]) != (status["address"], status["geometry"]):
                raise CaptureUnavailable("Status window moved before unequipping; nothing pressed")
            if read_equipment(trade.observer)["equipment"].get(slot, {}).get("uid") != item["uid"]:
                raise CaptureUnavailable("Worn gear changed before unequipping; nothing pressed")
            if hovered_window(gui) != status["address"]:
                raise HoverNotReady("Pointer is not over the Status window")

        trade.input_attempted = True
        trade.click(point, before_press=lambda: wait_hover_validation(guard, lambda: None), double=True)
        trade.verified_read(
            lambda: (trade.inventory.read(), read_equipment(trade.observer)),
            off,
            "Unequip unverified; no repeat input issued",
            timeout=3,
        )
    finally:
        # Never leave it open: over the field it takes the farmer's clicks
        # (2026-10-02 06:54, 35 kills a minute until closed).
        close_status(trade)
    return {
        "uid": item["uid"],
        "type_id": item["type_id"],
        "slot": slot,
        "durability": item.get("durability"),
        "max_durability": item.get("max_durability"),
    }

def confirm_prompt(gui):
    """The shared ###Confirm prompt's strings when one is shown, else None."""
    from conquest.merchants import open_booth_control_1078 as control
    from conquest.merchants.memory import string

    try:
        model = control._model(gui)
        if gui.session.read_block(model + 12, 1) != b"\x01":
            return None
        values = tuple(string(gui.session, model + offset, 256) for offset in (0x48, 0x68, 0x88, 0xA8))
    except ValueError:
        return None
    return dict(zip(("title", "message", "positive_label", "negative_label"), values))


def answer_confirm(trade, gui, prompt, positive):
    """Press Yes (the line above No) or No on the shown prompt, hover-checked."""
    from conquest.merchants import open_booth_control_1078 as control
    from conquest.merchants.driver import wait_hover_validation

    window, no_point = control.locate(gui, prompt, reader=confirm_prompt)
    label = prompt["positive_label"] if positive else prompt["negative_label"]
    # Full-width buttons on consecutive lines; ImGui's line height plus its
    # item spacing separates them. Each candidate is pressed only once memory
    # shows the pointer over that exact label.
    offsets = (22, 26, 18) if positive else (0,)
    for offset in offsets:
        point = (no_point[0], no_point[1] - offset)

        def guard():
            if confirm_prompt(gui) != prompt:
                raise CaptureUnavailable("Confirmation changed before answering; no button pressed")
            gui.assert_hovered(window, label)

        try:
            trade.input_attempted = True
            trade.click(point, before_press=lambda: wait_hover_validation(guard, lambda: None))
            return label
        except HoverNotReady:
            continue
    raise ValueError(f"Could not hover the confirmation's {label}; nothing pressed")


def repair_item(trade, uid):
    """Shop open: press Repair, click the carried piece; verified by memory."""
    from conquest.discard_loot import inventory_button
    from conquest.merchants.driver import wait_hover_validation

    trade.life()
    bag = trade.inventory.read()
    matches = [i for i in bag.items if i.uid == uid]
    if len(matches) != 1 or matches[0].slot is None:
        raise ValueError("The piece to repair is not carried")
    item = matches[0]
    if item.amount >= item.limit:
        return {"uid": uid, "durability": item.amount, "max_durability": item.limit, "cost": 0}
    shop = trade.shop.gui.read("Shop")
    grid = trade.shop.gui.read("Shop/##ShopGrid_")
    try:
        trade.shop.gui.read("Inventory")
    except ValueError as error:
        if "not active" not in str(error) and "absent" not in str(error):
            raise
        trade.input_attempted = True
        trade.click(inventory_button(trade.shop.gui))
        trade.verified_read(lambda: trade.shop.gui.read("Inventory"), bool, "Inventory opening unverified")
    bag_grid = trade.shop.gui.read("Inventory/##ItemGrid_")
    if bag_grid.size != (407.0, 175.0) or bag_grid.scroll != (0.0, 0.0):
        raise ValueError("Inventory grid differs from the qualified layout")
    # The Repair bar fills the space between the item grid and the shop's
    # bottom edge (04:50 screenshot: shop 288 x 438, grid 248 x 370).
    x, y = shop.position
    width, height = shop.size
    below = grid.position[1] + grid.size[1]
    repair_point = (round(x + width / 2), round((below + y + height) / 2))
    if not below < repair_point[1] < y + height:
        raise ValueError("No room for the Repair bar under the shop grid")
    gui = GuiReader.for_session(trade.observer.adapter)
    shop_window = windows(trade)["Shop"]

    def guard_repair():
        if trade.shop.gui.read("Shop") != shop:
            raise CaptureUnavailable("Shop moved before Repair; no button pressed")
        gui.assert_hovered(shop_window, REPAIR_LABEL)

    trade.input_attempted = True
    trade.click(repair_point, before_press=lambda: wait_hover_validation(guard_repair, lambda: None))
    time.sleep(0.2)
    cell = (
        round(bag_grid.position[0] + 20 + 40 * (item.slot % 10)),
        round(bag_grid.position[1] + 20 + 40 * (item.slot // 10)),
    )

    def guard_item():
        if trade.shop.gui.read("Inventory/##ItemGrid_") != bag_grid:
            raise CaptureUnavailable("Bag moved before the repair click; no button pressed")
        now = [i for i in trade.inventory.read().items if i.uid == uid]
        if len(now) != 1 or now[0].slot != item.slot:
            raise CaptureUnavailable("The piece moved before the repair click; no button pressed")

    trade.click(cell, before_press=guard_item)
    answered = None
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        now = trade.inventory.read()
        piece = next((i for i in now.items if i.uid == uid), None)
        if piece is not None and piece.amount >= piece.limit and now.silver < bag.silver:
            return {
                "uid": uid,
                "durability": piece.amount,
                "max_durability": piece.limit,
                "cost": bag.silver - now.silver,
                "confirmed": answered,
            }
        prompt = confirm_prompt(gui) if answered is None else None
        if prompt is not None:
            text = f"{prompt['title']} {prompt['message']}".lower()
            if "repair" in text:
                answered = answer_confirm(trade, gui, prompt, positive=True)
                deadline = time.monotonic() + 4
            else:
                answer_confirm(trade, gui, prompt, positive=False)
                raise ValueError(f"Unexpected confirmation during repair: {prompt['title']!r}")
        time.sleep(0.1)
    raise ValueError("Repair unverified; no repeat input issued")


def journal():
    data = read_json(JOURNAL, {})
    return data if isinstance(data, dict) else {}


def off_body_uids():
    """Pieces taken off for repair and not yet worn again."""
    return frozenset(
        row["uid"]
        for row in journal().get("pieces", {}).values()
        if isinstance(row, dict) and type(row.get("uid")) is int and row.get("state") != "worn"
    )


def _note(slot, **fields):
    data = journal()
    pieces = data.setdefault("pieces", {})
    pieces[slot] = {**pieces.get(slot, {}), **fields, "at": time.time()}
    write_json(JOURNAL, data)


def worn_out(gear, test_slot=None):
    """Worn slots below REPAIR_BELOW (or the one asked for), weakest first."""
    rows = []
    for slot in GEAR_COLUMN:
        item = gear.get("equipment", {}).get(slot)
        if slot == "arrows" or not item or not item.get("max_durability"):
            continue
        share = item["durability"] / item["max_durability"]
        if share < REPAIR_BELOW or (slot == test_slot and share < 1):
            rows.append((share, slot))
    return [slot for _, slot in sorted(rows)]


def rewear_arrows(loop, arrows):
    """The arrows taken off for a bow repair, or a carried pack of their kind."""
    bag = loop.town("supplies")["items"]
    uids = [i["uid"] for i in bag if i.get("uid") == arrows.get("uid")] or [
        i["uid"] for i in bag if i.get("type_id") == arrows.get("type_id")
    ]
    if not uids:
        raise ValueError("The arrows taken off for the bow repair are not carried")
    loop.town("close", window="Shop")
    loop.town("equip-arrows", uid=uids[0])


def rewear_left_off(loop):
    """Wear again what a failed repair left in the bag: the bow before its
    arrows. Nothing to do (and no town call) when every piece is worn."""
    pieces = journal().get("pieces", {})
    left = [
        (slot, row)
        for slot, row in pieces.items()
        if isinstance(row, dict) and row.get("state") not in (None, "worn")
    ]
    for slot, row in sorted(left, key=lambda pair: pair[0] == "arrows"):
        if slot == "arrows":
            rewear_arrows(loop, row)
        elif row.get("uid") in {i["uid"] for i in loop.town("supplies")["items"]}:
            loop.town("close", window="Shop")
            loop.town("equip", uid=row["uid"])
        _note(slot, state="worn")
    return [slot for slot, _ in left]


def repair_worn(loop, vendor=5, gear=None):
    """Restock step at a shop: repair worn pieces, always wearing them again.

    Pieces left off by an earlier failure are worn first. `gear` is the
    restock review's own read (EquipmentReview.gear); without one nothing is
    repaired. Each piece: shop closed, unequip (the bow's arrows first), shop
    open, Repair, shop closed, equip (the bow, then its arrows).
    """
    rewear_left_off(loop)
    if gear is None:
        return []
    data = journal()
    test_slot = data.pop("test_slot", None)
    if test_slot is not None:
        write_json(JOURNAL, data)  # a supervised test repair runs once
    done = []
    for slot in worn_out(gear, test_slot):
        loop.record("gear_repair_started", slot=slot, activity=f"Repairing the worn {slot}")
        loop.town("close", window="Shop")
        arrows = None
        if slot == "bow" and gear.get("equipment", {}).get("arrows"):
            arrows = loop.town("unequip", slot="arrows")
            _note("arrows", uid=arrows["uid"], type_id=arrows["type_id"], state="off")
        receipt = None
        try:
            piece = loop.town("unequip", slot=slot)
            _note(slot, uid=piece["uid"], state="off", before=piece.get("durability"))
            try:
                loop.town("open", vendor_type=vendor)
                receipt = loop.town("repair-item", uid=piece["uid"])
                _note(slot, state="repaired", after=receipt.get("durability"), cost=receipt.get("cost"))
            finally:
                loop.town("close", window="Shop")
                loop.town("equip", uid=piece["uid"])
                _note(slot, state="worn")
        finally:
            if arrows is not None:
                rewear_arrows(loop, arrows)
                _note("arrows", state="worn")
        loop.record(
            "gear_repaired",
            slot=slot,
            receipt=receipt,
            activity=f"Repaired the {slot} ({piece.get('durability')} -> {receipt.get('durability')}) "
            f"for {receipt.get('cost')} silver",
        )
        done.append(slot)
    return done