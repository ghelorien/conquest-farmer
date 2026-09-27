"""Decline another player's team invitation on the Farmer.

A stranger's invitation (live 2026-09-27 18:35, "sir.KAMOTE¡¡ invites you to
join their team.") opens the shared 1078 ###Confirm prompt. The Open Booth
cleanup refuses every other prompt, so each farm start and town action failed
and Suicide stood in Twin City for ten minutes. Only this exact prompt is
answered, only with its No button, and only once memory shows No hovered.
"""

import time

from conquest.merchants import open_booth_control_1078 as control
from conquest.merchants.memory import string

TITLE = "Join Team###Confirm"
QUESTION = " invites you to join their team.\n\nDo you accept?"


def read(gui):
    """The visible team invitation as a stable snapshot, else None."""
    session = gui.session
    model = control._model(gui)
    if session.read_block(model + 12, 1) != b"\x01":
        return None
    values = tuple(
        string(session, model + offset, 256) for offset in (0x48, 0x68, 0x88, 0xA8)
    )
    if (
        values[0] != TITLE
        or not values[1].endswith(QUESTION)
        or values[2:] != ("Yes", control.LABEL)
    ):
        return None
    callback = session.read_block(model + 0x100, 8)
    session.assert_identity()
    return {
        "model_address": model,
        "title": values[0],
        "message": values[1],
        "positive_label": values[2],
        "negative_label": values[3],
        "callback": callback.hex(),
    }


def decline(trade, check=lambda: None):
    """Press No on a visible team invitation; True once memory shows it closed."""
    from conquest.desktop_runtime import physical_coordinates
    from conquest.merchants.coordination import input_scope
    from conquest.merchants.driver import wait_hover_validation
    from conquest.merchants.memory import GuiReader
    from conquest.merchants.reader_1078 import CLIENT_SHA256_1078

    if getattr(trade.observer.adapter, "expected_sha256", None) != CLIENT_SHA256_1078:
        return False  # the prompt layout is proved for the 1078 client only
    gui = GuiReader.for_session(trade.observer.adapter)
    prompt = read(gui)
    if prompt is None:
        return False
    with input_scope(), physical_coordinates():
        check()
        layout, revision = trade.warehouse_layout()
        window, logical = control.locate(gui, prompt, reader=read)
        point = trade.warehouse_native_point(logical, revision)

        def guard():
            check()
            layout.assert_current(revision)
            if control.locate(gui, prompt, reader=read) != (window, logical):
                raise ValueError("Team invitation changed before declining")
            gui.assert_hovered(window, control.LABEL)

        trade.input_attempted = True
        trade.click(point, before_press=lambda: wait_hover_validation(guard, check))
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if read(gui) is None:
                return True
            time.sleep(0.05)
    raise ValueError("Team invitation decline was not verified")
