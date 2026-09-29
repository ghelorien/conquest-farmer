"""Route clicks pass through the live chat's message area, nothing else.

Alex 2026-09-27: "The chat box is clickable you can press through it";
2026-09-29: "Force click there you need to be able to do it on your own".
This client draws the chat at the top left, where clear_scene reserves a
status panel. At Phoenix's west gate the clamped camera draws portal 0 at
(160,96) and the (8,376) approach at (256,144), both inside the messages, and
every route to Twin City looped there (Suicide 2026-09-29 08:40-08:53).

Failure modes, written before the change:
1. A route click in the chat's message area is still refused.
2. A route click on the chat's tabs, header or input row (Send, Clear, Chat)
   is allowed.
3. A route click under another window over the chat (StatusTips) is allowed.
4. Combat, loot or other targeting starts clicking through the chat.
5. Without a live chat window nothing changes.
"""

import numpy as np
import pytest

from conquest.navigation import TerrainMap
from conquest.target_actionability import target_actionability
from conquest.viewport import ChatPassThrough, clear_scene

SIZE = (1416, 876)
# Suicide's live windows, read 2026-09-29 09:25 (gui_windows.py).
SUICIDE = [
    {"name": "Chat##Message", "geometry": (4.0, 3.0, 511.0, 181.0)},
    {"name": "Chat##Message/##ScrollingRegion_BD4470D8", "geometry": (12.0, 51.0, 495.0, 96.0)},
    {"name": "##ServerStatus", "geometry": (2.0, 2.0, 416.0, 32.0)},
    {"name": "##ControlRight", "geometry": (1173.0, 774.0, 32.0, 102.0)},
    {"name": "StatusTips", "geometry": (30.0, 80.0, 32.0, 32.0)},
    {"name": "##MiscButtons", "geometry": (968.0, 0.0, 278.0, 107.0)},
    {"name": "##SystemMessages", "geometry": (2.0, 16.0, 32.0, 32.0)},
    {"name": "##ManagementMessages", "geometry": (100.0, 365.0, 32.0, 32.0)},
    {"name": "##Minimap", "geometry": (1246.0, 0.0, 170.0, 128.0)},
    {"name": "##Control", "geometry": (243.0, 774.0, 930.0, 102.0)},
]


def test_the_phoenix_gate_points_pass_through_the_chat_messages():
    # 1
    chat = ChatPassThrough.from_windows(SUICIDE)
    for point in ((160, 96), (256, 144)):
        assert not clear_scene(point, SIZE, chat_blocks=False)
        assert chat.passes(point)
        assert target_actionability(point, SIZE, SIZE, route_chat=chat)["actionable"]


@pytest.mark.parametrize(
    "point",
    [
        (60, 38),  # the All/World/Trade tab row
        (200, 12),  # the server status header
        (394, 162),  # Send
        (441, 162),  # Clear
        (489, 162),  # Chat
        (190, 162),  # the Talk dropdown
        (560, 100),  # past the chat's right edge
    ],
)
def test_chat_controls_and_outside_points_are_still_refused(point):
    # 2
    chat = ChatPassThrough.from_windows(SUICIDE)
    assert not chat.passes(point)
    assert not target_actionability(point, SIZE, SIZE, route_chat=chat)["actionable"]


def test_another_window_over_the_messages_still_blocks():
    # 3: StatusTips (30,80,32,32) lies over the message area.
    chat = ChatPassThrough.from_windows(SUICIDE)
    assert not chat.passes((40, 90))


def test_other_targeting_never_clicks_through_the_chat():
    # 4: without route_chat the top left stays reserved.
    result = target_actionability((160, 96), SIZE, SIZE)
    assert not result["actionable"] and result["reason"] == "scene_control"


def test_no_live_chat_window_changes_nothing():
    # 5
    assert ChatPassThrough.from_windows([w for w in SUICIDE if "Chat" not in w["name"]]) is None


def test_toxic_chat_geometry_is_read_live():
    # Toxic's chat is wider and lower: (-3,21,714,213), messages (5,69,698,128).
    chat = ChatPassThrough.from_windows(
        [
            {"name": "Chat##Message", "geometry": (-3.0, 21.0, 714.0, 213.0)},
            {"name": "Chat##Message/##ScrollingRegion_1", "geometry": (5.0, 69.0, 698.0, 128.0)},
        ]
    )
    assert chat.passes((600, 150)) and not chat.passes((600, 60))


def test_route_movement_ignores_the_stale_bottom_left_chat_box():
    # Portal 9's jump at Twin City's clamped south edge fell in clear_scene's
    # bottom-left chat box, which this client does not draw (2026-09-29).
    point = (400, 700)
    assert not target_actionability(point, SIZE, SIZE)["actionable"]
    assert target_actionability(point, SIZE, SIZE, chat_blocks=False)["actionable"]
    # The XP popup row and the control bar still block route clicks.
    assert not target_actionability((708, 720), SIZE, SIZE, chat_blocks=False)["actionable"]
    assert not target_actionability((400, 800), SIZE, SIZE, chat_blocks=False)["actionable"]


@pytest.mark.parametrize("chat_present", [True, False])
def test_travel_steps_onto_the_gate_approach_through_the_chat(monkeypatch, chat_present):
    # The controller's planner: at the clamped Phoenix gate (anchor (288,192))
    # the approach two tiles west and one north projects to (256,144) in the
    # chat messages. Refusing it shortened every step and oscillated between
    # (9,377) and (10,377).
    from types import SimpleNamespace

    from conquest import scene_input, viewport
    from conquest.overnight import OvernightLoop
    from conquest.routes import RouteLibrary

    loop = OvernightLoop.__new__(OvernightLoop)
    loop.route = RouteLibrary().load("turtledove")
    loop.terrain = TerrainMap(1011, 30, 30, np.zeros((30, 30), dtype=bool), "", (), ())
    position = [10, 10]
    monkeypatch.setattr(scene_input, "memory_player_anchor", lambda *a: (288, 192))
    monkeypatch.setattr(
        viewport,
        "read_route_chat",
        lambda session: ChatPassThrough.from_windows(SUICIDE) if chat_present else None,
    )
    loop.living = lambda: {"embedded_controls": {"life": {"position": list(position)}}}
    loop.care = SimpleNamespace(check=lambda h: None, session=None)
    loop.record = lambda *a, **k: None
    steps = []

    def step(destination, expected_position):
        steps.append(tuple(destination))
        position[:] = destination
        return {"reached": True}

    loop.stepper = SimpleNamespace(step_to=step)
    loop.travel((8, 9))
    assert position == [8, 9]
    assert (steps[0] == (8, 9)) is chat_present


def test_route_run_into_the_chat_messages_is_sent(monkeypatch, tmp_path):
    # The route input itself: a run whose tile projects into the messages is
    # clicked; one that projects onto the input row is refused.
    from contextlib import nullcontext
    from types import SimpleNamespace

    from conquest import desktop_runtime, foreground, route_recovery, scene_input
    from conquest.route_recovery import EmbeddedRecoveryInput

    life = SimpleNamespace(
        position=(10, 10), map_id=1011, ghost_candidate=False,
        current_hp=100, max_hp=100, status=512,
    )
    monkeypatch.setattr(desktop_runtime, "physical_coordinates", nullcontext)
    anchor = [(288, 192)]  # Suicide at Phoenix's west gate, camera clamped
    monkeypatch.setattr(scene_input, "memory_player_anchor", lambda *args: anchor[0])
    monkeypatch.setattr(
        route_recovery, "route_chat", lambda observer: ChatPassThrough.from_windows(SUICIDE)
    )
    clicks = []

    def click(*args, **kwargs):
        kwargs["layout_guard"]()
        kwargs["before_press"]()
        clicks.append(args[1:3])

    monkeypatch.setattr(foreground, "foreground_click", click)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "reports").mkdir()
    target = SimpleNamespace(
        snapshot=lambda: dict(client_size=list(SIZE), foreground=1, root_hwnd=1, minimized=False)
    )
    revision = SimpleNamespace(client_size=SIZE, gui_size=SIZE, client_origin=(0, 0), panels=())
    layout = SimpleNamespace(
        target=target, qualified=lambda: revision, assert_current=lambda expected: expected
    )
    observer = SimpleNamespace(
        adapter=SimpleNamespace(viewport_size=lambda: SIZE),
        health_layout=None,
        character="Suicide",
        bridge=SimpleNamespace(operations=SimpleNamespace(target=target)),
        focus_client=lambda: None,
        read_life=lambda: life,
    )
    terrain = TerrainMap(1011, 40, 40, np.zeros((40, 40), dtype=bool), "", (), ())
    sender = EmbeddedRecoveryInput(observer, None, terrain=terrain, layout=layout)
    # Two tiles west, one north: (-32, -48) from the anchor = (256, 144).
    sender.send("run", (8, 9), vars(life))
    assert clicks == [(256, 144)]
    anchor[0] = (288, 208)  # the same step now lands on the input row (256, 160)
    with pytest.raises(ValueError, match="outside the clear scene"):
        sender.send("run", (8, 9), vars(life))
    assert clicks == [(256, 144)]
