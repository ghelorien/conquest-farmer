from dataclasses import replace
from types import SimpleNamespace as NS
import pytest
from conquest.discard_loot import (
    discard_candidate,
    ground_key,
    ignored_drop,
    removal_received,
)
from conquest.memory_inventory import Item
from conquest.memory_ground import GroundItem


@pytest.mark.parametrize(
    "type_id,plus,slot,expected",
    [
        (480003, 0, 0, True),
        (480003, 1, 0, False),
        (480003, None, 0, False),
        (480003, 0, None, False),
        (480007, 0, 0, False),
        (1088001, 0, 0, False),
        (1088000, 0, 0, False),
        (700001, 0, 0, False),
        (1000020, 0, 0, False),
        (1050000, 0, 0, False),
        (1000000, 0, 0, True),
        (1001000, 0, 0, True),
    ],
)
def test_discard_preserves_unknown_plus_equipped_rare_and_supplies(
    type_id, plus, slot, expected
):
    assert discard_candidate(Item(1, type_id, 1, 1, slot, plus)) is expected


def test_ignore_is_specific_to_ground_generation_map_type_and_tile():
    drop = GroundItem(42, 123, 480003, (600, 550), 100)
    rows = [{"ground_key": ground_key(drop, 1002)}]
    assert ignored_drop(drop, 1002, rows)
    assert ignored_drop(replace(drop, object_address=999), 1002, rows)
    assert not ignored_drop(replace(drop, uid=43), 1002, rows)
    assert not ignored_drop(replace(drop, spawn_tick=101), 1002, rows)
    assert not ignored_drop(replace(drop, type_id=1088001), 1002, rows)
    assert not ignored_drop(drop, 1015, rows)


def test_discard_receipt_requires_exact_removal_and_no_other_losses():
    item = Item(1, 480003, 1, 1, 0, 0)
    keep = Item(2, 1088001, 1, 1, 1, 0)
    before = NS(items=(item, keep), silver=100)
    assert removal_received(
        item, before, NS(items=(replace(keep, slot=0),), silver=100)
    )
    assert not removal_received(item, before, NS(items=(), silver=100))
    assert not removal_received(item, before, before)
    assert not removal_received(item, before, NS(items=(keep,), silver=101))


def test_persisted_discard_attempt_is_never_repeated(monkeypatch, tmp_path):
    from conquest import discard_loot as module
    from conquest.discord_notify import write_json

    path = tmp_path / "journal.json"
    write_json(path, [{"uid": 1, "state": "attempted"}])
    monkeypatch.setattr(module, "JOURNAL", path)
    monkeypatch.setattr(module, "MemoryGroundReader", lambda entities: None)
    trade = NS(
        observer=NS(entities=None),
        inventory=NS(read=lambda: NS(items=(Item(1, 480003, 1, 1, 0, 0),))),
    )
    discarder = module.DiscardLoot(trade)
    with pytest.raises(ValueError, match="already attempted"):
        discarder.discard(1)


def test_inventory_button_uses_current_table_column_and_rejects_stale_frame():
    import struct
    from conquest.discard_loot import inventory_button

    base = 0x140000000
    context = 0x100000
    table = 0x200000
    columns = 0x300000
    record = bytearray(536)
    column = bytearray(104)
    struct.pack_into("<II", record, 0, 0x02A99238, 0x482010)
    struct.pack_into("<Q", record, 0x18, columns)
    struct.pack_into("<II", record, 0x70, 100, 6)
    struct.pack_into("<4f", record, 0xF0, 541, 743, 975, 783)
    struct.pack_into("<2f", column, 0x34, 620, 691)
    data = {
        base + 0x6966F0: struct.pack("<Q", context),
        context + 0x4338: struct.pack("<IIQ", 1, 1, table),
        context + 0x3E38: struct.pack("<I", 100),
    }

    def read(address, size):
        if address == table:
            return bytes(record)
        if address == columns + 104:
            return bytes(column)
        if address == columns + 104 + 0x34:
            return bytes(column[0x34:0x3C])
        return data[address]

    gui = NS(
        base=base,
        session=NS(read_block=read, assert_identity=lambda: None),
        read=lambda name: NS(position=(53, 691), size=(930, 102)),
    )
    assert inventory_button(gui) == (656, 753)
    data[context + 0x3E38] = struct.pack("<I", 104)
    with pytest.raises(ValueError, match="not current"):
        inventory_button(gui)


def test_native_looter_ignores_discarded_record_but_accepts_a_new_drop():
    from contextlib import nullcontext
    from conquest.native_farm import NativeFarmSupervisor

    drop = GroundItem(42, 123, 480003, (600, 550), 100)
    newer = replace(drop, uid=43, object_address=124, spawn_tick=101, plus=1)
    supervisor = NativeFarmSupervisor.__new__(NativeFarmSupervisor)
    supervisor.observer = NS(lock=nullcontext())
    supervisor.player_anchor = lambda position: (518, 396)
    supervisor.ownership_guard = lambda: None
    supervisor.pending_loot = None
    supervisor.loot_wait_until = 0
    supervisor.loot_cooldowns = {}
    supervisor.last_loot_error = None
    supervisor.discarder = NS(records=[{"ground_key": ground_key(drop, 1002)}])
    supervisor.map_id = 1002
    supervisor.ground_items = lambda: (drop,)
    supervisor.notify = lambda *args: None
    inventory = NS(items=(), capacity=40)
    clicks = []
    dispatch = lambda point, **kwargs: clicks.append(kwargs["drop"])
    assert not supervisor.loot_step(inventory, (600, 550), dispatch)
    assert not clicks
    supervisor.ground_items = lambda: (drop, newer)
    assert supervisor.loot_step(inventory, (600, 550), dispatch)
    assert clicks == [newer]


def test_native_discard_uses_logical_coordinates_and_serialized_dispatch(monkeypatch):
    from contextlib import contextmanager
    from conquest import native_farm

    inside = []
    calls = []

    @contextmanager
    def logical():
        inside.append(True)
        try:
            yield
        finally:
            inside.pop()

    monkeypatch.setattr(native_farm, "logical_coordinates", logical)
    supervisor = native_farm.NativeFarmSupervisor.__new__(
        native_farm.NativeFarmSupervisor
    )
    supervisor.pending_loot = None
    supervisor.defending = False
    supervisor.targets_observation_available = True
    supervisor.scene_timestamp = __import__("time").monotonic()
    supervisor.position = (100, 100)
    supervisor.scene_monsters = ()

    def discard(uid):
        assert inside
        calls.append(uid)
        return {"uid": uid, "state": "verified"}

    supervisor.discarder = NS(attempted=set(), discard=discard)
    supervisor.notify = lambda *args: None
    supervisor.dispatch = lambda callback: callback()
    assert supervisor.discard_step(NS(items=(Item(1, 480003, 1, 1, 0, 0),)))
    assert calls == [1] and not inside


def test_uncertain_drag_is_quarantined_panel_closed_and_farming_can_continue(
    monkeypatch, tmp_path
):
    from conquest import discard_loot as module

    monkeypatch.setattr(module, "JOURNAL", tmp_path / "journal.json")
    discarder = module.DiscardLoot.__new__(module.DiscardLoot)
    discarder.records = []
    discarder.attempted = set()
    discarder.cleanup_pending = False
    calls = []

    def attempt(uid):
        calls.append(uid)
        discarder.records.append({"uid": uid, "state": "attempted"})
        discarder.attempted.add(uid)
        discarder.cleanup_pending = True
        raise ValueError("Discard not verified in bag and ground memory")

    def close():
        discarder.cleanup_pending = False

    discarder._discard = attempt
    discarder.close_inventory = close
    result = discarder.discard(42)
    assert result["state"] == "unverified" and not discarder.cleanup_pending
    with pytest.raises(ValueError, match="already attempted"):
        discarder.discard(42)
    assert calls == [42]
    from conquest.discord_notify import read_json

    assert read_json(module.JOURNAL)[0]["state"] == "unverified"


def test_cleanup_remains_pending_if_character_dies_during_uncertain_drag(
    monkeypatch, tmp_path
):
    from conquest import discard_loot as module
    from conquest.capture import CaptureUnavailable

    monkeypatch.setattr(module, "JOURNAL", tmp_path / "journal.json")
    discarder = module.DiscardLoot.__new__(module.DiscardLoot)
    discarder.records = []
    discarder.attempted = set()
    discarder.cleanup_pending = True

    def attempt(uid):
        discarder.records.append({"uid": uid, "state": "attempted"})
        discarder.attempted.add(uid)
        raise ValueError("No receipt")

    def close():
        raise CaptureUnavailable("Character died")

    discarder._discard = attempt
    discarder.close_inventory = close
    assert discarder.discard(42)["state"] == "unverified"
    assert discarder.cleanup_pending


def test_pending_panel_cleanup_runs_before_new_discard_without_inventory_candidates(
    monkeypatch,
):
    from contextlib import nullcontext
    from conquest import native_farm

    monkeypatch.setattr(native_farm, "logical_coordinates", nullcontext)
    supervisor = native_farm.NativeFarmSupervisor.__new__(
        native_farm.NativeFarmSupervisor
    )
    calls = []
    supervisor.discarder = NS(
        cleanup_pending=True, close_inventory=lambda: calls.append("close")
    )
    supervisor.dispatch = lambda callback: callback()
    assert supervisor.discard_step(NS(items=()))
    assert calls == ["close"]


@pytest.mark.parametrize("failure", [ValueError, OSError])
def test_pre_drag_reader_failure_defers_without_marking_item_attempted(failure):
    import time
    from conquest import discard_loot as module

    discarder = module.DiscardLoot.__new__(module.DiscardLoot)
    discarder.records = []
    discarder.attempted = set()
    discarder.cleanup_pending = True

    def attempt(uid):
        raise failure("Ground observation unavailable before discard; no drag sent")

    def close():
        discarder.cleanup_pending = False

    discarder._discard = attempt
    discarder.close_inventory = close
    result = discarder.discard(42)
    assert result["state"] == "deferred" and result["retry_after"] == 10
    assert discarder.next_attempt_at > time.monotonic() + 9
    assert (
        not discarder.records
        and not discarder.attempted
        and not discarder.cleanup_pending
    )


def test_pre_drag_capture_failure_defers():
    from conquest.capture import CaptureUnavailable

    test_pre_drag_reader_failure_defers_without_marking_item_attempted(
        CaptureUnavailable
    )


def test_discard_cooldown_leaves_combat_available():
    import time
    from conquest.native_farm import NativeFarmSupervisor

    supervisor = NativeFarmSupervisor.__new__(NativeFarmSupervisor)
    supervisor.pending_loot = None
    supervisor.defending = False
    supervisor.discarder = NS(
        cleanup_pending=False, next_attempt_at=time.monotonic() + 10
    )
    assert not supervisor.discard_step(NS(items=(Item(1, 480003, 1, 1, 0, 0),)))


@pytest.mark.parametrize("failure", [ValueError, OSError])
def test_pending_panel_reader_failure_retries_instead_of_terminating(
    monkeypatch, failure
):
    from contextlib import nullcontext
    from conquest import native_farm
    from conquest.capture import CaptureUnavailable

    monkeypatch.setattr(native_farm, "logical_coordinates", nullcontext)
    supervisor = native_farm.NativeFarmSupervisor.__new__(
        native_farm.NativeFarmSupervisor
    )

    def close():
        raise failure("GUI snapshot changed")

    supervisor.discarder = NS(cleanup_pending=True, close_inventory=close)
    supervisor.dispatch = lambda callback: callback()
    with pytest.raises(CaptureUnavailable, match="Waiting to close Inventory"):
        supervisor.discard_step(NS(items=()))
    assert supervisor.discard_panel_pending


def test_ground_failure_does_not_open_inventory():
    from conquest.discard_loot import DiscardLoot
    from conquest.capture import CaptureUnavailable

    seen = []

    def gui(name):
        seen.append(name)
        raise ValueError("not active")

    def ground():
        raise ValueError("Ground scene changed during sampling")

    trade = NS(
        inventory=NS(read=lambda: NS(items=(Item(1, 480003, 1, 1, 0, 0),))),
        life=lambda minimum: NS(position=(100, 100), map_id=1002),
        shop=NS(gui=NS(read=gui)),
        verified_read=lambda reader, *args, **kwargs: reader(),
        click=lambda point: pytest.fail("Inventory must stay closed"),
    )
    d = DiscardLoot.__new__(DiscardLoot)
    d.trade = trade
    d.attempted = set()
    d.cleanup_pending = False
    d.ground = NS(read=ground)
    with pytest.raises(ValueError, match="Ground scene"):
        d._discard(1)
    assert seen == ["Shop", "Warehouse"] and not d.cleanup_pending


@pytest.mark.parametrize(
    "scene,available,age",
    [
        ([NS(position=(112, 100), alive=True, current_hp=10)], True, 0),
        ([NS(position=(101, 100), alive=None, current_hp=None)], True, 0),
        ([], False, 0),
        ([], True, 2),
        ([], True, -2),
    ],
)
def test_optional_cleanup_never_opens_bag_in_combat_or_unknown_scene(
    scene, available, age
):
    import time
    from conquest.native_farm import NativeFarmSupervisor

    s = NativeFarmSupervisor.__new__(NativeFarmSupervisor)
    s.pending_loot = None
    s.defending = False
    s.discarder = None
    s.targets_observation_available = available
    s.scene_timestamp = time.monotonic() - age
    s.position = (100, 100)
    s.scene_monsters = scene
    s.dispatch = lambda callback: pytest.fail("Optional cleanup interrupted combat")
    assert not s.discard_step(NS(items=(Item(1, 480003, 1, 1, 0, 0),)))


def test_repeated_cleanup_failures_back_off_globally_and_success_resets():
    from conquest.discard_loot import DiscardLoot

    d = DiscardLoot.__new__(DiscardLoot)
    d.records = []
    d.attempted = set()
    d.cleanup_pending = False

    def fail(uid):
        raise ValueError("Geometry is not qualified")

    d._discard = fail
    assert [d.discard(uid)["retry_after"] for uid in range(8)] == [
        10,
        20,
        40,
        80,
        160,
        300,
        300,
        300,
    ]
    d._discard = lambda uid: {"uid": uid, "state": "verified"}
    assert d.discard(8)["state"] == "verified"
    d._discard = fail
    assert d.discard(9)["retry_after"] == 10
