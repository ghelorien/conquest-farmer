"""Mapping the Status window for gear repair (Alex 2026-10-02 04:4x: "Keep an
eye on gear durability and go automatically repair before it gets
destroyed"; his way: unequip into the bag, the shop's Repair, click the item,
wear it again)."""

import json

import pytest
from types import SimpleNamespace as NS

from conquest import discard_loot, gear_repair


def verified(read, accept, failure, timeout=2):
    result = read()
    if not accept(result):
        raise ValueError(failure)
    return result


def status_trade(monkeypatch, toggles=True):
    shown = {"open": False}
    clicks = []

    def windows(trade):
        names = {"##Control": {"geometry": (243, 774, 930, 102), "scroll": (0, 0)}}
        if shown["open"]:
            names["Status"] = {"geometry": (100, 120, 400, 520), "scroll": (0, 0)}
            names["Status/##Gear_A1"] = {"geometry": (110, 150, 380, 300), "scroll": (0, 0)}
        return names

    def click(point, button="left", **kwargs):
        clicks.append((tuple(point), button))
        if toggles or not shown["open"]:
            shown["open"] = not shown["open"]

    monkeypatch.setattr(gear_repair, "windows", windows)
    monkeypatch.setattr(discard_loot, "control_button", lambda gui, column: (766, 836) if column == 0 else None)
    monkeypatch.setattr(gear_repair.time, "sleep", lambda seconds: None)
    trade = NS(life=lambda: None, shop=NS(gui=None), click=click, verified_read=verified, input_attempted=False)
    return trade, clicks, shown


def test_the_probe_opens_the_status_window_reports_it_and_closes_it(monkeypatch):
    trade, clicks, shown = status_trade(monkeypatch)
    result = gear_repair.probe(trade)
    assert clicks == [((766, 836), "left"), ((766, 836), "left")]
    assert set(result["opened"]) == {"Status", "Status/##Gear_A1"}
    assert result["opened"]["Status"]["geometry"] == [100, 120, 400, 520]
    assert result["closed_by"] == "button" and not shown["open"]


def test_a_status_window_the_button_leaves_open_is_closed_by_its_close_button(monkeypatch):
    trade, clicks, shown = status_trade(monkeypatch, toggles=False)
    closed = []

    def click_close(trade, name, display_only=False):
        closed.append((name, display_only))
        shown["open"] = False

    monkeypatch.setattr("conquest.panel_close.click_close", click_close)
    result = gear_repair.probe(trade)
    assert closed == [("Status", True)] and result["closed_by"] == "close"


def test_a_probe_runs_once_and_only_in_town(monkeypatch):
    events, actions = [], []
    town = {"inside": False}
    monkeypatch.setattr("conquest.return_scroll.in_town", lambda life, map_id: town["inside"])
    loop = NS(
        route=NS(restock_map_id=1002),
        living=lambda: {"embedded_controls": {"life": {"map_id": 1002, "position": [430, 380]}}},
        town=lambda action: actions.append(action) or {"opened": {"Status": {}}},
        record=lambda event, **fields: events.append(event),
    )
    assert gear_repair.probe_if_asked(loop) is None and actions == []  # not asked
    gear_repair.PROBE.write_text(json.dumps({"pending": True}))
    assert gear_repair.probe_if_asked(loop) is None and gear_repair.PROBE.exists()  # in the field
    town["inside"] = True
    assert gear_repair.probe_if_asked(loop) == {"opened": {"Status": {}}}
    assert actions == ["gear-window"] and not gear_repair.PROBE.exists()
    assert events == ["gear_window_probe_opening", "gear_window_probe"]
    assert gear_repair.probe_if_asked(loop) is None and actions == ["gear-window"]


def gear(**durability):
    rows = {"head": 4204, "necklace": 3999, "ring": 4099, "bow": 5099, "armor": 3998, "boots": 4098}
    return {"equipment": {
        slot: {"uid": 100 + i, "type_id": 1, "durability": durability.get(slot, top), "max_durability": top}
        for i, (slot, top) in enumerate(rows.items())
    } | {"arrows": {"uid": 99, "durability": 300, "max_durability": 1000}}}


def test_slots_follow_the_mapped_status_column():
    status = {"geometry": (70.0, 109.0, 506.0, 376.0)}
    assert gear_repair.slot_point(status, "head") == (278, 192)
    assert gear_repair.slot_point(status, "bow") == (278, 323)
    assert gear_repair.slot_point(status, "boots") == (278, 455)


def test_only_pieces_under_the_repair_line_are_repaired_weakest_first():
    assert gear_repair.worn_out(gear()) == []
    worn = gear(necklace=2300, bow=1500, ring=2500)  # 58%, 29%, 61%
    assert gear_repair.worn_out(worn) == ["bow", "necklace"]
    assert gear_repair.worn_out(gear(necklace=3412), test_slot="necklace") == ["necklace"]


def repair_loop(fail=None):
    actions, events = [], []
    bag = {"items": []}

    def town(action, **fields):
        actions.append((action, fields.get("slot") or fields.get("uid") or fields.get("window") or fields.get("vendor_type")))
        if action == "unequip":
            bag["items"].append({"uid": 101})
            return {"uid": 101, "type_id": 1, "slot": fields["slot"], "durability": 2300, "max_durability": 3999}
        if action == "repair-item":
            if fail:
                raise ValueError(fail)
            return {"uid": 101, "durability": 3999, "max_durability": 3999, "cost": 210}
        if action == "equip":
            bag["items"] = [i for i in bag["items"] if i["uid"] != fields["uid"]]
            return {"equipped": fields["uid"]}
        if action == "supplies":
            return {"items": list(bag["items"])}
        return {}

    loop = NS(town=town, record=lambda event, **fields: events.append(event))
    return loop, actions, events, bag


def test_a_worn_piece_comes_off_is_repaired_and_is_worn_again():
    loop, actions, events, bag = repair_loop()
    assert gear_repair.repair_worn(loop, gear=gear(necklace=2300)) == ["necklace"]
    assert actions == [
        ("close", "Shop"), ("unequip", "necklace"), ("open", 5), ("repair-item", 101),
        ("close", "Shop"), ("equip", 101),
    ]
    assert events == ["gear_repair_started", "gear_repaired"]
    assert gear_repair.journal()["pieces"]["necklace"]["state"] == "worn"
    assert gear_repair.off_body_uids() == frozenset()
    assert gear_repair.repair_worn(loop) == []  # no gear read: nothing to do


def test_a_failed_repair_still_puts_the_piece_back_on():
    loop, actions, events, bag = repair_loop(fail="Repair unverified; no repeat input issued")
    with pytest.raises(ValueError, match="Repair unverified"):
        gear_repair.repair_worn(loop, gear=gear(necklace=2300))
    assert actions[-2:] == [("close", "Shop"), ("equip", 101)] and bag["items"] == []
    assert gear_repair.journal()["pieces"]["necklace"]["state"] == "worn"


def test_a_piece_left_off_is_worn_first_and_never_banked_or_sold(monkeypatch):
    from conquest import town_trade, valuables

    gear_repair._note("ring", uid=777, state="off")
    assert gear_repair.off_body_uids() == {777}
    ring = {"uid": 777, "type_id": 150118, "slot": 2, "plus": 0}
    assert not valuables.urgent_storage(ring) and not town_trade.sale_candidate(ring)
    loop, actions, events, bag = repair_loop()
    bag["items"].append({"uid": 777})
    gear_repair.repair_worn(loop, gear=gear())
    assert actions == [("supplies", None), ("close", "Shop"), ("equip", 777)]
    assert gear_repair.off_body_uids() == frozenset()


def test_a_supervised_test_repair_runs_once():
    write = gear_repair.write_json
    write(gear_repair.JOURNAL, {"test_slot": "necklace"})
    loop, actions, events, bag = repair_loop()
    assert gear_repair.repair_worn(loop, gear=gear(necklace=3412)) == ["necklace"]
    assert "test_slot" not in gear_repair.journal()
    assert gear_repair.repair_worn(loop, gear=gear(necklace=3412)) == []


def test_repair_presses_repair_then_the_piece_and_reads_the_receipt(monkeypatch):
    from collections import namedtuple

    from conquest.memory_inventory import InventorySnapshot, Item

    Window = namedtuple("Window", "address title position size scroll")
    reads = {
        "Shop": Window(1, "Shop", (300.0, 263.0), (288.0, 438.0), (0.0, 0.0)),
        "Shop/##ShopGrid_": Window(2, "g", (320.0, 301.0), (248.0, 370.0), (0.0, 0.0)),
        "Inventory": Window(3, "Inventory", (627.0, 377.0), (447.0, 287.0), (0.0, 0.0)),
        "Inventory/##ItemGrid_": Window(4, "i", (647.0, 415.0), (407.0, 175.0), (0.0, 0.0)),
    }
    state = {"durability": 2300, "silver": 1000}

    def snapshot():
        piece = Item(101, 120095, state["durability"], 3999, 12)
        return InventorySnapshot(0, 0, (piece,), None, state["silver"], 40)

    hovered, clicks = [], []

    def click(point, button="left", before_press=None, **kwargs):
        if before_press:
            before_press()
        clicks.append((tuple(point), button))
        if len(clicks) == 2:  # the piece, in repair mode
            state.update(durability=3999, silver=790)

    monkeypatch.setattr(gear_repair, "windows", lambda trade: {"Shop": {"address": 1}})
    monkeypatch.setattr(
        gear_repair.GuiReader, "for_session",
        classmethod(lambda cls, session: NS(assert_hovered=lambda window, label: hovered.append(label))),
    )
    monkeypatch.setattr(gear_repair, "confirm_prompt", lambda gui: None)
    monkeypatch.setattr(gear_repair.time, "sleep", lambda seconds: None)
    trade = NS(
        life=lambda: None, inventory=NS(read=snapshot), shop=NS(gui=NS(read=reads.__getitem__)),
        observer=NS(adapter=None), click=click, input_attempted=False,
    )
    receipt = gear_repair.repair_item(trade, 101)
    assert clicks == [((444, 686), "left"), ((747, 475), "left")]
    assert hovered == ["Repair"]
    assert receipt == {"uid": 101, "durability": 3999, "max_durability": 3999, "cost": 210, "confirmed": None}

def unequip_trade(monkeypatch, *, lands=True, slot="necklace", arrows_worn=False):
    from collections import namedtuple

    from conquest import equipment
    from conquest.memory_inventory import InventorySnapshot, Item

    Window = namedtuple("Window", "address title position size scroll")
    state = {"worn": True, "status": True, "clicks": [], "closed": 0}
    pieces = {
        "necklace": {"uid": 55, "type_id": 120095, "durability": 3412, "max_durability": 3999},
        "bow": {"uid": 66, "type_id": 500107, "durability": 2142, "max_durability": 5099},
        "arrows": {"uid": 77, "type_id": 1050001, "durability": 600, "max_durability": 1000},
    }
    item = pieces[slot]

    def gear(observer):
        worn = {slot: item} if state["worn"] else {}
        if arrows_worn and slot != "arrows":
            worn["arrows"] = pieces["arrows"]
        return {"equipment": worn}

    def bag():
        items = [Item(1, 1000030, 12, 12, 0), Item(2, 1050001, 1000, 1000, 1)]
        if not state["worn"]:
            items.append(Item(item["uid"], item["type_id"], item["durability"], item["max_durability"], 2))
        return InventorySnapshot(0, 0, tuple(items), None, 1000, 40)

    def windows(trade):
        names = {"Inventory": {"address": 7, "geometry": (627.0, 377.0, 447.0, 287.0)}}
        if state["status"]:
            names["Status"] = {"address": 9, "geometry": (70.0, 109.0, 506.0, 376.0)}
        return names

    def click(point, button="left", before_press=None, double=False, **kwargs):
        before_press()  # every check passes before the button goes down
        state["clicks"].append((tuple(point), button, double))
        if lands:
            state["worn"] = False

    def close_status(trade):
        state["closed"] += 1
        state["status"] = False

    monkeypatch.setattr(equipment, "read_equipment", gear)
    monkeypatch.setattr(gear_repair, "windows", windows)
    monkeypatch.setattr(gear_repair, "open_status", lambda trade: windows(trade)["Status"])
    monkeypatch.setattr(gear_repair, "close_status", close_status)
    monkeypatch.setattr(gear_repair, "hovered_window", lambda gui: 9)
    monkeypatch.setattr(gear_repair.GuiReader, "for_session", classmethod(lambda cls, session: None))
    trade = NS(life=lambda: None, inventory=NS(read=bag), shop=NS(gui=None),
               observer=NS(adapter=None), click=click, verified_read=verified, input_attempted=False)
    return trade, state


def test_unequip_double_clicks_the_piece_on_its_status_slot(monkeypatch):
    trade, state = unequip_trade(monkeypatch)
    piece = gear_repair.unequip(trade, "necklace")
    # Alex 07:5x: "its double click on the item".
    assert state["clicks"] == [((278, 236), "left", True)]
    assert piece["uid"] == 55 and piece["durability"] == 3412
    assert state["closed"] == 1


def test_a_failed_unequip_still_closes_the_status_window(monkeypatch):
    trade, state = unequip_trade(monkeypatch, lands=False)
    with pytest.raises(ValueError, match="Unequip unverified"):
        gear_repair.unequip(trade, "necklace")
    assert state["closed"] == 1 and state["worn"]


def test_the_bow_comes_off_only_after_its_arrows(monkeypatch):
    trade, state = unequip_trade(monkeypatch, slot="bow", arrows_worn=True)
    with pytest.raises(ValueError, match="arrows off before the bow"):
        gear_repair.unequip(trade, "bow")
    assert state["clicks"] == [] and state["closed"] == 0
    trade, state = unequip_trade(monkeypatch, slot="arrows")
    assert gear_repair.unequip(trade, "arrows")["uid"] == 77
    assert state["clicks"] == [((278, 367), "left", True)]  # the arrows slot, fifth


def bow_loop():
    actions = []
    bag = {"items": []}

    def town(action, **fields):
        actions.append((action, fields.get("slot") or fields.get("uid") or fields.get("window") or fields.get("vendor_type")))
        if action == "unequip":
            uid = {"arrows": 77, "bow": 66}[fields["slot"]]
            bag["items"].append({"uid": uid, "type_id": {77: 1050001, 66: 500107}[uid]})
            return {"uid": uid, "type_id": {77: 1050001, 66: 500107}[uid], "slot": fields["slot"],
                    "durability": 2142, "max_durability": 5099}
        if action == "repair-item":
            return {"uid": 66, "durability": 5099, "max_durability": 5099, "cost": 400}
        if action in ("equip", "equip-arrows"):
            bag["items"] = [i for i in bag["items"] if i["uid"] != fields["uid"]]
            return {"equipped": fields["uid"]}
        if action == "supplies":
            return {"items": list(bag["items"])}
        return {}

    return NS(town=town, record=lambda event, **fields: None), actions, bag


def test_a_worn_bow_is_repaired_with_its_arrows_off_and_both_go_back_on():
    loop, actions, bag = bow_loop()
    worn = gear(bow=2142)
    assert gear_repair.repair_worn(loop, gear=worn) == ["bow"]
    assert actions == [
        ("close", "Shop"), ("unequip", "arrows"), ("unequip", "bow"), ("open", 5),
        ("repair-item", 66), ("close", "Shop"), ("equip", 66),
        ("supplies", None), ("close", "Shop"), ("equip-arrows", 77),
    ]
    assert bag["items"] == [] and gear_repair.off_body_uids() == frozenset()


def test_pieces_left_off_go_back_on_bow_before_arrows():
    gear_repair._note("arrows", uid=77, type_id=1050001, state="off")
    gear_repair._note("bow", uid=66, state="off")
    loop, actions, bag = bow_loop()
    bag["items"] = [{"uid": 66, "type_id": 500107}, {"uid": 77, "type_id": 1050001}]
    assert gear_repair.rewear_left_off(loop) == ["arrows", "bow"]
    assert [a for a in actions if a[0].startswith("equip")] == [("equip", 66), ("equip-arrows", 77)]
    assert gear_repair.rewear_left_off(loop) == []