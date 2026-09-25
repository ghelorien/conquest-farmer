"""Preserve zero-input provenance across the route worker's XP care boundary."""

from copy import deepcopy
from contextlib import nullcontext
import json
from pathlib import Path
import struct
import threading
import time
from types import SimpleNamespace as NS

import numpy as np
import pytest

from conquest import travel_care, worker, xp_skill
from conquest.capture import CaptureUnavailable
from conquest.embedded_bridge import EmbeddedBridge
from conquest.merchants import coordination
from conquest.merchants.coordination import InputAcquisitionBusy, InputCoordinator
from conquest.overnight import OvernightLoop, OvernightStopped
from conquest.travel_care import TravelCare, TravelStateChanged
from test_town_input_acquisition import held_by_peer, release_peer
from test_xp_skill import CHARGE as XP_CHARGE, STATUS as XP_STATUS
from test_xp_skill import fixture as xp_memory


@pytest.fixture
def rig(tmp_path, monkeypatch):
    observer, blobs, popup = xp_memory(monkeypatch)
    owner = InputCoordinator(path=tmp_path / "input.lock")
    monkeypatch.setattr(coordination, "_coordinator", owner)
    state = NS(
        control={"enabled": False, "revision": 7},
        life={
            "object_address": 0x100000,
            "status": 0x10,
            "dead_candidate": False,
            "position": [10, 10],
            "map_id": 1011,
            "current_hp": 100,
            "max_hp": 100,
        },
        target={"pid": 123, "created_at": 123.0, "path": "fixture"},
        manual=False,
        clicks=[],
        calls=[],
        events=[],
        post_error=None,
    )

    @coordination.coordinated_input
    def click(body):
        state.clicks.append(body)
        if state.post_error:
            raise state.post_error
        return {"issued": True}

    def dispatch(operation, body):
        state.calls.append(operation)
        if operation == "health":
            return {
                "target": deepcopy(state.target),
                "window": {
                    "client_size": [1036, 793],
                    "foreground": 1,
                    "root_hwnd": 1,
                    "hwnd": 1,
                    "minimized": False,
                },
            }
        assert operation == "foreground-click"
        return click(body)

    def snapshot():
        return {
            "control": deepcopy(state.control),
            "life": deepcopy(state.life),
            "observed_at": time.time(),
        }

    monkeypatch.setattr("conquest.mouse_priority.active", lambda: state.manual)
    service = EmbeddedBridge(
        NS(
            session=NS(pid=123),
            dispatch=dispatch,
            target=NS(snapshot=lambda: {"root_hwnd": 1, "hwnd": 1}),
        ),
        threading.RLock(),
        tmp_path / "bridge.json",
        snapshot,
    )
    service.native_probe_mode = True
    care = object.__new__(TravelCare)
    care.info = service.info_path
    care.session = observer.adapter
    care.layout = None
    care.exact_1078 = False
    care.revive_state = {}
    care.pending = None
    care.last_heal = -float("inf")
    care.next_panel_check = float("inf")
    care.notify = state.events.append
    care._xp_skill = xp_skill.XpSkill(
        observer, lambda event, fields: state.events.append({"event": event, **fields})
    )
    monkeypatch.setattr(
        travel_care, "resolve_player", lambda *_: {"name": 100, "max_hp": 200}
    )
    state.owner, state.care, state.service = owner, care, service
    state.popup, state.blobs = popup, blobs
    state.health = lambda: worker.request(service.info_path, "health")
    try:
        yield state
    finally:
        service.close()


def busy_pass(rig):
    with held_by_peer(rig.owner):
        with pytest.raises(TravelStateChanged, match="XP input busy") as error:
            rig.care.check(rig.health())
        assert type(error.value.__cause__) is InputAcquisitionBusy
    assert rig.clicks == []
    assert rig.care._xp_skill.attempts == 0
    assert rig.care._xp_skill.pending is None
    assert not any(row["event"] == "xp_fly_attempt" for row in rig.events)


def test_real_mutex_http_xp_denial_exits_care_then_requalifies_changed_popup(rig):
    busy_pass(rig)
    assert rig.calls.count("foreground-click") == 1
    rig.popup.position = (450.0, 610.0)
    with pytest.raises(TravelStateChanged, match="XP full"):
        rig.care.check(rig.health())
    assert len(rig.clicks) == 1 and rig.clicks[0]["point"] == [478, 638]
    assert rig.care._xp_skill.attempts == 1
    assert rig.care._xp_skill.pending is not None


@pytest.mark.parametrize(
    "mutation",
    ["not_ready", "flying", "popup", "dead", "manual", "enabled", "stop", "fence"],
)
def test_busy_then_changed_authority_never_activates_old_xp_point(rig, mutation):
    busy_pass(rig)
    if mutation == "not_ready":
        rig.blobs[XP_CHARGE] = struct.pack("<I", 99)
    elif mutation == "flying":
        rig.blobs[XP_STATUS] = struct.pack("<Q", 0x8000010)
    elif mutation == "popup":
        rig.popup.size = (112.0, 56.0)
    elif mutation == "dead":
        rig.life.update(dead_candidate=True, revive_ready_candidate=False)
    elif mutation == "manual":
        rig.manual = True
    elif mutation == "enabled":
        rig.control["enabled"] = True
    elif mutation == "stop":
        rig.owner.stop()
    else:
        rig.owner.manual_sessions["Farmer"] = {"holds_automation": True}
    if mutation in ("not_ready", "flying", "popup"):
        rig.care.check(rig.health())
    else:
        with pytest.raises(ValueError) as error:
            rig.care.check(rig.health())
        assert not isinstance(error.value.__cause__, InputAcquisitionBusy)
    assert rig.clicks == [] and rig.care._xp_skill.attempts == 0


@pytest.mark.parametrize("failure", ["same_text", "post_input", "os_lease"])
def test_generic_and_postinput_failures_do_not_become_retryable_xp_passes(
    rig, monkeypatch, failure
):
    if failure == "os_lease":
        import msvcrt

        def denied(*args):
            raise OSError("OS lease unavailable")

        monkeypatch.setattr(msvcrt, "locking", denied)
    else:
        rig.post_error = CaptureUnavailable(
            "Waiting for the current input action"
            if failure == "same_text"
            else "SendInput(mouse-down) failed"
        )
    with pytest.raises(ValueError) as error:
        rig.care.check(rig.health())
    assert type(error.value) is ValueError
    assert not isinstance(error.value, TravelStateChanged)
    assert rig.calls.count("foreground-click") == 1
    assert len(rig.clicks) == (0 if failure == "os_lease" else 1)


def route_loop(rig, monkeypatch):
    """Use actual Overnight health/identity and outer travel retry boundaries."""
    from conquest import city_travel, scene_input
    from conquest.navigation import TerrainMap
    from conquest.routes import RouteLibrary

    loop = object.__new__(OvernightLoop)
    loop.info = rig.service.info_path
    loop.identity = deepcopy(rig.target)
    loop.state = {"updated_at": time.time()}
    loop.refresh = lambda: None
    loop.record = lambda *args, **kwargs: None
    loop.check_stop = lambda: None
    loop.focus = lambda health: True
    loop.route = RouteLibrary().load("turtledove")
    loop.terrain = TerrainMap(1011, 40, 40, np.zeros((40, 40), dtype=bool), "", (), ())
    loop.care = rig.care
    loop.stepper = NS(
        step_to=lambda *args, **kwargs: pytest.fail("Unexpected route dispatch")
    )
    monkeypatch.setattr(city_travel, "service_role", lambda *_: None)
    monkeypatch.setattr(scene_input, "memory_player_anchor", lambda *_: (518, 396))
    return loop


@pytest.mark.parametrize("mutation", ["stop", "process"])
def test_outer_route_checks_stop_and_process_before_retrying_xp(
    rig, monkeypatch, mutation
):
    loop = route_loop(rig, monkeypatch)
    original = rig.care.check
    with held_by_peer(rig.owner) as release:

        def check(health):
            try:
                original(health)
            except TravelStateChanged as error:
                assert isinstance(error.__cause__, InputAcquisitionBusy)
                release_peer(release, rig.owner)
                if mutation == "stop":

                    def stopped():
                        raise OvernightStopped("Stopped by user")

                    loop.check_stop = stopped
                else:
                    rig.target["created_at"] += 1
                raise

        rig.care.check = check
        with pytest.raises(OvernightStopped):
            loop._travel((20, 10))
    assert rig.clicks == [] and rig.calls.count("foreground-click") == 1


def test_outer_route_replans_fresh_position_and_terrain_after_busy(rig, monkeypatch):
    loop = route_loop(rig, monkeypatch)
    original = rig.care.check
    plans, steps = [], []
    planner = loop.terrain.travel_path

    def path(source, destination, **kwargs):
        plans.append(source)
        return planner(source, destination, **kwargs)

    monkeypatch.setattr(loop.terrain, "travel_path", path)
    with held_by_peer(rig.owner) as release:

        def check(health):
            try:
                original(health)
            except TravelStateChanged as error:
                assert isinstance(error.__cause__, InputAcquisitionBusy)
                release_peer(release, rig.owner)
                rig.life["position"] = [11, 11]
                rig.blobs[XP_CHARGE] = struct.pack("<I", 99)
                rig.care.check = original
                raise

        rig.care.check = check

        def step(destination, expected_position):
            assert expected_position == (11, 11)
            steps.append((expected_position, destination))
            rig.life["position"] = list(destination)
            return {"reached": True}

        loop.stepper.step_to = step
        loop._travel((20, 10))
    assert plans == [(11, 11)] and len(steps) == 1 and rig.clicks == []


@pytest.mark.parametrize("changed", ["map", "position", "terrain"])
def test_new_route_request_rejects_stale_map_position_or_blocked_terrain(
    monkeypatch, changed
):
    from conquest import route_input
    from conquest.navigation import TerrainMap

    terrain = TerrainMap(1011, 40, 40, np.zeros((40, 40), dtype=bool), "", (), ())
    life = NS(map_id=1011, position=(10, 10), dead_candidate=False)
    sender = route_input.RouteJumpInput(
        NS(
            adapter=None,
            health_layout=None,
            character="Parasite",
            read_life=lambda: life,
        ),
        terrain,
    )
    sender.recovery_input.send = lambda *args: pytest.fail("Stale movement dispatched")
    if changed == "map":
        life.map_id = 1002
    elif changed == "position":
        life.position = (11, 10)
    else:
        terrain.blocked[10, 11] = True
    with pytest.raises(ValueError):
        sender(
            {
                "source": [10, 10],
                "destination": [20, 10],
                "map_id": 1011,
                "expires_at": time.time() + 3,
            }
        )


@pytest.mark.parametrize(
    "error_type", [InputAcquisitionBusy, CaptureUnavailable, ValueError]
)
def test_standalone_worker_http_preserves_only_exact_acquisition_type(
    tmp_path, monkeypatch, error_type
):
    info = tmp_path / "worker.json"
    ready = threading.Event()
    original_write = Path.write_text

    def write(path, text, *args, **kwargs):
        result = original_write(path, text, *args, **kwargs)
        if path == info:
            json.loads(text)  # Signal only after the complete connection is published.
            ready.set()
        return result

    operations = NS(stopping=False, target=NS(backend=NS(user=None)))

    def dispatch(operation, body):
        operations.stopping = True
        raise error_type("Waiting for the current input action")

    operations.dispatch = dispatch
    monkeypatch.setattr(Path, "write_text", write)
    monkeypatch.setattr(worker, "MemorySession", lambda *_: nullcontext(None))
    monkeypatch.setattr(worker, "Operations", lambda *args, **kwargs: operations)
    monkeypatch.setattr(worker, "bind", lambda *_: lambda vk: 0)
    thread = threading.Thread(target=worker.serve, args=(1, 2, "a" * 64, info))
    thread.start()
    try:
        assert ready.wait(5)
        with pytest.raises(error_type) as error:
            worker.request(info, "foreground-click", {})
        assert type(error.value) is error_type
    finally:
        operations.stopping = True
        thread.join(5)
        assert not thread.is_alive()
