"""Bounded diagnostic drag within one inventory cell; no production routing.

The caller owns the isolated surface, input lease, fingerprint/code/handler
qualification, and final queue/stock reconciliation. This module never focuses,
scrolls, targets the booth, or confirms a transaction.
"""

from copy import deepcopy
import time

from conquest.merchants.qualification import grid_control, stock, window


GRID = "Inventory/##ItemGrid_A800F95C"


def _geometry(driver, snapshot, item, client_size, viewport):
    control, table = grid_control(
        driver.memory.gui, snapshot, GRID, "##ItemTable", (20, 20)
    )
    win = window(snapshot, GRID)
    if control["stride"] != [40.0, 40.0]:
        raise ValueError("Drag diagnostic requires the proven 40-pixel inventory cells")
    slot = item["slot"]
    if type(slot) is not int or not 0 <= slot < 40:
        raise ValueError("Invalid drag source slot")
    columns = table["columns"]
    x = columns[slot % len(columns)]["content_x"]
    y = table["outer"][1] + slot // len(columns) * table["row_height"]
    # Center and a 14 logical-pixel displacement both remain inside this cell.
    logical = ((x + 20, y + 20), (x + 34, y + 20))
    left, top, right, bottom = table["clip"]
    wx, wy, width, height = win["geometry"]
    for px, py in logical:
        if (
            not left + 2 < px < right - 2
            or not top + 2 < py < bottom - 2
            or not wx + 2 < px < wx + width - 2
            or not wy + 2 < py < wy + height - 2
        ):
            raise ValueError("Drag source cell is clipped")
    points = [
        tuple(round(v * n / g) for v, n, g in zip(p, client_size, viewport))
        for p in logical
    ]
    if any(
        not 0 <= p < min(n, 32768)
        for point in points
        for p, n in zip(point, client_size)
    ):
        raise ValueError("Invalid background drag coordinates")
    return {
        "window": win["address"],
        "geometry": list(win["geometry"]),
        "scroll": list(win["scroll"]),
        "table": table,
        "points": points,
    }


def run_drag_cancel(driver, before, reader, report, sample, check):
    """Attempt one reversible drag, recording acknowledgements or raising.

    sample(stage) must append its fresh GUI observation to report['samples'].
    The caller must always perform its normal queue drain and stock reconciliation.
    """
    started = time.monotonic()
    deadline = started + 6.5
    target, session = driver.target, reader.session
    identity = deepcopy(session.identity)
    client_size = tuple(target.snapshot()["client_size"])
    viewport = driver.memory.gui.viewport_size()
    original_stock = stock(before)
    if (
        before.get("trade")
        or before.get("request")
        or any(w["name"] == "Add Item to Booth" for w in before["windows"])
    ):
        raise ValueError("Drag diagnostic requires no trade or price dialog")
    item = geometry = None
    for candidate in before["inventory"]:
        if candidate.get("bound") is not False:
            continue
        try:
            found = _geometry(driver, before, candidate, client_size, viewport)
        except ValueError:
            continue
        item, geometry = deepcopy(candidate), found
        break
    if item is None:
        raise ValueError(
            "No visible verified unbound inventory item for drag diagnostic"
        )
    origin, displaced = geometry["points"]
    report["drag_source"] = {
        "uid": item["uid"],
        "slot": item["slot"],
        "window": geometry["window"],
        "origin": origin,
        "displaced": displaced,
    }
    attempted = False

    def guarded():
        if time.monotonic() >= deadline:
            raise ValueError("Background drag diagnostic exceeded its time bound")
        check()
        session.assert_identity()
        if session.identity != identity:
            raise ValueError("Drag process identity changed")

    def fresh():
        guarded()
        current = driver.read()
        matches = [row for row in current["inventory"] if row["slot"] == item["slot"]]
        if (
            len(matches) != 1
            or matches[0] != item
            or stock(current) != original_stock
            or current["position"] != before["position"]
            or current.get("trade")
            or current.get("request")
            or any(w["name"] == "Add Item to Booth" for w in current["windows"])
            or tuple(target.snapshot()["client_size"]) != client_size
            or driver.memory.gui.viewport_size() != viewport
            or _geometry(driver, current, item, client_size, viewport) != geometry
        ):
            raise ValueError("Drag source, stock or window layout changed")

    def burst(point, flags, button=None):
        guarded()
        report["input_messages_sent"] = True
        packed = point[0] | point[1] << 16
        target.post(0x200, flags, packed)
        target.post(0x102, 1, 1)
        if button is not None:
            target.post(button, flags, packed)
        for index in range(12):
            guarded()
            # Both adjacent points remain inside the verified source cell.
            target.post(0x200, flags, point[0] + index % 2 | point[1] << 16)
            target.post(0x102, 1, 1)

    def wait(stage, predicate, timeout=1):
        until = min(deadline, time.monotonic() + timeout)
        while time.monotonic() < until:
            guarded()
            sample(stage)
            state = report["samples"][-1]["gui"]
            if predicate(state):
                return state
            time.sleep(0.005)
        raise ValueError(f"Background drag phase was not acknowledged: {stage}")

    fresh()
    state = reader.snapshot()
    if (
        state["active"]["id"]
        or state["drag"]["active"]
        or state["queue"]["size"]
        or state["backend"]["buttons_down"]
        or any(state["mouse_down"])
        or any(state["modifiers"].values())
        or state.get("key_mods", 0)
    ):
        raise ValueError("Drag diagnostic requires neutral input state")
    burst(origin, 0)
    hovered = wait(
        "drag-prime",
        lambda s: (
            s["hover"]["window"] == geometry["window"]
            and s["hover"]["id"]
            and s["want_capture_mouse"]
            and not s["active"]["id"]
        ),
    )
    expected_id = hovered["hover"]["id"]
    report["drag_source"]["control_id"] = expected_id
    fresh()
    state = reader.snapshot()
    if (
        state["hover"] != {"window": geometry["window"], "id": expected_id}
        or not state["want_capture_mouse"]
        or state["active"]["id"]
    ):
        raise ValueError("Drag source hover expired before press")
    try:
        attempted = True
        report["button_messages_sent"] = True
        burst(origin, 1, 0x201)
        wait(
            "drag-press",
            lambda s: s["active"]["id"] == expected_id and s["mouse_down"][0],
        )
        report["drag_press_verified"] = True
        fresh()
        burst(displaced, 1)
        state = wait("drag-held", lambda s: s["drag"]["active"])
        if (
            state["drag"]["payload_type"] != "CQITEM"
            or state["drag"]["source_item_uid"] != item["uid"]
            or state["drag"]["source_id"] != expected_id
            or not state["mouse_down"][0]
            or state["drag"]["delivery"]
        ):
            raise ValueError(
                "Drag payload did not match the exact source inventory item"
            )
        report["drag_payload_verified"] = True
        report["drag_payload"] = deepcopy(state["drag"])
        fresh()
        burst(origin, 1)

        def returned(s):
            position = s["mouse_position"]
            logical_origin = [
                v * g / n for v, g, n in zip(origin, viewport, client_size)
            ]
            return (
                s["drag"]["active"]
                and s["drag"]["source_item_uid"] == item["uid"]
                and not s["drag"]["delivery"]
                and s["mouse_down"][0]
                and all(abs(a - b) <= 2 for a, b in zip(position, logical_origin))
            )

        wait("drag-return", returned)
        report["drag_return_verified"] = True
        fresh()
        burst(origin, 0, 0x202)
        attempted = False
        wait(
            "drag-release",
            lambda s: (
                not s["drag"]["active"]
                and not s["active"]["id"]
                and not s["backend"]["buttons_down"]
                and not any(s["mouse_down"])
            ),
            timeout=1.2,
        )
        report["drag_cancel_verified"] = True
        report["release_verified"] = True
        fresh()
        return report
    finally:
        if attempted:
            # Cancel/Stop must not leave a synthetic held button. Do not invoke
            # the ordinary guard here: it may be the reason cleanup is needed.
            session.assert_identity()
            if session.identity != identity:
                raise ValueError("Drag release refused after process identity changed")
            target.post(0x202, 0, origin[0] | origin[1] << 16)
            report["drag_emergency_release_sent"] = True
        report["drag_elapsed_seconds"] = round(time.monotonic() - started, 4)
