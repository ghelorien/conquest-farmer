from contextlib import nullcontext
from copy import deepcopy
from types import SimpleNamespace as NS

import pytest

from conquest.merchants import open_booth_cancel_1078 as module


@pytest.fixture
def rig(monkeypatch, tmp_path):
    from conquest import desktop_runtime
    from conquest.merchants import coordination, memory, driver

    monkeypatch.setattr(module, "JOURNAL", tmp_path / "cancel.json")
    prompt = {
        "model_address": 100,
        "title": "Open Booth###Confirm",
        "message": "Start Vending",
        "positive_label": "Yes",
        "negative_label": "No",
        "callback_address": 200,
    }
    snapshot = {
        "identity": {"pid": 10, "creation_time_100ns": 20},
        "character": "Parasite",
        "character_uid": 30,
        "server": "America",
        "inventory": [{"uid": 40, "plus": 2}],
        "booth": [],
        "silver": 500,
        "capacity": 40,
        "own_booth_uid": 0,
        "booth_open": False,
        "map_id": 1036,
        "position": [229, 216],
        "confirmation": prompt,
        "trade": None,
        "request": None,
        "hp": 100,
        "timestamp": 1000,
    }
    state = {
        "snapshot": snapshot,
        "clicks": 0,
        "hovers": [],
        "allowed": True,
        "close": True,
        "fail": None,
    }
    gui = NS(
        model=lambda *a: 0x10000,
        session=NS(
            read_block=lambda *a: bytes(
                [int(state["snapshot"]["confirmation"] is not None)]
            )
        ),
        assert_hovered=lambda w, label: state["hovers"].append(label),
    )
    window = {"name": "Trade###Confirm", "address": 300}
    monkeypatch.setattr(memory.GuiReader, "for_session", lambda *a: gui)
    monkeypatch.setattr(
        module.control, "read", lambda gui: deepcopy(state["snapshot"]["confirmation"])
    )
    monkeypatch.setattr(
        module.control, "locate", lambda gui, snapshot: (window, (708, 460))
    )
    monkeypatch.setattr(module, "observe", lambda observer: deepcopy(state["snapshot"]))
    monkeypatch.setattr(module, "_clear", lambda: None)
    monkeypatch.setattr(coordination, "input_scope", lambda: nullcontext())
    monkeypatch.setattr(desktop_runtime, "physical_coordinates", lambda: nullcontext())
    monkeypatch.setattr(driver, "wait_hover_validation", lambda guard, check: guard())

    def check():
        if not state["allowed"]:
            raise ValueError("Stopped")

    def click(point, *, before_press, before_mouse_down):
        if state.get("before_guard"):
            state["before_guard"]()
        before_press()
        if state.get("before_down"):
            state["before_down"]()
        before_mouse_down()
        assert module._load()["phase"] == "submitted"
        state["clicks"] += 1
        if state["fail"]:
            raise ValueError(state["fail"])
        if state["close"]:
            state["snapshot"]["confirmation"] = None

    trade = NS(
        observer=NS(
            character="Parasite", adapter=NS(expected_sha256=module.CLIENT_SHA256_1078)
        ),
        check_input=check,
        click=click,
        input_attempted=False,
        warehouse_layout=lambda: (NS(assert_current=lambda r: None), "revision"),
        warehouse_native_point=lambda point, revision: point,
    )
    return trade, state


def test_native_no_has_durable_marker_and_verified_unchanged_receipt(rig):
    trade, state = rig
    result = module.cancel(trade)
    assert result["cancel_verified"] and result["outcome"] == "closed_unchanged"
    assert state["clicks"] == 1 and state["hovers"] == ["No", "No"]
    record = module._load()
    assert record["before"]["confirmation"]["title"] == "Open Booth###Confirm"
    assert record["after"]["confirmation"] is None
    assert module._owned(record["before"]) == module._owned(record["after"])
    assert module.cancel(trade) is None and state["clicks"] == 1


def test_lost_click_ack_never_replays_and_can_reconcile_closed(rig):
    trade, state = rig
    state["fail"] = "lost response"
    with pytest.raises(ValueError, match="lost response"):
        module.cancel(trade)
    assert module._load()["phase"] == "submitted"
    with pytest.raises(ValueError, match="no repeat input"):
        module.cancel(trade)
    assert state["clicks"] == 1
    state["snapshot"]["confirmation"] = None
    assert module.cancel(trade)["cancel_verified"]
    assert state["clicks"] == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("silver", 501),
        ("inventory", [{"uid": 41, "plus": 2}]),
        ("identity", {"pid": 11, "creation_time_100ns": 20}),
        ("booth_open", True),
        ("position", [230, 216]),
    ],
)
def test_submitted_changed_ownership_remains_held(rig, field, value):
    trade, state = rig
    state["fail"] = "lost response"
    with pytest.raises(ValueError):
        module.cancel(trade)
    state["snapshot"]["confirmation"] = None
    state["snapshot"][field] = value
    with pytest.raises(ValueError, match="ownership changed"):
        module.cancel(trade)
    assert state["clicks"] == 1 and module._load()["phase"] == "submitted"


def test_stop_at_mouse_down_has_no_marker_or_input(rig):
    trade, state = rig
    state["before_down"] = lambda: state.update(allowed=False)
    with pytest.raises(ValueError, match="Stopped"):
        module.cancel(trade)
    assert state["clicks"] == 0 and module._load()["phase"] == "prepared"
    state["allowed"] = True
    state.pop("before_down")
    assert module.cancel(trade)["cancel_verified"] and state["clicks"] == 1


def test_changed_prompt_at_input_boundary_is_never_dismissed(rig):
    trade, state = rig
    state["before_down"] = lambda: state["snapshot"]["confirmation"].update(
        title="Trade###Confirm"
    )
    with pytest.raises(ValueError, match="instance changed"):
        module.cancel(trade)
    assert state["clicks"] == 0 and module._load()["phase"] == "prepared"


def test_closed_fast_path_does_not_scan_assets_or_pin_code(rig, monkeypatch):
    trade, state = rig
    state["snapshot"]["confirmation"] = None
    monkeypatch.setattr(
        module, "observe", lambda o: pytest.fail("unnecessary full asset scan")
    )
    monkeypatch.setattr(
        module.control, "read", lambda g: pytest.fail("unnecessary code scan")
    )
    assert module.cancel(trade) is None and not module.JOURNAL.exists()


def test_unknown_modal_never_creates_cancel_marker(rig, monkeypatch):
    trade, state = rig

    def unknown(observer):
        raise ValueError("not the exact Open Booth prompt")

    monkeypatch.setattr(module, "observe", unknown)
    with pytest.raises(ValueError, match="not the exact"):
        module.cancel(trade)
    assert not module.JOURNAL.exists() and state["clicks"] == 0


def test_new_verified_closed_instance_has_separate_receipt(rig):
    trade, state = rig
    prompt = deepcopy(state["snapshot"]["confirmation"])
    first = module.cancel(trade)
    state["snapshot"]["confirmation"] = prompt
    second = module.cancel(trade)
    assert first["request_id"] != second["request_id"] and state["clicks"] == 2
    assert module._load()["history"][0]["request_id"] == first["request_id"]


def test_route_hook_dispatches_only_exact_build(monkeypatch):
    calls = []
    loop = NS(
        health=lambda: {"expected_sha256": module.CLIENT_SHA256_1078},
        town=lambda action: calls.append(action),
    )
    module.cleanup(loop)
    assert calls == ["cancel-open-booth-confirm"]
    loop.health = lambda: {"expected_sha256": "other"}
    module.cleanup(loop)
    assert len(calls) == 1


@pytest.mark.parametrize(
    "dead,map_id,position",
    [
        (False, 1036, [229, 216]),
        (True, 1036, [229, 216]),
        (False, 1011, [229, 216]),
        (False, 1036, [230, 216]),
    ],
)
def test_special_observation_retains_modal_and_checks_fresh_life(
    monkeypatch, dead, map_id, position
):
    from conquest.memory_life import MemoryLifeReader

    prompt = {"title": "Open Booth###Confirm", "message": "Start Vending"}
    snapshot = {
        "trade": None,
        "request": deepcopy(prompt),
        "map_id": 1036,
        "position": [229, 216],
        "hp": 100,
        "health": {"max_hp_candidate": 100},
        "timestamp": module.time.time(),
    }
    memory = NS(
        read_manual_ownership=lambda: deepcopy(snapshot),
        gui=object(),
        _actual_and_wrapper=lambda: (1, 2, 3),
    )
    monkeypatch.setattr(module, "_PromptMemory", lambda observer: memory)
    monkeypatch.setattr(module.control, "read", lambda gui: deepcopy(prompt))
    life = NS(
        dead_candidate=dead,
        map_id=map_id,
        position=position,
        current_hp=100,
        max_hp=100,
        object_address=2,
    )
    monkeypatch.setattr(
        MemoryLifeReader, "for_session", lambda *a: NS(read=lambda: life)
    )
    observer = NS(character="Parasite", adapter=object())
    if dead or map_id != 1036 or position != snapshot["position"]:
        with pytest.raises(ValueError, match="living Market"):
            module.observe(observer)
    else:
        result = module.observe(observer)
        assert result["confirmation"] == prompt and result["request"] is None
        assert result["canonical_manual_ownership"] is False


@pytest.mark.parametrize(
    "hold", ["manual", "delivery", "route", "protected", "probe", "stop"]
)
def test_manual_and_transaction_holds_are_not_cleanup_authority(monkeypatch, hold):
    from conquest.merchants import (
        coordination,
        delivery_operation,
        delivery_route,
        delivery_probe,
    )

    def blocked():
        raise ValueError("held")

    monkeypatch.setattr(
        coordination, "check_input", blocked if hold == "stop" else lambda: None
    )
    monkeypatch.setattr(
        coordination, "manual_session_blocked", lambda c: hold == "manual"
    )
    monkeypatch.setattr(
        delivery_operation,
        "guard_protected_assets",
        blocked if hold == "protected" else lambda: None,
    )
    monkeypatch.setattr(delivery_operation, "pending", lambda: hold == "delivery")
    monkeypatch.setattr(delivery_route, "pending", lambda: hold == "route")
    monkeypatch.setattr(
        delivery_probe,
        "read_probe",
        lambda **k: {"phase": "prepared"} if hold == "probe" else None,
    )
    with pytest.raises(ValueError):
        module._clear()
