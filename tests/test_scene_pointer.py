from types import SimpleNamespace as NS

import pytest

from conquest.capture import CaptureUnavailable


@pytest.mark.parametrize(
    "scenario",
    ["stable", "npc_moves_after_cursor", "pointer_not_observed", "torn_npc_read"],
)
def test_shop_click_rechecks_npc_after_cursor_move_before_mouse_down(
    monkeypatch, scenario
):
    from conquest import town_trade, scene_pointer

    trade = town_trade.TownTrade.__new__(town_trade.TownTrade)
    trade.observer = NS(
        adapter=NS(viewport_size=(1416, 907)), operations=NS(target=object())
    )
    trade.life = lambda *args, **kwargs: None
    state = {"cursor_moved": False, "clicked": False}
    presses = []

    def vendor(kind):
        if state["cursor_moved"] and scenario == "torn_npc_read":
            raise ValueError("NPC scene changed during observation")
        shifted = state["cursor_moved"] and scenario == "npc_moves_after_cursor"
        return NS(
            entity_id=101356,
            map_id=1011,
            type_id=10013,
            name="Blacksmith",
            position=(197, 226),
            draw_position=(701 if shifted else 700, 400),
        )

    trade.vendor = vendor

    def shop(uid):
        if not state["clicked"]:
            raise ValueError("Requested GUI window is not active")
        return NS(products=[])

    trade.shop = NS(read=shop)

    def click(target, x, y, size, **kwargs):
        state["cursor_moved"] = True
        kwargs["before_press"]()
        presses.append((x, y))
        state["clicked"] = True

    def pointer(session, point, check):
        check()
        if scenario == "pointer_not_observed":
            raise CaptureUnavailable(
                "Game has not confirmed the scene pointer; no button pressed"
            )

    monkeypatch.setattr(town_trade, "foreground_click", click)
    monkeypatch.setattr(town_trade.time, "sleep", lambda _: None)
    monkeypatch.setattr(scene_pointer, "wait_scene_pointer", pointer)
    if scenario == "stable":
        assert trade({"action": "open", "vendor_type": 5})["opened"]
        assert presses == [(700, 368)]
    else:
        with pytest.raises(town_trade.TownObservationUnavailable):
            trade({"action": "open", "vendor_type": 5})
        assert not presses
