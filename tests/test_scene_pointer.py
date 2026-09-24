import struct
from types import SimpleNamespace as NS

import pytest

from conquest.capture import CaptureUnavailable
from conquest.memory_life import CLIENT_SHA256
from conquest.scene_pointer import wait_scene_pointer


@pytest.mark.parametrize("scenario", ["settles", "timeout", "manual_stop", "bad_code"])
def test_waits_for_game_pointer_without_repeating_input(scenario):
    elapsed = [0.0]
    checks = []
    reads = []
    base = 0x140000000

    def read(address, size):
        if address == base + 0x19FCB1:
            return bytes(6) if scenario == "bad_code" else bytes.fromhex("8b2df5884f00")
        if address == base + 0x19FCBC:
            return bytes.fromhex("8b35e6884f00")
        assert address == base + 0x6985A8 and size == 8
        reads.append(elapsed[0])
        return struct.pack(
            "<2i", 700, 400 if scenario == "settles" and elapsed[0] >= 0.03 else 350
        )

    def check():
        checks.append(elapsed[0])
        if scenario == "manual_stop" and elapsed[0] >= 0.02:
            raise CaptureUnavailable("Manual Stop")

    def sleep(seconds):
        elapsed[0] += seconds

    session = NS(
        expected_sha256=CLIENT_SHA256,
        modules=[{"name": "ImConquer.exe", "base": base, "size": 0x700000}],
        read_block=read,
        assert_identity=lambda: None,
    )
    kwargs = dict(clock=lambda: elapsed[0], sleep=sleep, timeout=0.08)
    if scenario == "settles":
        wait_scene_pointer(session, (700, 400), check, **kwargs)
        assert 0.03 <= elapsed[0] < 0.08 and len(reads) >= 4
    else:
        error = ValueError if scenario == "bad_code" else CaptureUnavailable
        with pytest.raises(error):
            wait_scene_pointer(session, (700, 400), check, **kwargs)
        assert elapsed[0] <= 0.09
        if scenario == "bad_code":
            assert not reads
    assert len(checks) == len(reads) or scenario == "manual_stop"


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
