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

from conquest.character_context import state_path
from conquest.merchants.memory import GuiReader

STATUS_COLUMN = 0
# Long enough for an outside screenshot of the open window.
PROBE_HOLD_SECONDS = 3.0
PROBE = Path(state_path(".runtime/gear-window-probe.json"))


def windows(trade):
    return {w["name"]: w for w in GuiReader.for_session(trade.observer.adapter).windows()}


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

    from conquest.discord_notify import read_json
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
