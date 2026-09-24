"""Qualify reopening Inventory using the pinned renderer's Items control."""

import time
from conquest.merchants.qualification import stock


def verify_inventory_panel(driver, check):
    from conquest.discard_loot import inventory_button
    from conquest.memory_shop import MemoryGui
    from conquest.merchants.connect_market import geometry, record_capability
    from conquest.merchants.return_driver import ReturnDriver

    check()
    before = driver.read()
    if (
        before["map_id"] != 1036
        or not before.get("own_booth_uid")
        or not before["booth_open"]
        or before.get("trade")
        or before.get("request")
        or any(w["name"] == "Add Item to Booth" for w in before["windows"])
    ):
        raise ValueError("Inventory qualification needs an idle owned booth in Market")
    gui = MemoryGui(driver.observer.adapter)
    dimensions = geometry(driver)
    travel = ReturnDriver(driver)

    def unchanged():
        check()
        fresh = driver.read()
        if (
            stock(fresh) != stock(before)
            or not fresh["booth_open"]
            or fresh.get("trade")
            or fresh.get("request")
            or any(w["name"] == "Add Item to Booth" for w in fresh["windows"])
            or any(
                fresh[k] != before[k] for k in ("map_id", "position", "own_booth_uid")
            )
            or geometry(driver) != dimensions
        ):
            raise ValueError("Merchant changed during inventory panel qualification")
        return fresh

    def checked_geometry():
        unchanged()
        return dimensions

    travel.qualify_movement = checked_geometry
    visible = lambda state: any(w["name"] == "Inventory" for w in state["windows"])
    # Starting open proves nothing about recovery. Observe a real closed ->
    # open transition; never repeat an unverified toggle.
    transitions = [False, True] if visible(before) else [True]
    for expected in transitions:
        state = unchanged()
        point = inventory_button(gui)

        def guard():
            current = unchanged()
            if visible(current) != visible(state) or inventory_button(gui) != point:
                raise ValueError("Inventory toggle changed before input")

        travel.click(point, check, before_press=guard)
        deadline = time.monotonic() + 2
        while visible(unchanged()) != expected:
            if time.monotonic() >= deadline:
                raise ValueError(
                    "Inventory toggle result was not verified; no repeat input"
                )
            time.sleep(0.05)
    evidence = {
        "verified_at": time.time(),
        "identity": before["identity"],
        "closed_to_open_verified": True,
        "stock_unchanged": True,
        "own_booth_uid": before["own_booth_uid"],
        "source": "pinned Items table control",
    }
    record_capability(driver, "inventory_panel", evidence, dimensions=dimensions)
    return evidence
