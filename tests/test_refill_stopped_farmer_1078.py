import copy
import os
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from conquest.capture import CaptureUnavailable
from conquest.merchants import listing_handoff_1078 as handoff


@pytest.fixture
def stopped(monkeypatch):
    control = {"enabled": False, "paused": False, "revision": 6}
    health = {
        "profile_id": "farmer-profile",
        "target": {"pid": 123, "creation_time_100ns": 456},
        "embedded_controls": {
            "control": copy.deepcopy(control),
            "observed_at": 1000.0,
            "observations_available": True,
            "external_execution": False,
            "manual_input_fence": False,
            "manual_mouse": False,
            "monsters": [],
            "life": {
                "map_id": 1011,
                "position": [379, 468],
                "current_hp": 1400,
                "max_hp": 1490,
                "dead_candidate": False,
            },
        },
    }
    session = object()
    target = NS(hwnd=77)
    observer = NS(session=session, operations=NS(target=target))
    bridge = NS(
        read_only=False,
        operations=NS(session=session, target=target),
        info_path=Path(f"embedded-worker-{os.getpid()}.json"),
        health=lambda: copy.deepcopy(health),
    )
    observer.bridge = bridge
    ui = NS(
        grant=None,
        closed=False,
        safe_to_yield=lambda: True,
        app=NS(
            closing=False,
            control=NS(snapshot=lambda: copy.deepcopy(control)),
            observer=observer,
            attachment=NS(attached=True, ready=True),
        ),
        runtime=NS(
            stop_event=NS(is_set=lambda: False),
            manual_handoff_status=lambda: None,
            manual_target=lambda _: "farmer-profile",
        ),
        coordinator=NS(
            purpose="booth_listing_1078_once",
            owner="Spiritual",
            stopped=False,
            manual_active=lambda: False,
            manual_session_blocked=lambda _: False,
        ),
    )
    monkeypatch.setattr(handoff.time, "time", lambda: 1000.0)
    return ui, health, control


def test_off_farmer_outside_market_uses_full_native_parking_proof_without_mutation(
    stopped,
):
    ui, health, control = stopped
    before = copy.deepcopy((health, control))
    assert handoff.farmer_safe(ui) == health["target"]
    assert (health, control) == before and ui.grant is None


@pytest.mark.parametrize(
    "changed",
    (
        "threat",
        "low_hp",
        "manual_mouse",
        "external_execution",
        "on",
        "paused",
        "identity",
        "owner",
    ),
)
def test_off_refill_rejects_real_unsafe_or_changed_ownership(stopped, changed):
    ui, health, control = stopped
    data = health["embedded_controls"]
    expected = copy.deepcopy(health["target"])
    if changed == "threat":
        data["monsters"] = [{"position": [380, 468], "alive": None}]
    elif changed == "low_hp":
        data["life"]["current_hp"] = 100
    elif changed in ("manual_mouse", "external_execution"):
        data[changed] = True
    elif changed == "on":
        control["enabled"] = True
    elif changed == "paused":
        control["paused"] = True
    elif changed == "identity":
        health["target"]["creation_time_100ns"] = 789
    else:
        ui.safe_to_yield = lambda: False
    with pytest.raises(CaptureUnavailable):
        handoff.farmer_safe(ui, expected)


def test_new_grant_during_idle_observation_requires_a_new_bound_check(stopped):
    ui, health, control = stopped

    def read():
        ui.grant = {"request_id": "new-grant"}
        return copy.deepcopy(health)

    ui.app.observer.bridge.health = read
    with pytest.raises(CaptureUnavailable, match="ownership changed"):
        handoff.farmer_safe(ui)


def test_trade_does_not_inherit_idle_refill_permission(stopped, monkeypatch):
    from conquest.merchants import booth_probe_1078

    ui, _, _ = stopped
    ui.coordinator.purpose = "trade"

    def old(*args):
        raise CaptureUnavailable("original trade prerequisite")

    monkeypatch.setattr(booth_probe_1078, "_farmer_safe_market", old)
    with pytest.raises(CaptureUnavailable, match="original trade prerequisite"):
        handoff.farmer_safe(ui)
