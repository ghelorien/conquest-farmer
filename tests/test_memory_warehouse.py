from types import SimpleNamespace as NS
from dataclasses import replace
import struct
import pytest
from conquest.memory_inventory import Item
from conquest.memory_build_layout import CLIENT_SHA256_1078, READ_LAYOUTS
from conquest.memory_life import CLIENT_SHA256
from conquest.memory_warehouse import (
    MemoryWarehouseReader,
    WarehouseSnapshot,
    deposit_received,
)

LAYOUT = READ_LAYOUTS[CLIENT_SHA256_1078]
BASE = 0x140000000


def put_model_registry(put):
    """One-node GUI registry holding the active 1078 warehouse model."""
    head, node, model = 0x400000, 0x400100, 0x400200
    put(BASE + LAYOUT.gui_registry_rva, struct.pack("<QQ", head, 1))
    put(head + 8, struct.pack("<Q", node))
    raw = bytearray(56)
    struct.pack_into("<I", raw, 32, LAYOUT.warehouse_model_key)
    struct.pack_into("<Q", raw, 40, model)
    put(node, bytes(raw))
    put(
        model,
        struct.pack("<Q", BASE + LAYOUT.warehouse_model_vtable_rva) + bytes(4) + b"\1",
    )


def test_deposit_receipt_requires_exact_item_in_warehouse_without_other_losses():
    meteor = Item(42, 1088001, 1, 1, 3, 0)
    other = Item(43, 1088000, 1, 1, 4, 0)
    before = NS(items=(meteor, other), silver=100)
    stash = WarehouseSnapshot((), 20, 500)
    after = NS(items=(replace(other, slot=0),), silver=100)
    receipt = WarehouseSnapshot((replace(meteor, slot=0),), 20, 500)
    assert deposit_received(meteor, before, stash, after, receipt)
    assert not deposit_received(meteor, before, stash, after, stash)
    assert not deposit_received(
        meteor, before, stash, NS(items=(), silver=100), receipt
    )
    assert not deposit_received(
        meteor, before, stash, NS(items=after.items, silver=99), receipt
    )
    assert not deposit_received(
        meteor,
        before,
        stash,
        after,
        WarehouseSnapshot((replace(meteor, uid=99),), 20, 500),
    )
    # Bank silver is part of the conserved warehouse state.
    assert not deposit_received(
        meteor, before, stash, after, replace(receipt, bank_silver=499)
    )


def reader_fixture(monkeypatch, count=1):
    from conquest import memory_warehouse as module

    base = BASE
    shared = 0x500000
    owner = 0x600000
    table = 0x700000
    entry = 0x800000
    item = 0x900000
    data = {
        base + LAYOUT.warehouse_root_rva: struct.pack("<Q", shared),
        shared: struct.pack("<Q", owner),
        owner + LAYOUT.warehouse_deque_offset: struct.pack("<4Q", table, 8, 0, count),
        owner + LAYOUT.warehouse_capacity_offset: struct.pack("<I", 20),
        owner + LAYOUT.warehouse_silver_offset: struct.pack("<I", 500),
        table: struct.pack("<Q", entry),
        entry: struct.pack("<Q", item),
        item: struct.pack("<Q", base + LAYOUT.item_vtable_rva),
        item + 8: struct.pack("<I", 42),
        item + 0x10: struct.pack("<I", 1088001),
        item + 0x62: struct.pack("<HH", 1, 1),
        item + 0x6B: b"\0",
    }
    put_model_registry(data.__setitem__)
    s = NS(
        read_block=lambda address, size: data[address],
        assert_identity=lambda: None,
        expected_sha256=CLIENT_SHA256_1078,
    )
    monkeypatch.setattr(
        module,
        "MemoryGui",
        lambda session, layout=None: NS(base=base, read=lambda name: "active"),
    )
    return MemoryWarehouseReader(s), data, owner


def test_warehouse_reads_exact_uid_and_type(monkeypatch):
    reader, data, owner = reader_fixture(monkeypatch)
    assert reader.read() == WarehouseSnapshot((Item(42, 1088001, 1, 1, 0, 0),), 20, 500)


def test_warehouse_reader_fails_closed_off_1078(monkeypatch):
    reader, data, owner = reader_fixture(monkeypatch)
    # Any non-1078 build (the retired 1074 build has no read layout at all).
    reader.session.expected_sha256 = CLIENT_SHA256
    with pytest.raises(ValueError, match="No qualified read layout"):
        MemoryWarehouseReader(reader.session)


def test_warehouse_requires_active_1078_model(monkeypatch):
    reader, data, owner = reader_fixture(monkeypatch)
    data[0x400200] = data[0x400200][:12] + b"\0"
    with pytest.raises(ValueError, match="model is not active"):
        reader.read()


def test_warehouse_rejects_invalid_count(monkeypatch):
    reader, data, owner = reader_fixture(monkeypatch, 21)
    with pytest.raises(ValueError, match="deque"):
        reader.read()


def test_warehouse_rejects_changed_topology(monkeypatch):
    reader, data, owner = reader_fixture(monkeypatch)
    old = reader.session.read_block
    calls = []

    def read(address, size):
        if address == owner + LAYOUT.warehouse_deque_offset:
            calls.append(1)
            if len(calls) > 1:
                return bytes(32)
        return old(address, size)

    reader.session.read_block = read
    with pytest.raises(ValueError, match="changed"):
        reader.read()


def test_rich_warehouse_fences_first_row_gems_while_later_row_is_decoded(monkeypatch):
    from conquest import memory_warehouse as module

    base = BASE
    shared = 0x500000
    owner = 0x600000
    table = 0x700000
    entries = (0x800000, 0x800100)
    items = (0x900000, 0x900100)
    memory = {}

    def put(address, data):
        for offset, value in enumerate(data):
            memory[address + offset] = value

    put_model_registry(put)
    put(base + LAYOUT.warehouse_root_rva, struct.pack("<Q", shared))
    put(shared, struct.pack("<Q", owner))
    put(owner + LAYOUT.warehouse_deque_offset, struct.pack("<4Q", table, 8, 0, 2))
    put(owner + LAYOUT.warehouse_capacity_offset, struct.pack("<I", 20))
    put(owner + LAYOUT.warehouse_silver_offset, struct.pack("<I", 500))
    put(table, struct.pack("<2Q", *entries) + bytes(48))
    for index, (entry, pointer) in enumerate(zip(entries, items), 1):
        put(entry, struct.pack("<Q", pointer))
        raw = bytearray(0xA0)
        struct.pack_into("<Q", raw, 0, base + LAYOUT.item_vtable_rva)
        struct.pack_into("<I", raw, 8, 40 + index)
        struct.pack_into("<I", raw, 0x10, 114643)
        struct.pack_into("<HH", raw, 0x62, 31, 40)
        raw[0x67] = raw[0x68] = 0
        raw[0x6B] = 1
        put(pointer, raw)
    session = NS(
        read_block=lambda address, size: bytes(
            memory[address + i] for i in range(size)
        ),
        assert_identity=lambda: None,
        expected_sha256=CLIENT_SHA256_1078,
    )
    monkeypatch.setattr(
        module,
        "MemoryGui",
        lambda session, layout=None: NS(base=base, read=lambda name: "active"),
    )

    def rich(pointer, slot):
        if slot == 1:
            memory[items[0] + 0x67] = 1
        return NS(
            uid=41 + slot,
            type_id=114643,
            plus=1,
            slot=slot,
            quantity=1,
            gem1=0,
            gem2=0,
            bound=False,
        )

    with pytest.raises(ValueError, match="changed"):
        MemoryWarehouseReader(session).read(rich_item=rich)


def test_town_warehouse_items_rich_is_an_explicit_read_only_opt_in(monkeypatch):
    from conquest import town_trade as module
    from conquest.merchants import delivery_bridge

    rich_item = object()
    calls = []
    reader = NS(
        read=lambda **options: calls.append(options) or WarehouseSnapshot((), 20, 500)
    )
    monkeypatch.setattr(module, "MemoryWarehouseReader", lambda adapter: reader)
    # The rich opt-in reads through delivery_bridge.source_memory, which binds
    # MerchantMemory at import; a non-1078 adapter selects that path.
    monkeypatch.setattr(
        delivery_bridge, "MerchantMemory", lambda observer: NS(item=rich_item)
    )
    trade = module.TownTrade.__new__(module.TownTrade)
    trade.observer = NS(adapter=NS(expected_sha256=CLIENT_SHA256))
    # A non-Market town map keeps the plain vendor(0) lookup.
    trade.life = lambda **kwargs: NS(map_id=1002)
    trade.vendor = lambda kind: 77
    assert trade({"action": "warehouse-items", "rich": True}) == {
        "items": (),
        "capacity": 20,
        "bank_silver": 500,
    }
    assert calls == [{"rich_item": rich_item}]


@pytest.mark.parametrize(
    "case", ["full", "unsupported", "ambiguous_receipt", "gui_race_ambiguous_receipt"]
)
def test_deposit_guards_and_ambiguous_receipt_never_repeat_drag(monkeypatch, case):
    from conquest import town_trade as module

    # Family 500 gear became urgent storage in 33b707e (URGENT_EQUIPMENT_FAMILIES),
    # so the unsupported case uses +0 gear from a family that is still refused.
    item = Item(42, 1088001 if case != "unsupported" else 410005, 1, 1, 0, 0)
    bag = NS(items=(item,), silver=100, equipped_ammo=None)
    stash = WarehouseSnapshot((), 0 if case == "full" else 20, 500)
    grid = NS(size=(407.0, 175.0), scroll=(0.0, 0.0), position=(577.0, 435.0))
    target = NS(size=(272.0, 326.0), scroll=(0.0, 0.0), position=(71.0, 195.0))
    reader = NS(read=lambda: stash, gui=NS(read=lambda name: target))
    monkeypatch.setattr(module, "MemoryWarehouseReader", lambda adapter: reader)
    trade = module.TownTrade.__new__(module.TownTrade)
    trade.observer = NS(adapter=None, operations=NS(target=None))
    trade.vendor = lambda kind: 123
    trade.inventory = NS(read=lambda: bag)
    trade.shop = NS(gui=NS(read=lambda name: grid))
    trade.life = lambda **kwargs: NS(map_id=1002)  # non-Market town
    layout = NS(assert_current=lambda revision: None)
    trade.warehouse_layout = lambda: (
        layout,
        NS(client_size=(1036, 793), gui_size=(1036, 793)),
    )
    drags = []

    def drag(*args, **kwargs):
        drags.append((args, kwargs))
        if case == "gui_race_ambiguous_receipt":
            from conquest.merchants.memory import GuiObservationChanged

            raise GuiObservationChanged("GUI registry changed")

    monkeypatch.setattr(module, "foreground_drag", drag)

    def verify(read, accept, failure, **kwargs):
        for _ in range(3):
            assert not accept(read())
        raise ValueError(failure)

    trade.verified_read = verify
    with pytest.raises(ValueError):
        trade({"action": "warehouse-deposit", "uid": 42})
    assert len(drags) == (1 if "ambiguous_receipt" in case else 0)


@pytest.mark.parametrize("gui_race", [False, True])
def test_deposit_drag_rechecks_control_layout_and_exact_state(monkeypatch, gui_race):
    from conquest import town_trade as module

    item = Item(42, 1088001, 1, 1, 0, 0)
    bag = NS(items=(item,), silver=100, equipped_ammo=None)
    stash = WarehouseSnapshot((), 20, 500)
    inventory_grid = NS(size=(407.0, 175.0), scroll=(0.0, 0.0), position=(577.0, 435.0))
    warehouse_grid = NS(size=(272.0, 326.0), scroll=(0.0, 0.0), position=(71.0, 195.0))
    reader = NS(read=lambda: stash, gui=NS(read=lambda name: warehouse_grid))
    monkeypatch.setattr(module, "MemoryWarehouseReader", lambda adapter: reader)
    trade = module.TownTrade.__new__(module.TownTrade)
    trade.observer = NS(adapter=None, operations=NS(target="target"))
    trade.vendor = lambda kind: 123
    trade.inventory = NS(read=lambda: bag)
    trade.shop = NS(gui=NS(read=lambda name: inventory_grid))
    trade.life = lambda **kwargs: NS(map_id=1002)  # non-Market town
    hovered = []
    trade.require_warehouse_hover = lambda window: hovered.append(window)
    checks = []
    trade.check_input = lambda: checks.append("control")
    layout = NS(assert_current=lambda revision: checks.append("layout"))
    revision = NS(client_size=(1416, 1016), gui_size=(1036, 793))
    trade.warehouse_layout = lambda: (layout, revision)
    endpoints = []

    def drag(*args, **kwargs):
        endpoints.append((args[1], args[2], args[3]))
        kwargs["layout_guard"]()
        kwargs["before_press"]()
        kwargs["layout_guard"]()
        kwargs["before_release"]()
        if gui_race:
            from conquest.merchants.memory import GuiObservationChanged

            raise GuiObservationChanged("GUI registry changed")

    monkeypatch.setattr(module, "foreground_drag", drag)

    def verified(read, accept, failure, **kwargs):
        after = NS(items=(), silver=100, equipped_ammo=None)
        stored = WarehouseSnapshot((item,), 20, 500)
        assert accept((after, stored))
        return after, stored

    trade.verified_read = verified
    assert trade({"action": "warehouse-deposit", "uid": 42})["verified_in_warehouse"]
    assert endpoints == [((816, 583), (283, 459), (1416, 1016))]
    assert hovered == [inventory_grid, warehouse_grid]
    assert checks == ["control", "layout"] * 6


def test_deposit_drag_refuses_release_after_warehouse_grid_moves(monkeypatch):
    from conquest import town_trade as module

    item = Item(42, 1088001, 1, 1, 0, 0)
    bag = NS(items=(item,), silver=100, equipped_ammo=None)
    stash = WarehouseSnapshot((), 20, 500)
    inventory_grid = NS(size=(407.0, 175.0), scroll=(0.0, 0.0), position=(577.0, 435.0))
    original = NS(size=(272.0, 326.0), scroll=(0.0, 0.0), position=(71.0, 195.0))
    moved = NS(size=(272.0, 326.0), scroll=(0.0, 0.0), position=(91.0, 195.0))
    reads = []

    def warehouse_grid(name):
        reads.append(name)
        return original if len(reads) < 4 else moved

    reader = NS(read=lambda: stash, gui=NS(read=warehouse_grid))
    monkeypatch.setattr(module, "MemoryWarehouseReader", lambda adapter: reader)
    trade = module.TownTrade.__new__(module.TownTrade)
    trade.observer = NS(adapter=None, operations=NS(target="target"))
    trade.vendor = lambda kind: 123
    trade.inventory = NS(read=lambda: bag)
    trade.shop = NS(gui=NS(read=lambda name: inventory_grid))
    trade.life = lambda **kwargs: NS(map_id=1002)  # non-Market town
    trade.require_warehouse_hover = lambda window: None
    layout = NS(assert_current=lambda revision: None)
    trade.warehouse_layout = lambda: (
        layout,
        NS(client_size=(1036, 793), gui_size=(1036, 793)),
    )

    def drag(*args, **kwargs):
        kwargs["before_press"]()
        kwargs["before_release"]()

    monkeypatch.setattr(module, "foreground_drag", drag)
    with pytest.raises(ValueError, match="moved during deposit drag"):
        trade({"action": "warehouse-deposit", "uid": 42})


@pytest.mark.parametrize("covered", ["source", "destination"])
def test_deposit_refuses_grid_covered_by_another_panel(monkeypatch, covered):
    from conquest import town_trade as module
    from conquest.merchants import driver
    from conquest.merchants.memory import HoverNotReady

    item = Item(42, 1088001, 1, 1, 0, 0)
    bag = NS(items=(item,), silver=100, equipped_ammo=None)
    stash = WarehouseSnapshot((), 20, 500)
    inventory_grid = NS(
        address=100, size=(407.0, 175.0), scroll=(0.0, 0.0), position=(577.0, 435.0)
    )
    warehouse_grid = NS(
        address=200, size=(272.0, 326.0), scroll=(0.0, 0.0), position=(71.0, 195.0)
    )
    reader = NS(read=lambda: stash, gui=NS(read=lambda name: warehouse_grid))
    monkeypatch.setattr(module, "MemoryWarehouseReader", lambda adapter: reader)
    monkeypatch.setattr(
        driver, "wait_hover_validation", lambda validate, check: validate()
    )
    trade = module.TownTrade.__new__(module.TownTrade)
    trade.observer = NS(adapter=None, operations=NS(target="target"))
    trade.vendor = lambda kind: 123
    trade.inventory = NS(read=lambda: bag)
    trade.shop = NS(gui=NS(read=lambda name: inventory_grid))
    trade.life = lambda **kwargs: NS(map_id=1002)  # non-Market town
    layout = NS(assert_current=lambda revision: None)
    trade.warehouse_layout = lambda: (
        layout,
        NS(client_size=(1036, 793), gui_size=(1036, 793)),
    )

    def require_hover(window):
        blocked = inventory_grid if covered == "source" else warehouse_grid
        if window is blocked:
            raise HoverNotReady(covered + " grid is covered")

    trade.require_warehouse_hover = require_hover
    pressed = []

    def drag(*args, **kwargs):
        kwargs["before_press"]()
        pressed.append(True)
        kwargs["before_release"]()

    monkeypatch.setattr(module, "foreground_drag", drag)
    # Only the pre-press source cover is the typed, bounded-retry error. A
    # covered destination while held is uncertain (WarehouseDropCovered).
    expected = (
        module.WarehouseHoverCovered
        if covered == "source"
        else module.WarehouseDropCovered
    )
    with pytest.raises(expected, match="covered"):
        trade({"action": "warehouse-deposit", "uid": 42})
    assert pressed == ([] if covered == "source" else [True])


def test_warehouse_points_scale_from_gui_to_current_native_client():
    from conquest.town_trade import TownTrade

    revision = NS(gui_size=(1036, 793), client_size=(1416, 1016))
    assert TownTrade.warehouse_native_point((597, 455), revision) == (816, 583)
    assert TownTrade.warehouse_native_point((207, 358), revision) == (283, 459)
    with pytest.raises(ValueError, match="outside"):
        TownTrade.warehouse_native_point((1036, 100), revision)
