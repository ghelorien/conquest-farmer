from dataclasses import replace
import json

import pytest

from conquest.control import FarmingControl
from conquest.control_runtime import ControlRuntime
from conquest.kill_loot import ConfirmedKill, GroundLoot, LootObservation
from conquest.memory_inventory import InventorySnapshot, Item


def farmer():
    control = FarmingControl()
    control.update({"target_type_ids": [1], "enabled": True})
    calls = []
    inventory = InventorySnapshot(10, 10, (), None, 0, 40)
    data = {
        "monsters": [
            {"entity_id": 77, "type_id": 1, "object_address": 177, "alive": True},
            {"entity_id": 88, "type_id": 1, "object_address": 188, "alive": True},
        ],
        "observations_available": True,
        "focused": False,
        "loot_observation": LootObservation("client/map", 10, (), (), inventory),
    }
    now = [10]
    runtime = ControlRuntime(
        control,
        None,
        None,
        None,
        "Parasite",
        observer=lambda: dict(data),
        dispatcher=lambda m, *_: calls.append(("attack", m["entity_id"])),
        pickup_dispatcher=lambda d, *_: calls.append(("pickup", d.uid)),
        clock=lambda: now[0],
    )

    def step(time, **changes):
        now[0] = time
        previous = data["loot_observation"]
        inventory = changes.pop("inventory", previous.inventory)
        inventory = replace(inventory, started_at=time, timestamp=time)
        data["loot_observation"] = replace(
            previous, timestamp=time, inventory=inventory, **changes
        )
        runtime.step()
        return runtime.snapshot()

    return runtime, control, calls, data, step


def kill():
    return ConfirmedKill("kill-77", 77, 177, 10.2)


def drop(uid=101, **changes):
    return GroundLoot(uid, 1000000, "kill-77", (300, 400), **changes)


def test_killed_id_loot_precedes_next_monster_and_confirms_stack_gain():
    runtime, control, calls, data, step = farmer()
    old_stack = Item(1, 1000000, 5, 10, 0)
    step(10, inventory=replace(data["loot_observation"].inventory, items=(old_stack,)))
    data["monsters"].pop(0)
    # Other monsters stay visible, and unrelated loot at the same point is ignored.
    step(
        10.3,
        kills=(kill(),),
        drops=(drop(), replace(drop(102), kill_event_id="other-kill")),
    )
    assert calls == [("attack", 77), ("pickup", 101)]
    inventory = replace(
        data["loot_observation"].inventory, items=(replace(old_stack, amount=6),)
    )
    result = step(10.6, drops=(), inventory=inventory)
    assert result["encounter"]["collected_drop_ids"] == [101]
    # Inventory gain with an existing UID (stack merge) is accepted.
    for time in [11, 11.5, 12, 12.5, 13, 13.6]:
        step(time)
    step(13.8)
    assert calls == [("attack", 77), ("pickup", 101), ("attack", 88)]
    json.dumps(runtime.snapshot())


def test_delayed_multiple_drops_and_silver_all_finish_before_next_target():
    runtime, control, calls, data, step = farmer()
    step(10)
    data["monsters"].pop(0)
    step(10.3, kills=(kill(),))
    step(11)
    step(12, drops=(drop(),))
    item = Item(1, 1000000, 1, 10, 0)
    inventory = replace(data["loot_observation"].inventory, items=(item,))
    result = step(12.3, drops=(drop(102, silver=True, amount=5),), inventory=inventory)
    assert calls == [("attack", 77), ("pickup", 101), ("pickup", 102)]
    result = step(12.6, drops=(), inventory=replace(inventory, silver=5))
    assert result["encounter"]["collected_drop_ids"] == [101, 102]


@pytest.mark.parametrize(
    "evidence",
    [
        (),
        (replace(kill(), monster_id=88),),
        (replace(kill(), object_address=999),),
        (replace(kill(), timestamp=9),),
    ],
)
def test_disappearance_wrong_id_or_reused_object_is_not_our_kill(evidence):
    runtime, control, calls, data, step = farmer()
    step(10)
    data["monsters"].pop(0)
    step(10.3, kills=evidence, drops=(drop(),))
    step(11)
    assert calls == [("attack", 77)]
    assert runtime.snapshot()["encounter"]["kill_event_id"] is None


def test_kill_looting_continues_when_last_selected_monster_disappears():
    runtime, control, calls, data, step = farmer()
    control.update({"target_type_ids": [], "target_ids": [77]})
    step(10)
    data["monsters"] = []
    step(10.3, kills=(kill(),), drops=(drop(),))
    assert calls[-1] == ("pickup", 101)
    assert control.snapshot()["resolved_target_ids"] == []


@pytest.mark.parametrize("inventory_gain", [False, True])
def test_unconfirmed_pickup_holds_next_target(inventory_gain):
    runtime, control, calls, data, step = farmer()
    step(10)
    step(10.3, kills=(kill(),), drops=(drop(),))
    inventory = data["loot_observation"].inventory
    if inventory_gain:
        inventory = replace(inventory, items=(Item(1, 1000000, 1, 10, 0),))
    # Test vanished item without gain, and gain while item remains on the ground.
    remaining = (drop(),) if inventory_gain else ()
    step(12.4, drops=remaining, inventory=inventory)
    step(14.5, drops=remaining, inventory=inventory)
    assert calls.count(("pickup", 101)) <= 2
    assert runtime.encounter.state == "blocked"
    assert ("attack", 88) not in calls


def test_full_inventory_blocks_pickup_and_retargeting():
    runtime, control, calls, data, step = farmer()
    step(10)
    inventory = replace(
        data["loot_observation"].inventory, capacity=1, items=(Item(1, 55, 1, 1, 0),)
    )
    step(10.3, kills=(kill(),), drops=(drop(),), inventory=inventory)
    assert calls == [("attack", 77)] and runtime.encounter.state == "blocked"


def test_preexisting_drop_is_excluded_even_if_its_attribution_is_replayed():
    runtime, control, calls, data, step = farmer()
    step(10, drops=(drop(),))
    step(10.3, kills=(kill(),))
    assert calls == [("attack", 77)]


def test_switch_off_between_plan_and_pickup_dispatch_cancels_input(monkeypatch):
    runtime, control, calls, data, step = farmer()
    step(10)
    dispatch = control.dispatch

    def off_first(*args, **kwargs):
        control.update({"enabled": False})
        return dispatch(*args, **kwargs)

    monkeypatch.setattr(control, "dispatch", off_first)
    step(10.3, kills=(kill(),), drops=(drop(),))
    assert calls == [("attack", 77)] and not control.snapshot()["enabled"]
    assert runtime.encounter.pending is None


def test_uncertain_input_does_not_retry_or_retarget():
    runtime, control, calls, data, step = farmer()
    step(10)

    def failed(*_):
        raise TimeoutError("Input response lost")

    runtime.pickup_dispatcher = failed
    step(10.3, kills=(kill(),), drops=(drop(),))
    result = step(11)
    assert result["encounter"]["state"] == "blocked"
    assert calls == [("attack", 77)]


def test_missing_loot_reader_blocks_attack_even_with_attack_dispatcher():
    runtime, control, calls, data, step = farmer()
    data.pop("loot_observation")
    runtime.step()
    assert not calls
    assert (
        "Monster-to-loot observations are not connected" in control.snapshot()["note"]
    )


def test_map_change_preserves_unfinished_encounter_as_blocked():
    runtime, control, calls, data, step = farmer()
    step(10)
    step(10.3, kills=(kill(),), drops=(drop(),), context="other-map")
    assert runtime.encounter.state == "blocked" and calls == [("attack", 77)]
